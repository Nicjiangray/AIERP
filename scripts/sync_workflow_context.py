#!/usr/bin/env python3
"""
同步 business.workflow_steps.fields：调用 meta.sync_workflow_step_fields()，
必要时为某意图插入默认步骤行（与旧版 context_schema.steps 骨架一致）。

规则：
- 某意图在 workflow_steps 中无行时：按 intent + target_tables + related_tables 生成基础步骤并 INSERT；
- 调用存储过程填充/更新 fields（p_force=false 时仅覆盖仍为 [] 的行）；
- 非 --force 时对比「当前库 fields」与按 meta 计算的期望字段集，打印差异报告。

用法：
  python scripts/sync_workflow_context.py
  python scripts/sync_workflow_context.py --force

依赖：
  constitution/refactor_workflow_steps.sql（workflow_steps 表）
  constitution/sync_workflow_step_fields.sql（存储过程）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional, Set

import psycopg2.errors
import psycopg2.extras

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.database import db_conn  # noqa: E402

SYSTEM_FIELD_NAMES = frozenset(
    {
        "id",
        "created_at",
        "updated_at",
        "deleted_at",
    }
)


def _parse_related_tables_raw(raw: Any) -> List[Dict[str, Any]]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    if isinstance(raw, str) and raw.strip():
        try:
            j = json.loads(raw)
            return j if isinstance(j, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _related_table_names_for(source_table: str) -> List[str]:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT related_tables FROM meta.meta_tables
                WHERE table_name = %s LIMIT 1
                """,
                (source_table,),
            )
            row = cur.fetchone()
    if not row or row[0] is None:
        return []
    out: List[str] = []
    seen: Set[str] = set()
    for item in _parse_related_tables_raw(row[0]):
        t = (item.get("table") or "").strip()
        if not t or t == source_table or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def _collect_tables_for_workflow(
    intent: str, target_tables: Optional[List[str]]
) -> List[str]:
    seeds: List[str] = list(target_tables or [])
    if intent == "query_master_data":
        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT table_name FROM meta.meta_tables m
                    WHERE EXISTS (
                        SELECT 1 FROM information_schema.tables t
                        WHERE t.table_schema = 'business'
                          AND t.table_name = m.table_name
                          AND t.table_type = 'BASE TABLE'
                    )
                    ORDER BY table_name
                    """
                )
                seeds = [str(r[0]) for r in cur.fetchall() if r and r[0]]
    seen: Set[str] = set()
    ordered: List[str] = []
    for t in seeds:
        if not t or t in seen:
            continue
        seen.add(t)
        ordered.append(t)
    extra: List[str] = []
    for t in list(ordered):
        for rt in _related_table_names_for(t):
            if rt not in seen:
                seen.add(rt)
                extra.append(rt)
    return ordered + extra


def _has_column(conn, table: str, column: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'meta' AND table_name = %s AND column_name = %s
            """,
            (table, column),
        )
        return cur.fetchone() is not None


def _fetch_fields_for_table(conn, table_name: str) -> List[Dict[str, Any]]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        pk_col = "is_primary_key" if _has_column(conn, "meta_fields", "is_primary_key") else None
        if pk_col:
            cur.execute(
                f"""
                SELECT field_name, field_cn, data_type,
                       COALESCE(is_required, false) AS is_required,
                       allowed_values,
                       COALESCE({pk_col}, false) AS is_primary_key
                FROM meta.meta_fields
                WHERE table_name = %s
                ORDER BY id
                """,
                (table_name,),
            )
        else:
            cur.execute(
                """
                SELECT field_name, field_cn, data_type,
                       COALESCE(is_required, false) AS is_required,
                       allowed_values,
                       false AS is_primary_key
                FROM meta.meta_fields
                WHERE table_name = %s
                ORDER BY id
                """,
                (table_name,),
            )
        return [dict(r) for r in cur.fetchall()]


def _is_system_row(row: Dict[str, Any]) -> bool:
    fn = (row.get("field_name") or "").strip()
    if fn in SYSTEM_FIELD_NAMES:
        return True
    if row.get("is_primary_key"):
        return True
    return False


def _normalize_allowed(av: Any) -> Any:
    if av is None:
        return None
    if isinstance(av, dict) and av:
        return list(av.keys())
    if isinstance(av, list) and av:
        return av
    if isinstance(av, str) and av.strip():
        try:
            j = json.loads(av)
            if isinstance(j, dict) and j:
                return list(j.keys())
            if isinstance(j, list):
                return j
        except json.JSONDecodeError:
            pass
    return av


