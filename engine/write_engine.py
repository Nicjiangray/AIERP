"""
受控写入：新建 / 更新 / 软删除；仅白名单表与元数据字段，禁止原始 SQL 字符串。
支持 preview_write：仅校验并生成中文摘要，不执行 SQL。

.. deprecated:: V5.2
   新编排与统一 SQL 引擎请使用 `engine.sql_engine`（preview_sql / confirm_execute_sql）。
   本模块保留供尚未迁移的调用方。
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set, Tuple

from psycopg2 import sql

from engine.query_engine import (
    _fail,
    _load_fields_for_table,
    _load_whitelist,
    _resolve_table_schema,
    _validate_identifier,
)

# data 中禁止由 LLM 传入的系统字段（出现则整单拒绝）
_BLOCKED_IN_DATA = frozenset(
    {"id", "created_at", "updated_at", "deleted_at", "delete_reason"}
)

_OPS = frozenset({"create", "update", "delete"})


def _ok(operation: str, table: str, row_id: int) -> Dict[str, Any]:
    return {
        "success": True,
        "operation": operation,
        "table": table,
        "id": row_id,
    }


def _table_columns(cur, schema: str, table: str) -> Set[str]:
    """物理表实际列名（小写）。"""
    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
        """,
        (schema, table),
    )
    return {r[0] for r in cur.fetchall()}


def _validate_data_keys(
    data: Any,
    allowed_meta: Set[str],
    *,
    allow_empty: bool = False,
) -> Optional[Dict[str, Any]]:
    if not isinstance(data, dict):
        return None
    if not allow_empty and len(data) == 0:
        return None
    for k in data.keys():
        if not isinstance(k, str):
            return None
        e = _validate_identifier(k, "字段名")
        if e:
            return None
        if k in _BLOCKED_IN_DATA:
            return None
        if k not in allowed_meta:
            return None
    return dict(data)


def _parse_id(payload: dict) -> Optional[int]:
    raw = payload.get("id")
    if raw is None:
        return None
    try:
        i = int(raw)
    except (TypeError, ValueError):
        return None
    if i < 1:
        return None
    return i


