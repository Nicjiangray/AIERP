"""V15-B 通用确认执行：do_insert / do_update / do_soft_delete + pending_submit.writes 循环。"""
from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import psycopg2.extras
from psycopg2 import sql as psql
from psycopg2.extras import Json

from engine.collecting import (
    _format_write_success_summary,
    _load_frame_context_snapshot,
    _load_simple_table_whitelist,
    _row_jsonable,
    _validate_ident,
    build_col_vals_from_fields,
    collect_update_fields_validated,
    merge_execution_result_into_frame,
)
from engine.data_sanitize import sanitize_step_data
from tools.database import db_conn_llm

logger = logging.getLogger(__name__)

# 与 pending_submit 侧一致：合同主表/明细可在确认事务内写入（非 simple 实体）
_EXTRA_WRITE_TABLES = frozenset(
    {"contract_changes", "contracts", "contract_items"}
)


def _allowed_tables() -> set:
    return set(_load_simple_table_whitelist()) | _EXTRA_WRITE_TABLES


def _validate_table_for_write(table: str) -> None:
    t = (table or "").strip()
    if not t or not _validate_ident(t):
        raise RuntimeError("非法表名")
    if t not in _allowed_tables():
        raise RuntimeError(f"安全校验失败：表 {t!r} 不在白名单")


def _do_insert_contract_changes_row(
    cur: Any, data: Dict[str, Any]
) -> Dict[str, Any]:
    _validate_table_for_write("contract_changes")
    cid = int(data["contract_id"])
    ct = str(data.get("change_type") or "").strip()
    reason = str(data.get("reason") or "").strip()
    sb = data.get("snapshot_before")
    sa = data.get("snapshot_after")
    ds = data.get("diff_summary") if isinstance(data.get("diff_summary"), dict) else {}
    ev = data.get("evidence_refs")
    if not isinstance(ev, list):
        ev = []
    cur.execute(
        """
        INSERT INTO business.contract_changes (
            contract_id, change_type, reason, evidence_refs,
            snapshot_before, snapshot_after, diff_summary
        ) VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb)
        RETURNING *
        """,
        (
            cid,
            ct,
            reason,
            json.dumps(ev, ensure_ascii=False),
            json.dumps(sb, default=str, ensure_ascii=False),
            json.dumps(sa, default=str, ensure_ascii=False),
            json.dumps(ds, default=str, ensure_ascii=False),
        ),
    )
    row = cur.fetchone()
    if not row:
        raise RuntimeError("INSERT contract_changes 未返回行")
    return dict(row)


def _do_single_insert(cur: Any, table: str, data: Dict[str, Any]) -> Dict[str, Any]:
    if table == "contract_changes":
        return _do_insert_contract_changes_row(cur, data)
    _validate_table_for_write(table)
    col_vals, err = build_col_vals_from_fields(table, data)
    if err:
        raise RuntimeError(err)
    params = tuple(v for _, v in col_vals)
    identifiers = [psql.Identifier(c) for c, _ in col_vals]
    placeholders = psql.SQL(", ").join([psql.Placeholder()] * len(col_vals))
    stmt = psql.SQL("INSERT INTO business.{} ({}) VALUES ({}) RETURNING *").format(
        psql.Identifier(table),
        psql.SQL(", ").join(identifiers),
        placeholders,
    )
    cur.execute(stmt, params)
    row = cur.fetchone()
    if not row:
        raise RuntimeError(f"INSERT INTO {table} 未返回结果")
    return dict(row)


def do_insert(cur: Any, table: str, data: Any) -> Dict[str, Any]:
    """INSERT：data 为 dict 单行或 list[dict] 多行。"""
    if isinstance(data, list):
        results: List[Dict[str, Any]] = []
        for row in data:
            if not isinstance(row, dict):
                continue
            results.append(_do_single_insert(cur, table, row))
        ids = [r.get("id") for r in results if r.get("id") is not None]
        out: Dict[str, Any] = {"ids": ids, "count": len(results)}
        if ids:
            out["id"] = ids[-1]
        return out
    if not isinstance(data, dict):
        raise RuntimeError("insert 的 data 须为对象或数组")
    return _do_single_insert(cur, table, data)


def _do_single_update(cur: Any, table: str, data: Dict[str, Any]) -> Dict[str, Any]:
    d = dict(data)
    record_id = d.pop("record_id", None) or d.pop("id", None)
    if record_id is None:
        raise RuntimeError(f"update {table} 缺少 record_id")
    _validate_table_for_write(table)
    norm, err = collect_update_fields_validated(table, d)
    if err:
        raise RuntimeError(err)
    norm.pop("id", None)
    if not norm:
        raise RuntimeError("没有可更新的字段")
    sets = [psql.SQL("{} = %s").format(psql.Identifier(c)) for c in norm.keys()]
    params: List[Any] = [norm[c] for c in norm.keys()]
    params.append(int(record_id))
    stmt = psql.SQL("UPDATE business.{} SET {} WHERE id = %s RETURNING *").format(
        psql.Identifier(table),
        psql.SQL(", ").join(sets),
    )
    logger.info(
        "[workflow_engine_exec] do_update SQL preview: UPDATE business.%s SET %s WHERE id=%s params_tail=%s",
        table,
        ", ".join(f"{c}=?" for c in norm.keys()),
        record_id,
        tuple(params)[-3:],
    )
    cur.execute(stmt, tuple(params))
    if cur.rowcount == 0:
        raise RuntimeError(f"UPDATE {table} id={record_id} 未找到记录")
    row = cur.fetchone()
    return dict(row) if row else {}


