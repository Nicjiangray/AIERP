#!/usr/bin/env python3
"""
测试 ``create_contract`` 工作流：跑通步骤 1～6 的引擎串联（第 5 步 mock LLM，第 6 步 dry-run）。

说明：
- 种子脚本里的 SQL 与当前库表字段可能不一致（如 ``code``/``role``/``is_active``），本脚本在测试前
  **临时** 将第 1～3、6 步的 ``sql_template`` / ``on_fail`` 改为与当前 ``business`` 表一致，
  结束后在 ``finally`` 中 **恢复**。
- 若库中无产品/供应商，会插入最小测试行再查询出 ``product_code`` / ``supplier_name``。

用法（项目根目录）::

    .venv/bin/python scripts/test_workflow.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import patch

import psycopg2
import psycopg2.extras

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from engine.workflow import run_workflow

WF_INTENT = "create_contract"


def _conn_kwargs() -> dict:
    return {
        "host": os.environ.get("PGHOST", "localhost"),
        "port": int(os.environ.get("PGPORT", "5432")),
        "dbname": os.environ.get("PGDATABASE", "aierp"),
        "user": os.environ.get("PGUSER", "nicjiang"),
    }


def _ensure_product_and_supplier(
    cur: psycopg2.extras.RealDictCursor,
) -> Tuple[str, str]:
    cur.execute("SELECT product_code FROM business.products LIMIT 1")
    pr = cur.fetchone()
    if pr:
        product_code = str(pr["product_code"])
    else:
        product_code = "WF-TEST-001"
        cur.execute(
            """
            INSERT INTO business.products (product_code, product_name)
            VALUES (%s, %s)
            """,
            (product_code, "工作流测试产品"),
        )
    cur.execute(
        "SELECT name FROM business.suppliers WHERE status = 'active' LIMIT 1"
    )
    sr = cur.fetchone()
    if sr:
        supplier_name = str(sr["name"])
    else:
        supplier_name = "工作流测试供应商"
        cur.execute(
            """
            INSERT INTO business.suppliers (supplier_code, name, currency, status)
            VALUES (%s, %s, 'CNY', 'active')
            """,
            ("WF-SUP-001", supplier_name),
        )
    return product_code, supplier_name


def _fetch_step_backup(
    cur: psycopg2.extras.RealDictCursor, orders: List[int]
) -> Dict[int, Dict[str, Any]]:
    cur.execute(
        """
        SELECT step_order, sql_template, on_fail
        FROM business.workflow_steps_legacy
        WHERE workflow_intent = %s AND step_order = ANY(%s)
        """,
        (WF_INTENT, orders),
    )
    return {int(r["step_order"]): dict(r) for r in cur.fetchall()}


def _apply_test_sql_patches(cur: psycopg2.extras.RealDictCursor) -> None:
    """与当前库表一致的 SQL；第 6 步改为只读 dry-run，避免写入 contracts。"""
    patches: List[Tuple[int, str, Optional[str]]] = [
        (
            1,
            "SELECT role_tags AS role FROM business.employees WHERE id = '{user_id}'",
            "stop",
        ),
        (
            2,
            "SELECT id, product_name FROM business.products WHERE product_code = '{product_code}'",
            "ask_user",
        ),
        (
            3,
            "SELECT id, name FROM business.suppliers WHERE name = '{supplier_name}' AND status = 'active'",
            "ask_user",
        ),
        (
            6,
            "SELECT 'dry_run'::text AS contract_id",
            "skip",
        ),
    ]
    for step_order, sql_t, on_fail in patches:
        cur.execute(
            """
            UPDATE business.workflow_steps_legacy
            SET sql_template = %s, on_fail = %s
            WHERE workflow_intent = %s AND step_order = %s
            """,
            (sql_t, on_fail, WF_INTENT, step_order),
        )


def _restore_step_backup(
    cur: psycopg2.extras.RealDictCursor, backup: Dict[int, Dict[str, Any]]
) -> None:
    for order, row in backup.items():
        cur.execute(
            """
            UPDATE business.workflow_steps_legacy
            SET sql_template = %s, on_fail = %s
            WHERE workflow_intent = %s AND step_order = %s
            """,
            (row["sql_template"], row["on_fail"], WF_INTENT, order),
        )


def _json_safe(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, indent=2, default=str)
    except TypeError:
        return repr(obj)


def _diff_context(prev: Dict[str, Any], cur: Dict[str, Any]) -> str:
    keys = sorted(set(prev) | set(cur))
    lines: List[str] = []
    for k in keys:
        a, b = prev.get(k), cur.get(k)
        if k not in prev:
            lines.append(f"  + {k}: {_json_safe(b)[:2000]}")
        elif k not in cur:
            lines.append(f"  - {k}: {_json_safe(a)[:2000]}")
        elif a != b:
            lines.append(f"  ~ {k}:")
            lines.append(f"      旧: {_json_safe(a)[:1200]}")
            lines.append(f"      新: {_json_safe(b)[:1200]}")
    if not lines:
        return "  (无键级变化)"
    return "\n".join(lines)


def main() -> None:
    # 与 print 同一流，避免终端上 stderr 的 INFO 跑到 stdout 的「初始 context」前面
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
        force=True,
    )
    orders_to_patch = [1, 2, 3, 6]
    backup: Dict[int, Dict[str, Any]] = {}

    conn = psycopg2.connect(**_conn_kwargs())
    try:
        conn.autocommit = False
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            backup = _fetch_step_backup(cur, orders_to_patch)
            if len(backup) != len(orders_to_patch):
                missing = set(orders_to_patch) - set(backup)
                raise RuntimeError(
                    f"workflow_steps_legacy 缺少步骤 {missing}，请先执行 scripts/seed_create_contract_workflow.py"
                )
            product_code, supplier_name = _ensure_product_and_supplier(cur)
            _apply_test_sql_patches(cur)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    context: Dict[str, Any] = {
        "user_id": "1",
        "product_code": product_code,
        "supplier_name": supplier_name,
    }

    print("=" * 72, flush=True)
    print("初始 context（仅业务入参）:", flush=True)
    print(_json_safe(context), flush=True)
    print("=" * 72, flush=True)

    prev_snapshot: Dict[str, Any] = {}

    def on_step_complete(ctx: Dict[str, Any]) -> None:
        nonlocal prev_snapshot
        print("\n--- 本步结束后的 context 变化 ---", flush=True)
        print(_diff_context(prev_snapshot, ctx), flush=True)
        prev_snapshot = dict(ctx)
        print("--- 当前 context 全量（节选） ---", flush=True)
        brief = {
            k: ctx[k]
            for k in ctx
            if not k.startswith("workflow_") or k in ("workflow_intent", "workflow_total_steps", "workflow_last_error")
        }
        print(_json_safe(brief), flush=True)
        print("-" * 72, flush=True)

    result: Dict[str, Any] = {}
    try:
        with patch("engine.workflow.chat", return_value="[]"):
            result = run_workflow(
                WF_INTENT,
                context,
                on_step_complete=on_step_complete,
            )
    finally:
        conn2 = psycopg2.connect(**_conn_kwargs())
        try:
            conn2.autocommit = False
            with conn2.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                _restore_step_backup(cur, backup)
            conn2.commit()
        except Exception:
            conn2.rollback()
            raise
        finally:
            conn2.close()

    print("\n" + "=" * 72, flush=True)
    print("run_workflow 最终返回（关键字段）:", flush=True)
    print(
        _json_safe(
            {
                k: result[k]
                for k in result
                if k
                in (
                    "user_id",
                    "product_code",
                    "supplier_name",
                    "user_role",
                    "product_id",
                    "supplier_id",
                    "file_data",
                    "missing_fields",
                    "contract_id",
                    "workflow_last_error",
                    "workflow_awaiting_user",
                    "workflow_user_message",
                    "workflow_error",
                )
            }
        ),
        flush=True,
    )
    print("=" * 72, flush=True)
    print("已恢复 workflow_steps_legacy 第 1/2/3/6 步的 sql_template 与 on_fail。", flush=True)


if __name__ == "__main__":
    main()
