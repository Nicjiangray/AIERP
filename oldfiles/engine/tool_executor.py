"""
执行 collecting Agent 的 LLM 工具调用：校验 + 数据库访问，代码层兜底。
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import psycopg2.extras

from engine.field_normalization import normalize_entity_fields
from tools.database import db_conn

logger = logging.getLogger(__name__)

_MAX_ROWS = 50

# SELECT 且禁止明显危险关键字（在去除字符串后的片段上检测）
_FORBIDDEN_SQL_KW = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|TRUNCATE|ALTER|CREATE|GRANT|REVOKE|"
    r"EXECUTE|CALL|COPY|INTO)\b|\bFOR\s+UPDATE\b",
    re.IGNORECASE | re.DOTALL,
)

# 禁止访问的 schema（显式写出时拦截）
_FORBIDDEN_SCHEMA = re.compile(
    r"\b(public|pg_catalog|information_schema)\s*\.",
    re.IGNORECASE,
)

_FROM_JOIN_WORD = re.compile(
    r"(?i)\b(?:from|join)\s+(?:lateral\s+)?([a-zA-Z_][a-zA-Z0-9_]*)",
)

# FROM/JOIN 后允许的无 schema 前缀关键字（函数或特殊语法）
_ALLOWED_UNQUALIFIED = frozenset(
    {
        "unnest",
        "generate_series",
        "jsonb_to_recordset",
        "json_to_recordset",
        "regexp_split_to_table",
    }
)


def _single_statement(sql: str) -> str:
    s = sql.strip().rstrip(";").strip()
    if ";" in s:
        raise ValueError("仅允许单条 SQL，不能包含分号串联多条语句。")
    return s


def _strip_string_literals_for_scan(sql: str) -> str:
    """弱化移除字符串字面量，避免误伤；不处理 SQL 标准外的所有转义边界。"""
    out: List[str] = []
    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        if c == "'":
            out.append(" ")
            i += 1
            while i < n:
                if sql[i] == "'" and i + 1 < n and sql[i + 1] == "'":
                    i += 2
                    continue
                if sql[i] == "'":
                    i += 1
                    break
                i += 1
            continue
        if c == '"':
            out.append(" ")
            i += 1
            while i < n and sql[i] != '"':
                if sql[i] == '"' and i + 1 < n and sql[i + 1] == '"':
                    i += 2
                    continue
                i += 1
            if i < n and sql[i] == '"':
                i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _validate_select_only(sql: str) -> str:
    s = _single_statement(sql)
    if not re.match(r"\s*SELECT\b", s, re.IGNORECASE | re.DOTALL):
        raise ValueError("query_db 仅允许以 SELECT 开头的只读查询。")
    return s


def _validate_schema_refs(scan: str) -> None:
    if _FORBIDDEN_SCHEMA.search(scan):
        raise ValueError("禁止访问 public / pg_catalog / information_schema 等 schema。")
    for m in _FROM_JOIN_WORD.finditer(scan):
        tok = (m.group(1) or "").lower()
        end = m.end(1)
        rest = scan[end : end + 48].lstrip()
        if tok in ("business", "meta"):
            continue
        if rest.startswith("("):
            continue
        if tok in _ALLOWED_UNQUALIFIED:
            continue
        raise ValueError(
            f"表引用须使用 business. 或 meta. 限定 schema，"
            f"或使用子查询 / 允许的内置函数；当前片段：{tok!r}"
        )


def _validate_agent_select_sql(sql: str) -> str:
    s = _validate_select_only(sql)
    scan = _strip_string_literals_for_scan(s)
    if _FORBIDDEN_SQL_KW.search(scan):
        raise ValueError("查询包含禁止的关键字（非只读或危险操作）。")
    _validate_schema_refs(scan)
    return s


def _sql_has_limit_clause(sql: str) -> bool:
    """在去除字符串字面量后检测是否已有 LIMIT，避免重复包装。"""
    scan = _strip_string_literals_for_scan(sql)
    return bool(re.search(r"\bLIMIT\b", scan, re.IGNORECASE))


def _sql_text_has_deleted_at_keyword(sql: str) -> bool:
    """若 SQL 文本中已出现 deleted_at（含用户/LLM 显式过滤已删行），则不再自动追加条件。"""
    return bool(re.search(r"\bdeleted_at\b", sql, re.IGNORECASE))


def execute_query_db(sql: str, reason: str) -> Dict[str, Any]:
    """
    校验：必须是 SELECT，仅允许 business / meta schema 引用（正则 + 规则扫描）。
    执行：使用 db_conn()（API 连接池）。
    返回：{"rows": [...], "count": n} 或 {"error": "..."}；
    最多 50 行，超出截断并说明。
    """
    _ = reason  # 供调用方日志或审计；此处仅打 debug
    if reason:
        logger.debug("[tool_executor] query_db reason: %s", reason[:500])
    _prev = (sql or "").strip().replace("\n", " ")[:160]
    logger.info("[executor.query_db] 入口 sql_preview=%s", _prev)
    try:
        validated = _validate_agent_select_sql(sql)
    except ValueError as e:
        logger.warning("[executor.query_db] 出口 status=error detail=%s", e)
        return {"error": str(e)}

    limit_n = _MAX_ROWS + 1

    def _wrap_limit(sql_inner: str) -> str:
        # 内层 SQL 可能含 %s 等占位符；若再用参数化 LIMIT %s，会与 psycopg2 参数个数冲突。
        # 已有 LIMIT 时直接执行原句；否则用常量拼接 LIMIT，整句无占位符。
        if _sql_has_limit_clause(sql_inner):
            return sql_inner
        return f"SELECT * FROM ({sql_inner}) AS _agent_q LIMIT {limit_n}"

    applied_deleted_filter = False
    sql_for_exec = validated
    if not _sql_text_has_deleted_at_keyword(validated):
        sql_for_exec = (
            f"SELECT * FROM ({validated}) AS _agent_q WHERE deleted_at IS NULL"
        )
        applied_deleted_filter = True
        logger.info("[tool_executor] query_db: 自动追加 deleted_at IS NULL 过滤")

    final_sql = _wrap_limit(sql_for_exec)

    rows: List[Any]
    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(final_sql)
                rows = cur.fetchall()
    except Exception as e:
        if applied_deleted_filter:
            logger.warning(
                "[tool_executor] query_db: 带 deleted_at 过滤执行失败，回退为原查询: %s",
                e,
                exc_info=True,
            )
            final_sql = _wrap_limit(validated)
            try:
                with db_conn() as conn:
                    with conn.cursor(
                        cursor_factory=psycopg2.extras.RealDictCursor
                    ) as cur:
                        cur.execute(final_sql)
                        rows = cur.fetchall()
            except Exception as e2:
                logger.exception("[tool_executor] query_db 执行失败: %s", e2)
                logger.warning(
                    "[executor.query_db] 出口 status=error detail=%s", e2
                )
                return {"error": f"执行失败：{e2}"}
        else:
            logger.exception("[tool_executor] query_db 执行失败: %s", e)
            logger.warning("[executor.query_db] 出口 status=error detail=%s", e)
            return {"error": f"执行失败：{e}"}

    truncated = len(rows) > _MAX_ROWS
    if truncated:
        rows = rows[:_MAX_ROWS]

    out_rows: List[Dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        for k, v in d.items():
            if isinstance(v, (date, datetime)):
                d[k] = v.isoformat()
            elif isinstance(v, Decimal):
                d[k] = str(v)
            elif isinstance(v, (bytes, memoryview)):
                d[k] = str(v)[:200]
        out_rows.append(d)

    result: Dict[str, Any] = {
        "rows": out_rows,
        "count": len(out_rows),
    }
    if truncated:
        result["truncated"] = True
        result["note"] = f"结果已超过 {_MAX_ROWS} 行，已截断为前 {_MAX_ROWS} 行。"
    logger.info(
        "[executor.query_db] 出口 status=success rows=%s truncated=%s",
        len(out_rows),
        truncated,
    )
    return result


def execute_write_entity(
    table_name: str,
    fields: Dict[str, Any],
    explanation: str,
    frame_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    复用 collecting.build_col_vals_from_fields 的白名单与字段校验；不立即写库。
    返回 pending_confirm 与 preview，供用户确认后由 main.py 等触发实际 INSERT。
    """
    from engine.collecting import (  # 延迟导入，避免与 collecting 循环依赖
        build_col_vals_from_fields,
        validate_write_entity_fields_meta,
    )

    if not isinstance(fields, dict):
        return {"error": "fields 必须是对象（字段名到值）。"}

    tn = (table_name or "").strip()
    if tn == "contracts":
        logger.warning(
            "write_entity 被拦截: table=%s, 合同应使用 submit_contract",
            tn,
        )
        return {
            "error": "合同请使用 submit_contract 工具，不要用 write_entity",
        }

    fields = normalize_entity_fields(tn, dict(fields))

    logger.info(
        "execute_write_entity: table=%s fields=%s",
        table_name,
        list(fields.keys()),
    )

    meta_err = validate_write_entity_fields_meta(table_name, fields)
    if meta_err:
        return {"error": meta_err}

    col_vals, err = build_col_vals_from_fields(table_name, fields)
    if err:
        return {"error": err}

    row_dict = {c: v for c, v in col_vals}
    preview = {
        "table_name": (table_name or "").strip(),
        "columns": [c for c, _ in col_vals],
        "row": row_dict,
    }
    out: Dict[str, Any] = {
        "status": "pending_confirm",
        "preview": preview,
        "explanation": (explanation or "").strip(),
    }
    if frame_id is not None:
        out["frame_id"] = int(frame_id)
    return out


