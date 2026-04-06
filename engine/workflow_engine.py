"""V15-D 通用工作流：确认后遍历 pending_submit.writes 执行。"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import psycopg2.extras
from psycopg2 import sql as psql

from engine.collecting import (
    _format_write_success_summary,
    _load_frame_context_snapshot,
    _row_jsonable,
    build_col_vals_from_fields,
    merge_execution_result_into_frame,
)
from tools.database import db_conn, db_conn_llm

logger = logging.getLogger(__name__)


def commit_insert_from_slots(ctx: Dict[str, Any]) -> None:
    """白名单校验后 INSERT（V15-B：复用 workflow_engine_exec._do_single_insert）。"""
    from engine.workflow_engine_exec import _do_single_insert

    frame_id = ctx.get("frame_id")
    target_table = (ctx.get("target_table") or "").strip()
    logger.info("[execute.write] 入口 frame=%s table=%s", frame_id, target_table or None)
    if frame_id is None or not target_table:
        ctx["reply"] = "内部错误：缺少 frame 或目标表。"
        ctx["status"] = "error"
        return

    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT param_key, value
                FROM business.slots
                WHERE frame_id = %s AND status = 'supplied'
                ORDER BY id
                """,
                (int(frame_id),),
            )
            rows = cur.fetchall()

    field_dict: Dict[str, Any] = {}
    for param_key, value in rows:
        if value is None:
            continue
        field_dict[str(param_key)] = value

    col_vals, err = build_col_vals_from_fields(target_table, field_dict)
    if err:
        ctx["reply"] = err
        ctx["status"] = "error"
        merge_execution_result_into_frame(
            int(frame_id),
            {
                "ok": False,
                "operation": "insert",
                "table_name": target_table,
                "error": err,
            },
        )
        return

    row_dict = {c: v for c, v in col_vals}
    verify_stmt = psql.SQL("SELECT * FROM business.{} WHERE id = %s").format(
        psql.Identifier(target_table)
    )

    try:
        with db_conn_llm() as conn:
            conn.autocommit = True
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                ins = _do_single_insert(cur, target_table, row_dict)
                new_id = int(ins.get("id"))
                cur.execute(verify_stmt, (new_id,))
                verified = cur.fetchone()
        if not verified:
            msg = "插入后验证失败：数据库中未找到新建记录。"
            ctx["reply"] = f"❌ 写入失败：{msg}"
            ctx["status"] = "error"
            merge_execution_result_into_frame(
                int(frame_id),
                {
                    "ok": False,
                    "operation": "insert",
                    "table_name": target_table,
                    "error": msg,
                },
            )
            return
        vdict = dict(verified)
        with db_conn_llm() as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE business.frames
                    SET status = 'completed', updated_at = now()
                    WHERE id = %s
                    """,
                    (int(frame_id),),
                )
        merge_execution_result_into_frame(
            int(frame_id),
            {
                "ok": True,
                "operation": "insert",
                "table_name": target_table,
                "record_id": vdict.get("id"),
                "row": _row_jsonable(vdict),
            },
        )
        ctx["reply"] = f"✅ 已写入：{_format_write_success_summary(vdict)}"
        ctx["status"] = "completed"
    except Exception as e:
        logger.exception("[collecting] INSERT 失败: %s", e)
        ctx["reply"] = f"❌ 写入失败：{e}"
        ctx["status"] = "error"
        merge_execution_result_into_frame(
            int(frame_id),
            {
                "ok": False,
                "operation": "insert",
                "table_name": target_table,
                "error": str(e),
            },
        )


def commit_update(
    frame_id: int,
    table_name: str,
    record_id: int,
    fields: Dict[str, Any],
    ctx: Dict[str, Any],
) -> None:
    """回归测试用：单条 UPDATE，内部走 do_update。"""
    from engine.workflow_engine_exec import do_update

    logger.info(
        "[execute.update] 入口 frame=%s table=%s record_id=%s",
        frame_id,
        table_name,
        record_id,
    )
    target_table = (table_name or "").strip()
    try:
        with db_conn_llm() as conn:
            conn.autocommit = True
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                merged = dict(fields or {})
                merged["record_id"] = int(record_id)
                do_update(cur, target_table, merged)
    except RuntimeError as e:
        logger.info("[execute.update] 失败: %s", e)
        ctx["reply"] = f"❌ 写入失败：{e}"
        ctx["status"] = "error"
        merge_execution_result_into_frame(
            int(frame_id),
            {
                "ok": False,
                "operation": "update",
                "table_name": target_table,
                "record_id": int(record_id),
                "error": str(e),
            },
        )
        return
    except Exception as e:
        logger.exception("[collecting] UPDATE 失败: %s", e)
        ctx["reply"] = f"❌ 写入失败：{e}"
        ctx["status"] = "error"
        merge_execution_result_into_frame(
            int(frame_id),
            {
                "ok": False,
                "operation": "update",
                "table_name": target_table,
                "record_id": int(record_id),
                "error": str(e),
            },
        )
        return
    with db_conn_llm() as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE business.frames
                SET status = 'completed', updated_at = now()
                WHERE id = %s
                """,
                (int(frame_id),),
            )
    merge_execution_result_into_frame(
        int(frame_id),
        {
            "ok": True,
            "operation": "update",
            "table_name": target_table,
            "record_id": int(record_id),
        },
    )
    ctx["reply"] = "✅ 已写入（测试）"
    ctx["status"] = "completed"



def run_confirm_execution(frame_id: int, user_id: int, ctx: Dict[str, Any]) -> None:
    """POST /ai/confirm：统一遍历 pending_submit.writes 执行（V15-D）。"""
    from engine.workflow_engine_exec import run_confirm_generic_writes

    ctx["frame_id"] = frame_id
    run_confirm_generic_writes(frame_id, user_id, ctx)