def do_update(cur: Any, table: str, data: Any) -> Dict[str, Any]:
    """UPDATE：data 为 dict（单行）或 list[dict]（多行），每行须含 record_id 或 id。"""
    if isinstance(data, list):
        results: List[Dict[str, Any]] = []
        for row in data:
            if not isinstance(row, dict):
                continue
            results.append(_do_single_update(cur, table, row))
        return {"updated_count": len(results), "rows": results}
    if isinstance(data, dict):
        return _do_single_update(cur, table, data)
    raise RuntimeError("update 的 data 须为对象或数组")


def _do_single_soft_delete(
    cur: Any,
    table: str,
    data: Dict[str, Any],
    where_field: str = "id",
    require_row: bool = True,
) -> Dict[str, Any]:
    _validate_table_for_write(table)
    wf = (where_field or "id").strip()
    if not _validate_ident(wf):
        raise RuntimeError("非法 WHERE 字段名")
    d = dict(data)
    key_val = d.get(wf)
    if key_val is None:
        key_val = d.get("record_id") or d.get("id")
    if key_val is None:
        raise RuntimeError(f"soft_delete {table} 缺少 {wf}")
    stmt = psql.SQL(
        "UPDATE business.{} SET deleted_at = now() WHERE {} = %s AND deleted_at IS NULL RETURNING *"
    ).format(psql.Identifier(table), psql.Identifier(wf))
    cur.execute(stmt, (key_val,))
    rc = cur.rowcount
    if require_row and rc == 0:
        raise RuntimeError(f"软删 {table} {wf}={key_val!r} 未找到记录")
    row = cur.fetchone() if rc else None
    out: Dict[str, Any] = {"deleted_count": rc}
    if row:
        out.update(dict(row))
        out["id"] = dict(row).get("id")
    return out


def do_soft_delete(
    cur: Any,
    table: str,
    data: Any,
    *,
    where_field: str = "id",
    require_row: bool = True,
) -> Dict[str, Any]:
    """
    软删：data 为 dict、list[dict]（多行），或标量（视为 id）。
    """
    if isinstance(data, list):
        total = 0
        rows_out: List[Dict[str, Any]] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            r = _do_single_soft_delete(cur, table, item, where_field, require_row)
            total += int(r.get("deleted_count") or 0)
            rows_out.append(r)
        return {"deleted_count": total, "rows": rows_out}
    if isinstance(data, dict):
        return _do_single_soft_delete(cur, table, data, where_field, require_row)
    return _do_single_soft_delete(
        cur, table, {"record_id": data}, where_field, require_row
    )


def _fill_contract_snapshot_after(cur: Any, contract_id: int) -> Dict[str, Any]:
    """确认事务内：前序 write 已生效后，读取合同+明细作为 snapshot_after。"""
    cur.execute(
        "SELECT * FROM business.contracts WHERE id = %s AND deleted_at IS NULL",
        (int(contract_id),),
    )
    c_row = cur.fetchone()
    cur.execute(
        """
        SELECT * FROM business.contract_items
        WHERE contract_id = %s AND deleted_at IS NULL
        ORDER BY id
        """,
        (int(contract_id),),
    )
    items_rows = cur.fetchall()
    return {
        "contract": _row_jsonable(dict(c_row)) if c_row else {},
        "items": [_row_jsonable(dict(r)) for r in items_rows],
    }


def resolve_depends(
    data: Any,
    depends_on: Optional[List[int]],
    step_results: Dict[int, Any],
) -> Any:
    if not depends_on:
        return data
    if isinstance(data, dict):
        return _resolve_depends_dict(data, depends_on, step_results)
    if isinstance(data, list):
        return [
            _resolve_depends_dict(r, depends_on, step_results)
            if isinstance(r, dict)
            else r
            for r in data
        ]
    return data


def _resolve_depends_dict(
    row: Dict[str, Any],
    depends_on: List[int],
    step_results: Dict[int, Any],
) -> Dict[str, Any]:
    out = dict(row)
    dep_id: Optional[int] = None
    for dep_seq in depends_on:
        dep = step_results.get(int(dep_seq)) or {}
        dep_id = dep.get("id")
        if dep_id is None and isinstance(dep.get("ids"), list) and dep["ids"]:
            try:
                dep_id = int(dep["ids"][0])
            except (TypeError, ValueError):
                dep_id = None
        if dep_id is not None:
            break
    if dep_id is None:
        return out
    # 子表明细依赖主表 contracts insert：必须补 contract_id。
    # 旧逻辑「首个空 *_id」会误填 product_id 等，且 contract_id 未出现在 payload 时永远不会补上。
    if out.get("contract_id") in (None, ""):
        out["contract_id"] = dep_id
    return out