def execute_update_entity(
    table_name: str,
    record_id: Any,
    fields: Dict[str, Any],
    explanation: str,
    frame_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    校验表名、字段白名单；不执行 UPDATE。
    record_id 必须以 frame.context_snapshot.verified_record_id 为准（由 query_db 唯一一行写入），
    不信任 LLM 传入的 record_id；无 verified_record_id 时返回 error，迫使 agent 先 query_db。
    """
    from engine.collecting import collect_update_fields_validated, get_frame_verified_record_id

    logger.info(
        "execute_update_entity: LLM 传入完整参数 table_name=%r record_id(原始)=%r "
        "fields=%s explanation=%r frame_id=%r",
        table_name,
        record_id,
        json.dumps(fields, ensure_ascii=False, default=str) if fields is not None else None,
        explanation,
        frame_id,
    )

    if not isinstance(table_name, str) or not table_name.strip():
        return {"error": "缺少 table_name。"}
    if not isinstance(explanation, str):
        return {"error": "explanation 必须是字符串。"}
    if not isinstance(fields, dict):
        return {"error": "fields 必须是对象。"}

    tn_up = (table_name or "").strip()
    if tn_up == "contracts":
        return {
            "error": "合同请使用 update_contract 工具，不要用 update_entity",
        }

    fields = normalize_entity_fields(tn_up, dict(fields))

    rid_llm: Optional[int] = None
    try:
        rid_llm = int(record_id)
    except (TypeError, ValueError):
        pass
    else:
        if rid_llm < 1:
            rid_llm = None

    if frame_id is None:
        return {"error": "update_entity 缺少 frame_id，无法校验目标记录。"}

    verified = get_frame_verified_record_id(int(frame_id))
    if verified is None:
        logger.warning(
            "execute_update_entity: context_snapshot 无 verified_record_id，拒绝执行（LLM 曾传 record_id=%r）",
            record_id,
        )
        return {
            "error": "请先用 query_db 查询目标记录（需返回唯一一行且含 id），再调用 update_entity。",
        }

    rid = verified
    if rid_llm is not None and rid_llm != rid:
        logger.warning(
            "execute_update_entity: 采用 verified_record_id=%s，忽略 LLM record_id=%s",
            rid,
            rid_llm,
        )

    norm, err = collect_update_fields_validated(table_name.strip(), fields)
    if err:
        return {"error": err}
    if not norm:
        return {"error": "没有可更新的字段。"}

    out: Dict[str, Any] = {
        "status": "pending_confirm",
        "action": "update",
        "table_name": table_name.strip(),
        "record_id": rid,
        "preview": {"fields": dict(norm)},
        "explanation": (explanation or "").strip(),
        "pending_update": {
            "table_name": table_name.strip(),
            "record_id": rid,
            "fields": dict(norm),
        },
    }
    if frame_id is not None:
        out["frame_id"] = int(frame_id)
    logger.info(
        "execute_update_entity: 最终 record_id=%s（仅来自 verified_record_id；LLM 曾传 %s）",
        rid,
        rid_llm,
    )
    return out


def _decimal_or_error(val: Any, label: str) -> Tuple[Optional[Decimal], Optional[str]]:
    if val is None:
        return None, f"{label} 不能为空。"
    try:
        d = Decimal(str(val))
    except Exception:
        return None, f"{label} 不是有效数字。"
    return d, None


def execute_submit_contract(
    contract: Any,
    items: Any,
    explanation: str,
    frame_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    校验主表 + 明细预览，不写库；返回 pending_confirm，由 collecting 写入 pending_contract。
    不使用 build_col_vals_from_fields / 简单实体表白名单，合同字段在本函数内显式校验。
    """
    arguments = {
        "contract": contract,
        "items": items,
        "explanation": explanation,
        "frame_id": frame_id,
    }
    logger.info(
        "[executor.submit_contract] 入口 keys=%s",
        list(arguments.keys()),
    )

    def _fail(error_msg: str) -> Dict[str, Any]:
        logger.warning("[executor.submit_contract] 出口 status=error detail=%s", error_msg)
        return {"error": error_msg}

    if not isinstance(contract, dict):
        return _fail("contract 必须是对象。")
    if not isinstance(items, list) or len(items) < 1:
        return _fail("items 必须为非空数组。")

    cid = contract.get("customer_id")
    try:
        cid_int = int(cid)
    except (TypeError, ValueError):
        return _fail("contract.customer_id 必须是整数。")
    if cid_int < 1:
        return _fail("contract.customer_id 无效。")

    cname = contract.get("customer_name")
    if not isinstance(cname, str) or not cname.strip():
        return _fail("contract.customer_name 必填。")

    currency = contract.get("currency")
    if currency is None or (isinstance(currency, str) and not currency.strip()):
        currency = "CNY"
    elif not isinstance(currency, str):
        return _fail("contract.currency 必须是字符串。")
    else:
        currency = currency.strip()

    payment_terms = contract.get("payment_terms")
    if payment_terms is not None and not isinstance(payment_terms, str):
        return _fail("contract.payment_terms 必须是字符串。")

    notes = contract.get("notes")
    if notes is not None and not isinstance(notes, str):
        return _fail("contract.notes 必须是字符串。")

    delivery_date = contract.get("delivery_date")
    signed_date = contract.get("signed_date")
    for lab, v in (
        ("contract.delivery_date", delivery_date),
        ("contract.signed_date", signed_date),
    ):
        if v is not None and not isinstance(v, str):
            return _fail(f"{lab} 必须是字符串（日期）或省略。")

    out_items: List[Dict[str, Any]] = []
    total = Decimal("0")
    for i, raw in enumerate(items):
        if not isinstance(raw, dict):
            return _fail(f"items[{i}] 必须是对象。")
        pname = raw.get("product_name")
        if not isinstance(pname, str) or not pname.strip():
            return _fail(f"items[{i}].product_name 必填。")

        q, err = _decimal_or_error(raw.get("quantity"), f"items[{i}].quantity")
        if err:
            return _fail(err)
        assert q is not None
        if q <= 0:
            return _fail(f"items[{i}].quantity 必须大于 0。")

        up, err2 = _decimal_or_error(raw.get("unit_price"), f"items[{i}].unit_price")
        if err2:
            return _fail(err2)
        assert up is not None
        if up < 0:
            return _fail(f"items[{i}].unit_price 不能为负。")

        amt = q * up
        total += amt

        line: Dict[str, Any] = {
            "product_name": pname.strip(),
            "quantity": float(q),
            "unit_price": float(up),
            "amount": float(amt),
        }
        pc = raw.get("product_code")
        if isinstance(pc, str) and pc.strip():
            line["product_code"] = pc.strip()
        pid = raw.get("product_id")
        if pid is not None:
            try:
                line["product_id"] = int(pid)
            except (TypeError, ValueError):
                return _fail(f"items[{i}].product_id 必须是整数或省略。")
        for opt_key in ("unit", "color", "spec", "notes"):
            ov = raw.get(opt_key)
            if ov is None:
                continue
            if not isinstance(ov, str):
                return _fail(f"items[{i}].{opt_key} 必须是字符串或省略。")
            if ov.strip():
                line[opt_key] = ov.strip()

        out_items.append(line)

    contract_out: Dict[str, Any] = {
        "customer_id": cid_int,
        "customer_name": cname.strip(),
        "currency": currency,
        "total_amount": float(total),
    }
    if isinstance(payment_terms, str) and payment_terms.strip():
        contract_out["payment_terms"] = payment_terms.strip()
    if isinstance(notes, str) and notes.strip():
        contract_out["notes"] = notes.strip()
    if isinstance(delivery_date, str) and delivery_date.strip():
        contract_out["delivery_date"] = delivery_date.strip()
    if isinstance(signed_date, str) and signed_date.strip():
        contract_out["signed_date"] = signed_date.strip()

    contract_preview = dict(contract_out)
    contract_preview["contract_no"] = "（确认后由系统自动生成）"

    preview = {"contract": contract_preview, "items": out_items}
    pending_contract = {
        "contract": dict(contract_out),
        "items": [dict(x) for x in out_items],
    }

    out: Dict[str, Any] = {
        "status": "pending_confirm",
        "action": "create_contract",
        "preview": preview,
        "pending_contract": pending_contract,
        "explanation": (explanation or "").strip() if isinstance(explanation, str) else "",
    }
    if frame_id is not None:
        out["frame_id"] = int(frame_id)
    logger.info(
        "[executor.submit_contract] 出口 status=pending_confirm total=%s items=%s",
        float(total),
        len(out_items),
    )
    return out


_CONTRACT_HEADER_PROTECTED = frozenset(
    {
        "id",
        "contract_no",
        "created_at",
        "updated_at",
        "deleted_at",
        "total_amount",
    }
)


def execute_update_contract(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """
    合同修改预览：校验 + 构造 snapshot_before/after，不写库。
    contract_id 以 frame.context_snapshot.verified_record_id 为准（须先 query_db 单行命中）。
    """
    from engine.collecting import _row_jsonable, get_frame_verified_record_id

    frame_id = arguments.get("frame_id")
    if frame_id is None:
        return {"error": "update_contract 缺少 frame_id。"}
    try:
        fid = int(frame_id)
    except (TypeError, ValueError):
        return {"error": "frame_id 无效。"}

    verified = get_frame_verified_record_id(fid)
    if verified is None:
        return {
            "error": "请先用 query_db 查询目标合同（返回唯一一行且含 id），再调用 update_contract。",
        }
    contract_id = int(verified)
    raw_arg_id = arguments.get("contract_id")
    try:
        arg_id = int(raw_arg_id) if raw_arg_id is not None else None
    except (TypeError, ValueError):
        arg_id = None
    if arg_id is not None and arg_id != contract_id:
        logger.warning(
            "execute_update_contract: 采用 verified_record_id=%s，忽略传入 contract_id=%s",
            contract_id,
            arg_id,
        )

    reason = (arguments.get("reason") or "").strip()
    if not reason:
        return {"error": "必须提供变更原因 reason。"}

    header_updates = arguments.get("header_updates") or {}
    if not isinstance(header_updates, dict):
        header_updates = {}
    items_add = arguments.get("items_add") or []
    items_update = arguments.get("items_update") or []
    items_remove = arguments.get("items_remove") or []
    if not isinstance(items_add, list):
        items_add = []
    if not isinstance(items_update, list):
        items_update = []
    if not isinstance(items_remove, list):
        items_remove = []

    hu_filt: Dict[str, Any] = {}
    for k, v in header_updates.items():
        if not isinstance(k, str) or not k.strip():
            continue
        kn = k.strip()
        if kn in _CONTRACT_HEADER_PROTECTED:
            continue
        hu_filt[kn] = v

    if not hu_filt and not items_add and not items_update and not items_remove:
        return {"error": "没有任何变更内容。"}

    with db_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT * FROM business.contracts
                WHERE id = %s AND deleted_at IS NULL
                """,
                (contract_id,),
            )
            contract_row = cur.fetchone()
            if not contract_row:
                return {"error": f"合同 id={contract_id} 不存在或已删除。"}
            cur.execute(
                """
                SELECT * FROM business.contract_items
                WHERE contract_id = %s AND deleted_at IS NULL
                ORDER BY id
                """,
                (contract_id,),
            )
            items_rows = cur.fetchall()

    snap_c = _row_jsonable(dict(contract_row))
    snap_items = [_row_jsonable(dict(r)) for r in items_rows]
    snapshot_before = {"contract": snap_c, "items": snap_items}

    after_contract = dict(contract_row)
    for k, v in hu_filt.items():
        if k in after_contract:
            after_contract[k] = v

    after_items: List[Dict[str, Any]] = [_row_jsonable(dict(r)) for r in items_rows]

    remove_ids = set()
    for x in items_remove:
        try:
            remove_ids.add(int(x))
        except (TypeError, ValueError):
            continue
    after_items = [it for it in after_items if int(it.get("id") or 0) not in remove_ids]

    update_map: Dict[int, Dict[str, Any]] = {}
    for u in items_update:
        if not isinstance(u, dict):
            continue
        iid = u.get("item_id")
        try:
            iid_int = int(iid)
        except (TypeError, ValueError):
            continue
        update_map[iid_int] = u

    for it in after_items:
        try:
            iid = int(it.get("id") or 0)
        except (TypeError, ValueError):
            continue
        if iid not in update_map:
            continue
        ch = update_map[iid]
        for k, v in ch.items():
            if k == "item_id":
                continue
            it[k] = v
        try:
            q = Decimal(str(it.get("quantity", 0)))
            p = Decimal(str(it.get("unit_price", 0)))
            it["amount"] = float(q * p)
        except Exception:
            pass

    for new_item in items_add:
        if not isinstance(new_item, dict):
            continue
        try:
            q = Decimal(str(new_item.get("quantity", 0)))
            p = Decimal(str(new_item.get("unit_price", 0)))
            amt = float(q * p)
        except Exception:
            amt = 0.0
        line = dict(new_item)
        line["amount"] = amt
        line["_is_new"] = True
        after_items.append(_row_jsonable(line))

    try:
        total_after = Decimal("0")
        for it in after_items:
            total_after += Decimal(str(it.get("amount") or 0))
        after_contract["total_amount"] = float(total_after)
    except Exception:
        after_contract["total_amount"] = snap_c.get("total_amount")

    snapshot_after = {
        "contract": _row_jsonable(after_contract),
        "items": after_items,
    }

    diff_summary: Dict[str, Any] = {
        "header_keys": list(hu_filt.keys()),
        "items_add": len([x for x in items_add if isinstance(x, dict)]),
        "items_update": len(update_map),
        "items_remove": len(remove_ids),
    }

    pending_contract_update = {
        "contract_id": contract_id,
        "reason": reason,
        "header_updates": hu_filt,
        "items_add": items_add if isinstance(items_add, list) else [],
        "items_update": items_update if isinstance(items_update, list) else [],
        "items_remove": list(remove_ids),
        "snapshot_before": snapshot_before,
        "snapshot_after": snapshot_after,
        "diff_summary": diff_summary,
    }

    cno = contract_row.get("contract_no")
    preview = {
        "contract_no": cno,
        "reason": reason,
        "snapshot_before": snapshot_before,
        "snapshot_after": snapshot_after,
        "diff_summary": diff_summary,
    }

    return {
        "status": "pending_confirm",
        "action": "update_contract",
        "preview": preview,
        "pending_contract_update": pending_contract_update,
        "explanation": reason,
        "frame_id": fid,
    }


def execute_delete_entity(
    table_name: str,
    record_id: Any,
    explanation: str,
    frame_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    校验表名在白名单；不执行 DELETE/UPDATE。
    record_id 仅以 frame.context_snapshot.verified_record_id 为准。
    """
    from engine.collecting import _load_delete_entity_whitelist, get_frame_verified_record_id

    logger.info(
        "execute_delete_entity: table_name=%r record_id(原始)=%r explanation=%r frame_id=%r",
        table_name,
        record_id,
        explanation,
        frame_id,
    )

    if not isinstance(table_name, str) or not table_name.strip():
        return {"error": "缺少 table_name。"}
    if not isinstance(explanation, str):
        return {"error": "explanation 必须是字符串。"}

    tn = table_name.strip()
    allowed = _load_delete_entity_whitelist()
    if tn not in allowed:
        return {"error": f"表 {tn!r} 不可通过 delete_entity 删除。"}

    if tn == "contracts" and not (explanation or "").strip():
        return {"error": "删除合同必须在 explanation 中说明原因。"}

    rid_llm: Optional[int] = None
    try:
        rid_llm = int(record_id)
    except (TypeError, ValueError):
        pass
    else:
        if rid_llm < 1:
            rid_llm = None

    if frame_id is None:
        return {"error": "delete_entity 缺少 frame_id，无法校验目标记录。"}

    verified = get_frame_verified_record_id(int(frame_id))
    if verified is None:
        logger.warning(
            "execute_delete_entity: 无 verified_record_id，拒绝（LLM 曾传 record_id=%r）",
            record_id,
        )
        return {
            "error": "请先用 query_db 查询目标记录（需返回唯一一行且含 id），再调用 delete_entity。",
        }

    rid = verified
    if rid_llm is not None and rid_llm != rid:
        logger.warning(
            "execute_delete_entity: 采用 verified_record_id=%s，忽略 LLM record_id=%s",
            rid,
            rid_llm,
        )

    out: Dict[str, Any] = {
        "status": "pending_confirm",
        "action": "delete",
        "table_name": tn,
        "record_id": rid,
        "preview": {
            "table_name": tn,
            "record_id": rid,
            "operation": "soft_delete",
        },
        "explanation": (explanation or "").strip(),
        "pending_delete": {
            "table_name": tn,
            "record_id": rid,
            "reason": (explanation or "").strip(),
        },
    }
    if frame_id is not None:
        out["frame_id"] = int(frame_id)
    return out


def execute_submit(arguments: Dict[str, Any], frame_id: Optional[int]) -> Dict[str, Any]:
    """V15-D：所有意图统一由 workflow_steps 驱动 pending_submit.writes。"""
    from engine import pending_submit_v15b

    return pending_submit_v15b.execute_submit_with_arguments(arguments, frame_id)


def execute_tool_call(name: str, arguments: Any) -> Dict[str, Any]:
    """
    根据工具名与参数（JSON 对象或 JSON 字符串）分发执行，供编排层统一调用。
    """
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments) if arguments.strip() else {}
        except json.JSONDecodeError as e:
            return {"error": f"工具参数 JSON 无效：{e}"}
    if not isinstance(arguments, dict):
        return {"error": "工具参数必须是 JSON 对象。"}

    logger.info("[executor.分发] name=%s args_keys=%s", name, list(arguments.keys()))

    if name == "query_db":
        sql = arguments.get("sql")
        reason = arguments.get("reason") or ""
        if not isinstance(sql, str) or not sql.strip():
            return {"error": "缺少 sql。"}
        if not isinstance(reason, str) or not reason.strip():
            return {"error": "缺少 reason。"}
        return execute_query_db(sql.strip(), reason.strip())

    if name == "submit":
        frame_id = arguments.get("frame_id")
        fid: Optional[int] = None
        if frame_id is not None:
            try:
                fid = int(frame_id)
            except (TypeError, ValueError):
                return {"error": "frame_id 无效。"}
        return execute_submit(arguments, fid)

    # ── 以下工具名已废弃（V15 起统一为 submit），保留分支便于旧会话/测试兼容 ──
    if name == "write_entity":
        table_name = arguments.get("table_name")
        fields = arguments.get("fields")
        explanation = arguments.get("explanation") or ""
        frame_id = arguments.get("frame_id")
        if not isinstance(table_name, str) or not table_name.strip():
            return {"error": "缺少 table_name。"}
        if not isinstance(explanation, str):
            return {"error": "explanation 必须是字符串。"}
        fid2: Optional[int] = None
        if frame_id is not None:
            try:
                fid2 = int(frame_id)
            except (TypeError, ValueError):
                return {"error": "frame_id 无效。"}
        return execute_submit(
            {
                "data": {
                    "table_name": table_name.strip(),
                    "fields": fields if isinstance(fields, dict) else {},
                    "explanation": explanation.strip(),
                },
                "frame_id": fid2,
            },
            fid2,
        )

    if name == "update_entity":
        table_name = arguments.get("table_name")
        record_id = arguments.get("record_id")
        fields = arguments.get("fields")
        explanation = arguments.get("explanation") or ""
        frame_id = arguments.get("frame_id")
        if not isinstance(table_name, str) or not table_name.strip():
            return {"error": "缺少 table_name。"}
        if not isinstance(explanation, str):
            return {"error": "explanation 必须是字符串。"}
        fid3: Optional[int] = None
        if frame_id is not None:
            try:
                fid3 = int(frame_id)
            except (TypeError, ValueError):
                return {"error": "frame_id 无效。"}
        return execute_submit(
            {
                "data": {
                    "table_name": table_name.strip(),
                    "record_id": record_id,
                    "fields": fields if isinstance(fields, dict) else {},
                    "explanation": explanation.strip(),
                },
                "frame_id": fid3,
            },
            fid3,
        )

    if name == "delete_entity":
        table_name = arguments.get("table_name")
        record_id = arguments.get("record_id")
        explanation = arguments.get("explanation") or ""
        frame_id = arguments.get("frame_id")
        if not isinstance(table_name, str) or not table_name.strip():
            return {"error": "缺少 table_name。"}
        if not isinstance(explanation, str):
            return {"error": "explanation 必须是字符串。"}
        fid4: Optional[int] = None
        if frame_id is not None:
            try:
                fid4 = int(frame_id)
            except (TypeError, ValueError):
                return {"error": "frame_id 无效。"}
        return execute_submit(
            {
                "data": {
                    "table_name": table_name.strip(),
                    "record_id": record_id,
                    "explanation": explanation.strip(),
                },
                "frame_id": fid4,
            },
            fid4,
        )

    if name == "submit_contract":
        contract = arguments.get("contract")
        items = arguments.get("items")
        explanation = arguments.get("explanation") or ""
        frame_id = arguments.get("frame_id")
        if not isinstance(explanation, str):
            return {"error": "explanation 必须是字符串。"}
        fid5: Optional[int] = None
        if frame_id is not None:
            try:
                fid5 = int(frame_id)
            except (TypeError, ValueError):
                return {"error": "frame_id 无效。"}
        if fid5 is None:
            return {"error": "submit_contract 缺少 frame_id。"}
        return execute_submit(
            {
                "data": {"contract": contract, "items": items, "explanation": explanation.strip()},
                "frame_id": fid5,
            },
            fid5,
        )

    if name == "update_contract":
        frame_id = arguments.get("frame_id")
        fid6: Optional[int] = None
        if frame_id is not None:
            try:
                fid6 = int(frame_id)
            except (TypeError, ValueError):
                return {"error": "frame_id 无效。"}
        arg = dict(arguments)
        arg["frame_id"] = fid6
        data = {k: v for k, v in arg.items() if k != "frame_id"}
        return execute_submit({"data": data, "frame_id": fid6}, fid6)

    if name == "ask_user":
        question = arguments.get("question") or ""
        hint = arguments.get("hint") or ""
        if not isinstance(question, str) or not question.strip():
            return {"error": "缺少 question。"}
        return {
            "status": "pending_user_reply",
            "question": question.strip(),
            "hint": (hint.strip() if isinstance(hint, str) else "") or None,
        }

    return {"error": f"未知工具：{name}"}