def _field_dict_for_prompt(row: Dict[str, Any], action: str) -> Optional[Dict[str, Any]]:
    if _is_system_row(row):
        return None
    fn = (row.get("field_name") or "").strip()
    if not fn:
        return None
    fn_low = fn.lower()
    if action == "lookup":
        if not (
            fn_low.endswith("_no")
            or fn_low.endswith("_code")
            or fn_low.endswith("_name")
            or fn_low in ("name", "code", "email", "phone", "mobile", "company_name", "short_name", "product_name")
        ):
            return None
    dt = (row.get("data_type") or "varchar").strip()
    if "(" in dt:
        dt = dt.split("(", 1)[0].strip()
    out: Dict[str, Any] = {
        "field": fn,
        "field_cn": (row.get("field_cn") or "").strip() or fn,
        "type": dt,
    }
    if row.get("is_required"):
        out["required"] = True
    av = _normalize_allowed(row.get("allowed_values"))
    if av is not None and av != [] and av != {}:
        out["allowed_values"] = av
    return out


def _build_fields_for_step(action: str, table: Optional[str]) -> List[Dict[str, Any]]:
    if not table or not table.strip():
        return []
    with db_conn() as conn:
        rows = _fetch_fields_for_table(conn, table.strip())
    out: List[Dict[str, Any]] = []
    for r in rows:
        fd = _field_dict_for_prompt(r, action)
        if fd:
            out.append(fd)
    return out


def _default_steps_for_intent(
    intent: str, tables: List[str]
) -> List[Dict[str, Any]]:
    submit_tool = {
        "create_simple_entity": "write_entity",
        "update_simple_entity": "update_entity",
        "delete_simple_entity": "delete_entity",
        "query_master_data": "query_db",
        "create_contract": "submit_contract",
        "update_contract": "update_contract",
        "delete_contract": "delete_entity",
    }.get(intent, "query_db")

    if intent == "query_master_data":
        steps: List[Dict[str, Any]] = []
        for i, tn in enumerate(tables, start=1):
            steps.append(
                {
                    "seq": i,
                    "action": "lookup",
                    "label": f"查询 {tn}",
                    "table": tn,
                    "instruction": "只读 SELECT；可 JOIN 其它 business 表。",
                    "fields": [],
                }
            )
        return steps

    if not tables:
        return []

    main = tables[0]
    related = [t for t in tables[1:] if t != main]
    steps = []
    seq = 1
    for rt in related:
        steps.append(
            {
                "seq": seq,
                "action": "lookup",
                "label": f"关联 {rt}",
                "table": rt,
                "instruction": "用 query_db 核对关联记录。",
                "fields": [],
            }
        )
        seq += 1
    steps.append(
        {
            "seq": seq,
            "action": "collect",
            "label": f"收集 {main}",
            "table": main,
            "instruction": "收集字段并调用对应工具。",
            "fields": [],
        }
    )
    seq += 1
    steps.append(
        {
            "seq": seq,
            "action": "submit",
            "label": "提交预览",
            "tool": submit_tool,
        }
    )
    return steps


def _field_names(fields: Any) -> Set[str]:
    if not isinstance(fields, list):
        return set()
    out: Set[str] = set()
    for f in fields:
        if isinstance(f, dict) and f.get("field"):
            out.add(str(f["field"]))
    return out


def _count_steps(conn, intent: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM business.workflow_steps WHERE workflow_intent = %s",
            (intent,),
        )
        row = cur.fetchone()
        return int(row[0]) if row else 0


def _insert_default_steps(conn, intent: str, tables: List[str]) -> None:
    steps = _default_steps_for_intent(intent, tables)
    if not steps:
        return
    with conn.cursor() as cur:
        for st in steps:
            seq = int(st.get("seq") or 0)
            action = (st.get("action") or "").strip()
            label = (st.get("label") or "").strip() or f"步骤{seq}"
            instr = (st.get("instruction") or "").strip() or None
            if action == "submit":
                tool = (st.get("tool") or "").strip() or None
                cur.execute(
                    """
                    INSERT INTO business.workflow_steps
                      (workflow_intent, seq, action, label, table_name, instruction, tool)
                    VALUES (%s, %s, %s, %s, NULL, %s, %s)
                    """,
                    (intent, seq, action, label, instr, tool),
                )
            else:
                tbl = (st.get("table") or "").strip() or None
                cur.execute(
                    """
                    INSERT INTO business.workflow_steps
                      (workflow_intent, seq, action, label, table_name, instruction, tool)
                    VALUES (%s, %s, %s, %s, %s, %s, NULL)
                    """,
                    (intent, seq, action, label, tbl, instr),
                )


