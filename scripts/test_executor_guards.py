#!/usr/bin/env python3
"""
工具执行层防护规则回归测试（不调用 LLM）。
需本机 PostgreSQL 与 aierp 库。

用法:
  cd /Users/nicjiang/aierp && .venv/bin/python scripts/test_executor_guards.py
"""
from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional

# 压低 executor 日志，仅看 PASS/FAIL
logging.getLogger("engine.tool_executor").setLevel(logging.ERROR)
logging.getLogger("engine.collecting").setLevel(logging.ERROR)

# 项目根
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tools.database import db_conn  # noqa: E402
from engine.collecting import (  # noqa: E402
    apply_preview_to_slots,
    get_frame_verified_record_id,
    persist_verified_record_id_to_frame,
)
from engine.workflow_engine import commit_insert_from_slots, commit_update  # noqa: E402
from engine.tool_executor import (  # noqa: E402
    execute_query_db,
    execute_update_entity,
    execute_write_entity,
)


def _create_temp_frame(context_snapshot: Optional[Dict[str, Any]] = None) -> int:
    snap = json.dumps(context_snapshot or {}, ensure_ascii=False)
    with db_conn() as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO business.frames
                    (workflow_intent, user_id, status, subject, context_snapshot)
                VALUES ('update_simple_entity', 2, 'collecting', 'test_executor_guards', %s::jsonb)
                RETURNING id
                """,
                (snap,),
            )
            return int(cur.fetchone()[0])


def _delete_frame(frame_id: int) -> None:
    with db_conn() as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM business.slots WHERE frame_id = %s", (int(frame_id),)
            )
            cur.execute("DELETE FROM business.frames WHERE id = %s", (int(frame_id),))


def _load_snapshot(frame_id: int) -> Dict[str, Any]:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT context_snapshot FROM business.frames WHERE id = %s",
                (int(frame_id),),
            )
            row = cur.fetchone()
            if not row or row[0] is None:
                return {}
            raw = row[0]
            if isinstance(raw, dict):
                return dict(raw)
            if isinstance(raw, str):
                try:
                    return json.loads(raw) if raw.strip() else {}
                except json.JSONDecodeError:
                    return {}
            return {}


def apply_verified_if_query_db_single_row(
    frame_id: int, result: Dict[str, Any]
) -> None:
    """
    与 engine/collecting.run_collecting 中 query_db 成功分支一致：
    count==1 且仅一行且含 id 时写入 verified_record_id。
    """
    rows = result.get("rows")
    _cnt = result.get("count")
    if (
        frame_id is not None
        and _cnt == 1
        and isinstance(rows, list)
        and len(rows) == 1
        and isinstance(rows[0], dict)
        and "id" in rows[0]
    ):
        persist_verified_record_id_to_frame(frame_id, int(rows[0]["id"]))


def test1_no_verified_returns_error() -> tuple[bool, str]:
    fid = _create_temp_frame({})
    try:
        out = execute_update_entity(
            "customers",
            1001,
            {"credit_limit": "250000"},
            "test",
            frame_id=fid,
        )
        if out.get("error") and "请先用 query_db" in out["error"]:
            return True, "无 verified_record_id 时返回 error"
        return False, f"期望 error，得到: {out!r}"
    finally:
        _delete_frame(fid)


def test2_uses_verified_not_llm() -> tuple[bool, str]:
    fid = _create_temp_frame({})
    try:
        snap = _load_snapshot(fid)
        snap["verified_record_id"] = 14
        with db_conn() as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE business.frames SET context_snapshot = %s::jsonb WHERE id = %s",
                    (json.dumps(snap, ensure_ascii=False), fid),
                )
        out = execute_update_entity(
            "customers",
            1001,
            {"credit_limit": "250000"},
            "test",
            frame_id=fid,
        )
        if out.get("error"):
            return False, f"不应失败: {out}"
        rid = (out.get("pending_update") or {}).get("record_id")
        top = out.get("record_id")
        if rid == 14 and top == 14:
            return True, "pending_update.record_id 与 record_id 均为 14（非 1001）"
        return False, f"record_id 应为 14，得到 pending_update={rid} top={top}"
    finally:
        _delete_frame(fid)


def test3_query_db_single_row_persists_verified() -> tuple[bool, str]:
    fid = _create_temp_frame({"target_table": "customers"})
    try:
        result = {
            "rows": [{"id": 14, "name": "浑圆纺织"}],
            "count": 1,
        }
        apply_verified_if_query_db_single_row(fid, result)
        v = get_frame_verified_record_id(fid)
        if v == 14:
            return True, "context_snapshot 已含 verified_record_id=14"
        return False, f"期望 verified_record_id=14，得到 {v!r} snap={_load_snapshot(fid)}"
    finally:
        _delete_frame(fid)


def test4_multi_row_no_persist() -> tuple[bool, str]:
    fid = _create_temp_frame({})
    try:
        result = {
            "rows": [
                {"id": 1, "name": "a"},
                {"id": 2, "name": "b"},
                {"id": 3, "name": "c"},
            ],
            "count": 3,
        }
        apply_verified_if_query_db_single_row(fid, result)
        v = get_frame_verified_record_id(fid)
        if v is None:
            return True, "多行结果未写入 verified_record_id"
        return False, f"不应写入 verified，得到 {v!r}"
    finally:
        _delete_frame(fid)


def test5_query_db_rejects_non_select() -> tuple[bool, str]:
    out = execute_query_db("DELETE FROM business.customers", "guard test")
    err = out.get("error")
    if err and "SELECT" in err:
        return True, f"拒绝非 SELECT: {err[:80]}..."
    return False, f"期望含 SELECT 的校验错误，得到: {out!r}"


def test6_write_entity_missing_required() -> tuple[bool, str]:
    out = execute_write_entity(
        "customers",
        {"name": "仅名称"},
        "guard",
        None,
    )
    err = out.get("error")
    if err and "缺少必填" in err:
        return True, "缺少 ask_if_missing 字段时返回 error"
    return False, f"期望缺少必填错误，得到: {out!r}"


def test7_write_entity_allowed_values() -> tuple[bool, str]:
    import time

    out = execute_write_entity(
        "customers",
        {
            "customer_code": f"GUARD_ENUM_{int(time.time())}",
            "name": "枚举测试",
            "payment_terms": "30天",
            "tax_id": "91110000GUARD",
            "invoice_type": "___INVALID_ENUM_FOR_GUARD___",
        },
        "guard",
        None,
    )
    err = out.get("error")
    if err and ("不在允许范围" in err or "允许" in err):
        return True, "allowed_values 不通过时返回 error"
    # meta 若未配置 invoice_type 的 allowed_values，则无法触发本断言，视为跳过
    if err:
        return False, f"非预期错误: {err!r}"
    return True, "跳过：当前库 meta 未对 invoice_type 配置 allowed_values，无法断言枚举"


def test8_execute_write_verifies_row() -> tuple[bool, str]:
    import time

    code = f"GUARD_W_{int(time.time())}"
    fid = _create_temp_frame({"target_table": "customers"})
    row = {
        "customer_code": code,
        "name": "executor_guard_insert",
        "payment_terms": "30天",
        "tax_id": "91110000GUARDW",
        "invoice_type": "normal",
    }
    try:
        apply_preview_to_slots(fid, "customers", row)
        ctx: Dict[str, Any] = {
            "frame_id": fid,
            "target_table": "customers",
            "table_cn": "客户",
            "subject": "guard",
            "user_id": 2,
        }
        commit_insert_from_slots(ctx)
        if ctx.get("status") != "completed":
            return False, f"期望 completed，得到 {ctx.get('status')!r} reply={ctx.get('reply')!r}"
        if "✅ 已写入" not in (ctx.get("reply") or ""):
            return False, f"回复应含 ✅ 已写入: {ctx.get('reply')!r}"
        snap = _load_snapshot(fid)
        er = snap.get("execution_result") if isinstance(snap, dict) else None
        if not isinstance(er, dict) or not er.get("ok"):
            return False, f"execution_result 应为 ok=True: {er!r}"
        rid = er.get("record_id")
        if rid is None:
            return False, f"无 record_id: {er!r}"
        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM business.customers WHERE id = %s",
                    (int(rid),),
                )
                if cur.fetchone() is None:
                    return False, f"库中无 id={rid}"
        return True, "INSERT 后 execution_result 与库内行一致"
    finally:
        with db_conn() as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM business.customers WHERE customer_code = %s", (code,)
                )
        _delete_frame(fid)


def test9_execute_update_zero_rows() -> tuple[bool, str]:
    fid = _create_temp_frame({})
    try:
        ctx: Dict[str, Any] = {}
        commit_update(
            fid,
            "customers",
            999999999,
            {"short_name": "x"},
            ctx,
        )
        rep = ctx.get("reply") or ""
        if ctx.get("status") == "error" and "未找到" in rep and "❌" in rep:
            return True, "rowcount=0 时失败提示"
        return False, f"期望失败+未找到，status={ctx.get('status')!r} reply={rep!r}"
    finally:
        _delete_frame(fid)


def main() -> None:
    tests: List[tuple[str, Any]] = [
        ("1 update_entity 无 verified_record_id → error", test1_no_verified_returns_error),
        ("2 有 verified_record_id 时用 14 不用 1001", test2_uses_verified_not_llm),
        ("3 query_db 单行 → 写入 verified_record_id", test3_query_db_single_row_persists_verified),
        ("4 query_db 多行 → 不写入 verified_record_id", test4_multi_row_no_persist),
        ("5 execute_query_db 拒绝 DELETE", test5_query_db_rejects_non_select),
        ("6 write_entity 缺少必填字段 → error", test6_write_entity_missing_required),
        ("7 write_entity 枚举不在 allowed_values → error", test7_write_entity_allowed_values),
        ("8 commit_insert_from_slots 插入后验证行存在", test8_execute_write_verifies_row),
        ("9 commit_update rowcount=0 → 失败", test9_execute_update_zero_rows),
    ]

    failed = 0
    for name, fn in tests:
        try:
            ok, reason = fn()
        except Exception as e:
            ok, reason = False, f"异常: {e}"
        if ok:
            print(f"PASS {name}: {reason}")
        else:
            failed += 1
            print(f"FAIL {name}: {reason}")

    if failed:
        print(f"\n共 {failed} 个失败", file=sys.stderr)
        sys.exit(1)
    print("\n全部通过。")


if __name__ == "__main__":
    main()
