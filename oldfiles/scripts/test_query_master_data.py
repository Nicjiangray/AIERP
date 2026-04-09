#!/usr/bin/env python3
"""
集成测试：run_query_master_data（依赖本机 PostgreSQL + LLM）。

用法（在仓库根目录）：
  .venv/bin/python scripts/test_query_master_data.py

需：PG* 环境变量、`llm_assistant` 对 ``meta.meta_*`` 至少 SELECT、对 ``business.*`` 可查询，
    且可写 ``business.frames`` / ``business.slots``。
    若报 permission denied for schema meta，请在库中执行例如：
    ``GRANT USAGE ON SCHEMA meta TO llm_assistant;``
    ``GRANT SELECT ON ALL TABLES IN SCHEMA meta TO llm_assistant;``
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import psycopg2
import psycopg2.extras

from engine.workflow_runner import run_query_master_data
from tools.database import db_conn_llm


def _print_case_header(n: int, title: str) -> None:
    print()
    print("=" * 72)
    print(f"用例 {n}：{title}")
    print("=" * 72)


def _print_result(ctx: Dict[str, Any]) -> None:
    tt = ctx.get("target_table")
    cond = ctx.get("conditions")
    reply = ctx.get("reply") or ""
    data = ctx.get("data") or {}
    main = data.get("main") if isinstance(data, dict) else []
    sub = data.get("sub") if isinstance(data, dict) else []
    if not isinstance(main, list):
        main = []
    if not isinstance(sub, list):
        sub = []

    qfu = ctx.get("query_follow_up")
    follow = bool(qfu)
    print("--- LLM 提取 ---")
    print(f"  target_table: {tt!r}")
    print(f"  conditions:   {json.dumps(cond, ensure_ascii=False) if cond is not None else None}")
    print("--- 输出 ---")
    print(f"  reply（前100字）: {reply[:100]!r}{'…' if len(reply) > 100 else ''}")
    print(f"  main 条数: {len(main)}")
    print(f"  sub 条数:  {len(sub)}")
    print(f"  是否有 follow_up: {follow}")
    if qfu:
        qs = str(qfu)
        print(f"  query_follow_up（前80字）: {qs[:80]!r}{'…' if len(qs) > 80 else ''}")


def _audit_workflow_tables() -> None:
    print()
    print("-" * 72)
    print("用例 5：frames / slots（跑完用例 1 后）")
    print("-" * 72)
    try:
        with db_conn_llm() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT * FROM business.frames
                    ORDER BY created_at DESC NULLS LAST, id DESC
                    LIMIT 1
                    """
                )
                wt = cur.fetchone()
                print("frames 最新 1 条：")
                if wt:
                    print(json.dumps(dict(wt), ensure_ascii=False, default=str, indent=2))
                else:
                    print("  （无记录）")

                cur.execute(
                    """
                    SELECT * FROM business.slots
                    ORDER BY id DESC
                    LIMIT 5
                    """
                )
                rows = cur.fetchall()
                print("slots 最新 5 条：")
                if rows:
                    for r in rows:
                        print(json.dumps(dict(r), ensure_ascii=False, default=str, indent=2))
                else:
                    print("  （无记录）")

                if wt and rows:
                    ok = False
                    for r in rows:
                        if r.get("param_key") == "query_conditions" and r.get("value"):
                            try:
                                payload = json.loads(r["value"])
                            except json.JSONDecodeError:
                                payload = {}
                            if "target_table" in str(payload) and "conditions" in payload:
                                ok = True
                    print()
                    print(
                        "  检查 param_key=query_conditions 且 value 含 target_table/conditions：",
                        "通过" if ok else "未满足预期（请人工核对）",
                    )
    except Exception as e:
        print(f"  数据库查询失败: {e}")


def main() -> None:
    base_ctx: Dict[str, Any] = {
        "user_id": 2,
        "workflow_intent": "test_query_master_data",
    }

    # 1. 条件明确的单表查询
    _print_case_header(1, "条件明确的单表查询")
    print('user_input = "查一下客户名包含 ABC 的客户"')
    print("预期：target_table=customers，conditions 有值，main 有数据")
    ctx1 = run_query_master_data({**base_ctx, "user_input": "查一下客户名包含 ABC 的客户"})
    _print_result(ctx1)

    # 2. 无条件全表查询
    _print_case_header(2, "无条件全表查询")
    print('user_input = "把所有产品列出来"')
    print("预期：target_table=products，conditions 为空，main 条数<=50")
    ctx2 = run_query_master_data({**base_ctx, "user_input": "把所有产品列出来"})
    _print_result(ctx2)
    dm = (ctx2.get("data") or {}).get("main")
    if isinstance(dm, list) and len(dm) <= 50:
        print("  [检查] main 条数 <= 50：通过")
    else:
        print("  [检查] main 条数 <= 50：请核对")

    # 3. 有子表的查询
    _print_case_header(3, "有子表的查询")
    print('user_input = "查合同编号包含 CT-2024 的合同"')
    print("预期：target_table=contracts，main 有数据，sub 有 contract_items")
    ctx3 = run_query_master_data(
        {**base_ctx, "user_input": "查合同编号包含 CT-2024 的合同"}
    )
    _print_result(ctx3)

    # 4. 参数不足需要追问
    _print_case_header(4, "参数不足需要追问")
    print('user_input = "帮我查一下"')
    print("预期：follow_up 追问，data 为空列表")
    ctx4 = run_query_master_data({**base_ctx, "user_input": "帮我查一下"})
    _print_result(ctx4)
    d4 = ctx4.get("data") or {}
    m4, s4 = d4.get("main"), d4.get("sub")
    if (not m4 or len(m4) == 0) and (not s4 or len(s4) == 0):
        print("  [检查] data.main/sub 为空：通过（或符合追问路径）")

    # 5. 验证 workflow 写入（依赖用例 1 已写入）
    _audit_workflow_tables()

    print()
    print("全部用例顺序执行完毕。")


if __name__ == "__main__":
    try:
        main()
    except psycopg2.Error as e:
        print(f"\n[错误] 数据库: {e}", file=sys.stderr)
        print(
            "若提示 permission denied for schema meta，请给 llm_assistant 授权 meta 只读，见脚本顶部说明。",
            file=sys.stderr,
        )
        sys.exit(1)