def _proc_sync_exists(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1
            FROM pg_proc p
            JOIN pg_namespace n ON p.pronamespace = n.oid
            WHERE n.nspname = 'meta' AND p.proname = 'sync_workflow_step_fields'
            LIMIT 1
            """
        )
        return cur.fetchone() is not None


def run_sync(force: bool) -> int:
    try:
        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM business.workflow_steps LIMIT 1")
    except psycopg2.errors.UndefinedTable:
        print(
            "错误：business.workflow_steps 不存在。请先执行 constitution/refactor_workflow_steps.sql",
            flush=True,
        )
        return 1

    with db_conn() as conn:
        if not _proc_sync_exists(conn):
            print(
                "错误：存储过程 meta.sync_workflow_step_fields 不存在。请先执行 constitution/sync_workflow_step_fields.sql",
                flush=True,
            )
            return 1

    inserted = 0
    with db_conn() as conn:
        conn.autocommit = False
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT intent, target_tables
                FROM business.workflows
                WHERE COALESCE(is_active, 1) = 1
                ORDER BY intent
                """
            )
            rows = cur.fetchall()

        for row in rows:
            intent = (row.get("intent") or "").strip()
            if not intent:
                continue
            tt = row.get("target_tables")
            if isinstance(tt, list):
                target_list = [str(x) for x in tt if x]
            elif tt is None:
                target_list = []
            else:
                target_list = [str(tt)]

            tables = _collect_tables_for_workflow(intent, target_list)
            n = _count_steps(conn, intent)
            if n == 0 and tables:
                _insert_default_steps(conn, intent, tables)
                print(f"[insert] {intent}: 写入默认 workflow_steps 行", flush=True)
                inserted += 1
        conn.commit()

    with db_conn() as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SELECT meta.sync_workflow_step_fields(%s)", (force,))

    diff_printed = 0
    if not force:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT intent, target_tables
                    FROM business.workflows
                    WHERE COALESCE(is_active, 1) = 1
                    ORDER BY intent
                    """
                )
                wf_rows = cur.fetchall()

            for row in wf_rows:
                intent = (row.get("intent") or "").strip()
                if not intent:
                    continue
                tt = row.get("target_tables")
                if isinstance(tt, list):
                    target_list = [str(x) for x in tt if x]
                elif tt is None:
                    target_list = []
                else:
                    target_list = [str(tt)]

                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        """
                        SELECT seq, action, table_name, fields
                        FROM business.workflow_steps
                        WHERE workflow_intent = %s
                        ORDER BY seq
                        """,
                        (intent,),
                    )
                    db_rows = cur.fetchall()

                diff_lines: List[str] = []
                for r in db_rows:
                    d = dict(r)
                    action = (d.get("action") or "").strip()
                    if action == "submit":
                        continue
                    tbl = (d.get("table_name") or "").strip()
                    if not tbl:
                        continue
                    fld = d.get("fields")
                    if isinstance(fld, str):
                        try:
                            fld = json.loads(fld)
                        except json.JSONDecodeError:
                            fld = []
                    if not isinstance(fld, list):
                        fld = []
                    exp = _build_fields_for_step(action, tbl)
                    sn = _field_names(fld)
                    gn = _field_names(exp)
                    seq = int(d.get("seq") or 0)
                    miss = gn - sn
                    extra = sn - gn
                    if miss:
                        diff_lines.append(f"  seq={seq} meta 有而库缺: {sorted(miss)}")
                    if extra:
                        diff_lines.append(f"  seq={seq} 库有而 meta 无: {sorted(extra)}")

                if diff_lines:
                    print(f"[diff] {intent}:", flush=True)
                    for line in diff_lines:
                        print(line, flush=True)
                    diff_printed += 1
                else:
                    print(f"[ok] {intent}", flush=True)

    if force:
        print(f"完成。已调用 meta.sync_workflow_step_fields(true)；新建默认行 {inserted} 条。", flush=True)
    else:
        print(
            f"完成。已调用 meta.sync_workflow_step_fields(false)；新建默认行 {inserted} 条；diff 报告 {diff_printed} 条意图。",
            flush=True,
        )
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description="同步 workflow_steps.fields（存储过程）")
    p.add_argument(
        "--force",
        action="store_true",
        help="强制用当前 meta 覆盖各 step 的 fields",
    )
    args = p.parse_args()
    raise SystemExit(run_sync(args.force))


if __name__ == "__main__":
    main()