def _parse_and_validate_write(
    cur, payload: dict
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """
    校验 write 请求，供 execute_write 与 preview_write 共用。

    成功：返回 (ctx, None)，ctx 含 op / table / schema / allowed_meta / physical_cols /
          cleaned（create/update）/ rid（update/delete）/ reason（delete）。
    失败：返回 (None, err)，err 为 {\"success\": False, \"error\": \"...\"}。
    """
    op = payload.get("operation")
    if not op or not isinstance(op, str):
        return None, _fail("缺少 operation 或类型错误")
    op = op.strip().lower()
    if op not in _OPS:
        return None, _fail(f"operation 必须是 create、update、delete 之一，当前为「{op}」")

    table = payload.get("table")
    if not table or not isinstance(table, str):
        return None, _fail("缺少 table 或类型错误")
    err = _validate_identifier(table, "表名")
    if err:
        return None, _fail(err)

    whitelist = _load_whitelist(cur)
    if table not in whitelist:
        return None, _fail(f"表「{table}」不在可写入白名单内（include_in_domain_model）")

    allowed_meta = _load_fields_for_table(cur, table)
    if not allowed_meta:
        return None, _fail(f"元数据中未找到表「{table}」的字段定义")

    schema = _resolve_table_schema(cur, table)
    if not schema:
        return None, _fail(f"数据库中不存在物理表「{table}」")

    physical_cols = _table_columns(cur, schema, table)

    ctx: Dict[str, Any] = {
        "op": op,
        "table": table,
        "schema": schema,
        "allowed_meta": allowed_meta,
        "physical_cols": physical_cols,
    }

    if op == "create":
        data = payload.get("data")
        cleaned = _validate_data_keys(data, allowed_meta, allow_empty=False)
        if cleaned is None:
            return None, _fail(
                "create 需要非空 data 对象，且字段须在元数据中登记，不得包含系统字段 id/created_at/updated_at/deleted_at/delete_reason"
            )
        for col in sorted(cleaned.keys()):
            if col not in physical_cols:
                return None, _fail(f"物理表不存在列「{col}」，请同步元数据")
        if not cleaned:
            return None, _fail("没有可写入的列")
        ctx["cleaned"] = cleaned
        return ctx, None

    if op == "update":
        rid = _parse_id(payload)
        if rid is None:
            return None, _fail("update 必须提供正整数 id")
        data = payload.get("data")
        cleaned = _validate_data_keys(data, allowed_meta, allow_empty=False)
        if cleaned is None:
            return None, _fail(
                "update 需要非空 data，且字段须在元数据中登记，不得包含系统字段"
            )
        for col in sorted(cleaned.keys()):
            if col not in physical_cols:
                return None, _fail(f"物理表不存在列「{col}」")
        set_parts_count = len(cleaned)
        if "updated_at" in physical_cols and "updated_at" in allowed_meta:
            set_parts_count += 1
        if set_parts_count == 0:
            return None, _fail("没有可更新的列")
        ctx["cleaned"] = cleaned
        ctx["rid"] = rid
        return ctx, None

    # delete
    rid = _parse_id(payload)
    if rid is None:
        return None, _fail("delete 必须提供正整数 id")
    reason = payload.get("reason")
    if reason is None or not isinstance(reason, str) or not reason.strip():
        return None, _fail("delete 必须提供非空字符串 reason（删除原因）")
    if "deleted_at" not in physical_cols:
        return None, _fail("该表无 deleted_at 列，不支持软删除")
    ctx["rid"] = rid
    ctx["reason"] = reason.strip()
    return ctx, None


def execute_write(payload: dict, db_conn) -> dict:
    """
    根据结构化 payload 执行 INSERT / UPDATE / 软删除，不使用用户提供的原始 SQL。

    db_conn: 已打开的 psycopg2 连接；本函数不 commit，由调用方提交或回滚事务。
    """
    if not isinstance(payload, dict):
        return _fail("payload 必须是对象")

    with db_conn.cursor() as cur:
        ctx, err = _parse_and_validate_write(cur, payload)
        if err:
            return err
        op = ctx["op"]
        table = ctx["table"]
        schema = ctx["schema"]
        allowed_meta = ctx["allowed_meta"]
        physical_cols = ctx["physical_cols"]

        if op == "create":
            return _execute_create(
                cur, schema, table, ctx["cleaned"], physical_cols
            )
        if op == "update":
            return _execute_update(
                cur,
                schema,
                table,
                ctx["cleaned"],
                ctx["rid"],
                allowed_meta,
                physical_cols,
            )
        return _execute_delete(
            cur,
            schema,
            table,
            ctx["rid"],
            ctx["reason"],
            allowed_meta,
            physical_cols,
        )


def _load_table_label(cur, table: str) -> str:
    """表中文展示名：table_cn，否则 description 摘要，否则表名。"""
    cur.execute(
        """
        SELECT COALESCE(NULLIF(TRIM(table_cn), ''), ''),
               COALESCE(NULLIF(TRIM(description), ''), '')
        FROM meta.meta_tables
        WHERE table_name = %s
          AND (deleted_at IS NULL)
        LIMIT 1
        """,
        (table,),
    )
    row = cur.fetchone()
    if not row:
        return table
    tc, desc = row[0] or "", row[1] or ""
    if tc:
        return tc
    if desc:
        return desc.strip()[:40] + ("…" if len(desc) > 40 else "")
    return table


def _load_field_labels(cur, table: str) -> Dict[str, str]:
    """field_name -> 展示中文（field_cn，缺省 field_name）。"""
    cur.execute(
        """
        SELECT field_name,
               COALESCE(NULLIF(TRIM(field_cn), ''), field_name)
        FROM meta.meta_fields
        WHERE table_name = %s
          AND (deleted_at IS NULL)
        """,
        (table,),
    )
    return {r[0]: r[1] for r in cur.fetchall()}


def _format_value(v: Any) -> str:
    if v is None:
        return "空"
    if isinstance(v, Decimal):
        return str(v)
    if hasattr(v, "isoformat") and callable(getattr(v, "isoformat")):
        try:
            return v.isoformat()
        except Exception:
            pass
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)[:200]
    return str(v)


def _build_write_summary(cur, ctx: Dict[str, Any]) -> str:
    """根据校验通过的 ctx 生成面向用户的中文摘要。"""
    table = ctx["table"]
    op = ctx["op"]
    table_cn = _load_table_label(cur, table)
    labels = _load_field_labels(cur, table)

    def lbl(fn: str) -> str:
        return labels.get(fn, fn)

    if op == "create":
        cleaned = ctx["cleaned"]
        parts = [f"{lbl(k)}：{_format_value(v)}" for k, v in sorted(cleaned.items())]
        return f"即将新建{table_cn}：" + "，".join(parts)

    if op == "update":
        rid = ctx["rid"]
        cleaned = ctx["cleaned"]
        parts = [f"{lbl(k)} 改为 {_format_value(v)}" for k, v in sorted(cleaned.items())]
        return f"即将修改{table_cn} ID={rid}：" + "，".join(parts)

    reason = ctx["reason"]
    rid = ctx["rid"]
    return f"即将删除{table_cn} ID={rid}，原因：{reason}"