def build_success_reply(
    writes: List[Dict[str, Any]], step_results: Dict[int, Any]
) -> str:
    if not writes:
        return "✅ 已执行"
    last = writes[-1]
    tbl = last.get("table")
    op = last.get("operation")
    seq_l = last.get("seq")
    sr = step_results.get(int(seq_l)) if seq_l is not None else None
    if op == "insert" and tbl == "contract_changes" and isinstance(sr, dict):
        cno = None
        for _seq, res in sorted(step_results.items()):
            if isinstance(res, dict) and res.get("contract_no"):
                cno = res.get("contract_no")
                break
        if cno:
            return f"✅ 合同已更新：{cno}"
        return "✅ 合同已更新"
    if op == "insert" and isinstance(sr, dict) and sr.get("contract_no"):
        return f"✅ 合同已创建：{sr.get('contract_no')}"
    if op == "insert" and isinstance(sr, dict) and sr.get("id") is not None:
        return f"✅ 已写入：表 {tbl} id={sr.get('id')}"
    if op == "soft_delete" and tbl == "contracts":
        return "✅ 合同已作废"
    return "✅ 已执行"


def run_confirm_generic_writes(frame_id: int, user_id: int, ctx: Dict[str, Any]) -> None:
    """遍历 pending_submit.writes，单事务执行（含 update_contract）。"""
    _ = user_id
    snap = _load_frame_context_snapshot(int(frame_id))
    ps = snap.get("pending_submit") if isinstance(snap.get("pending_submit"), dict) else {}
    writes = ps.get("writes") if isinstance(ps.get("writes"), list) else []
    reason = (ps.get("reason") or "").strip()
    logger.info(
        "[workflow_engine_exec] pending_submit writes_count=%s writes=%s",
        len(writes),
        json.dumps(writes, ensure_ascii=False, default=str)[:8000],
    )
    if not writes:
        ctx["reply"] = "❌ 没有待执行的写操作（writes）"
        ctx["status"] = "error"
        return

    step_results: Dict[int, Any] = {}
    try:
        with db_conn_llm() as conn:
            conn.autocommit = False
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                for w in writes:
                    if not isinstance(w, dict):
                        continue
                    seq = w.get("seq")
                    table = (w.get("table") or "").strip()
                    op = (w.get("operation") or "").strip().lower()
                    raw_data = w.get("data")
                    depends_on = w.get("depends_on")
                    if depends_on is None and w.get("depends_on_seq") is not None:
                        depends_on = [int(w["depends_on_seq"])]
                    if not isinstance(depends_on, list):
                        depends_on = None
                    where_field = (w.get("where_field") or "id").strip()
                    require_row = w.get("require_row", True)
                    if require_row is False:
                        require_row = False

                    data = raw_data
                    data = sanitize_step_data(table, data, op)
                    if (
                        table == "contract_changes"
                        and op == "insert"
                        and isinstance(data, dict)
                    ):
                        cid = data.get("contract_id")
                        sa0 = data.get("snapshot_after")
                        if cid and not sa0:
                            data = dict(data)
                            data["snapshot_after"] = _fill_contract_snapshot_after(
                                cur, int(cid)
                            )
                    if depends_on:
                        data = resolve_depends(data, depends_on, step_results)

                    if op == "insert":
                        res = do_insert(cur, table, data)
                    elif op == "update":
                        res = do_update(cur, table, data)
                    elif op in ("soft_delete", "delete"):
                        res = do_soft_delete(
                            cur,
                            table,
                            data,
                            where_field=where_field,
                            require_row=require_row,
                        )
                    else:
                        raise RuntimeError(f"未知 operation: {op!r}")

                    if seq is not None:
                        step_results[int(seq)] = res

                cur.execute(
                    """
                    UPDATE business.frames
                    SET status = 'completed', updated_at = now()
                    WHERE id = %s
                    """,
                    (int(frame_id),),
                )
            conn.commit()
    except Exception as e:
        logger.exception("[workflow_engine_exec] 通用执行失败: %s", e)
        ctx["reply"] = f"❌ 执行失败：{e}"
        ctx["status"] = "error"
        merge_execution_result_into_frame(
            int(frame_id),
            {"ok": False, "error": str(e), "engine": "v15b_generic"},
        )
        return

    ctx["reply"] = build_success_reply(writes, step_results)
    ctx["status"] = "completed"
    ser: Dict[str, Any] = {}
    for k, v in step_results.items():
        if isinstance(v, dict):
            ser[str(k)] = _row_jsonable(v)
        else:
            ser[str(k)] = v

    merge_execution_result_into_frame(
        int(frame_id),
        {
            "ok": True,
            "engine": "v15b_generic",
            "step_results": ser,
        },
    )