def preview_write(payload: dict, db_conn) -> dict:
    """
    不执行 SQL：仅做与 execute_write 相同的校验，并生成中文摘要。
    成功时原样带回 payload（引用副本，避免调用方修改）。
    """
    if not isinstance(payload, dict):
        return _fail("payload 必须是对象")

    with db_conn.cursor() as cur:
        ctx, err = _parse_and_validate_write(cur, payload)
        if err:
            return err
        summary = _build_write_summary(cur, ctx)

    out = dict(payload)
    return {
        "success": True,
        "stage": "preview",
        "operation": ctx["op"],
        "table": ctx["table"],
        "summary": summary,
        "payload": out,
    }


def _execute_create(
    cur,
    schema: str,
    table: str,
    cleaned: Dict[str, Any],
    physical_cols: Set[str],
) -> Dict[str, Any]:
    insert_cols = sorted(cleaned.keys())
    values = [cleaned[c] for c in insert_cols]

    cols_sql = sql.SQL(", ").join(sql.Identifier(c) for c in insert_cols)
    ph = sql.SQL(", ").join(sql.Placeholder() for _ in insert_cols)
    stmt = sql.SQL("INSERT INTO {}.{} ({}) VALUES ({}) RETURNING id").format(
        sql.Identifier(schema),
        sql.Identifier(table),
        cols_sql,
        ph,
    )
    cur.execute(stmt, values)
    row = cur.fetchone()
    if not row:
        return _fail("插入失败：未返回 id")
    new_id = int(row[0])
    return _ok("create", table, new_id)


def _execute_update(
    cur,
    schema: str,
    table: str,
    cleaned: Dict[str, Any],
    rid: int,
    allowed_meta: Set[str],
    physical_cols: Set[str],
) -> Dict[str, Any]:
    set_parts: List[Any] = []
    params: List[Any] = []

    for col in sorted(cleaned.keys()):
        set_parts.append(sql.SQL("{} = {}").format(sql.Identifier(col), sql.Placeholder()))
        params.append(cleaned[col])

    if "updated_at" in physical_cols and "updated_at" in allowed_meta:
        set_parts.append(
            sql.SQL("{} = NOW()").format(sql.Identifier("updated_at"))
        )

    if not set_parts:
        return _fail("没有可更新的列")

    where_extra = sql.SQL("")
    if "deleted_at" in physical_cols:
        where_extra = sql.SQL(" AND {} IS NULL").format(sql.Identifier("deleted_at"))

    stmt = sql.SQL("UPDATE {}.{} SET {} WHERE {} = {}{}").format(
        sql.Identifier(schema),
        sql.Identifier(table),
        sql.SQL(", ").join(set_parts),
        sql.Identifier("id"),
        sql.Placeholder(),
        where_extra,
    )
    params.append(rid)
    cur.execute(stmt, params)
    if cur.rowcount != 1:
        return _fail("更新失败：记录不存在或已删除")
    return _ok("update", table, rid)


def _execute_delete(
    cur,
    schema: str,
    table: str,
    rid: int,
    reason: str,
    allowed_meta: Set[str],
    physical_cols: Set[str],
) -> Dict[str, Any]:
    set_fragments: List[sql.SQL] = [
        sql.SQL("{} = NOW()").format(sql.Identifier("deleted_at"))
    ]
    params: List[Any] = []

    if "delete_reason" in physical_cols:
        if "delete_reason" not in allowed_meta:
            return _fail("元数据未登记 delete_reason，无法写入删除原因")
        set_fragments.append(
            sql.SQL("{} = {}").format(
                sql.Identifier("delete_reason"), sql.Placeholder()
            )
        )
        params.append(reason)

    where_extra = sql.SQL("")
    if "deleted_at" in physical_cols:
        where_extra = sql.SQL(" AND {} IS NULL").format(sql.Identifier("deleted_at"))

    stmt = sql.SQL("UPDATE {}.{} SET {} WHERE {} = {}{}").format(
        sql.Identifier(schema),
        sql.Identifier(table),
        sql.SQL(", ").join(set_fragments),
        sql.Identifier("id"),
        sql.Placeholder(),
        where_extra,
    )
    params.append(rid)

    cur.execute(stmt, params)
    if cur.rowcount != 1:
        return _fail("软删除失败：记录不存在或已被删除")
    return _ok("delete", table, rid)
