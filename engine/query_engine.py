"""
受控查询：仅允许白名单表与元数据登记字段，禁止原始 SQL 字符串。

.. deprecated:: V5.2
   新编排与统一 SQL 引擎请使用 `engine.sql_engine`（execute_sql / preview_sql）。
   本模块保留供尚未迁移的调用方。
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set

from psycopg2 import sql

# 允许的 filters 操作符 → 内部处理类别
_OPS = frozenset(
    {"eq", "neq", "contains", "gt", "lt", "gte", "lte", "in", "is_null"}
)

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_DEFAULT_LIMIT = 20
_MAX_LIMIT = 100


def _fail(msg: str) -> Dict[str, Any]:
    return {"success": False, "error": msg}


def _ok(table: str, rows: List[Dict[str, Any]], count: int) -> Dict[str, Any]:
    return {"success": True, "table": table, "count": count, "rows": rows}


def _validate_identifier(name: str, kind: str) -> Optional[str]:
    """校验标识符，非法则返回中文错误信息。"""
    if not name or not isinstance(name, str):
        return f"{kind} 不能为空"
    if not _IDENT.match(name):
        return f"{kind}「{name}」包含非法字符，仅允许字母、数字与下划线且不以数字开头"
    return None


def _jsonable_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """将单行中的日期、Decimal 等转为可 JSON 序列化的值。"""
    out: Dict[str, Any] = {}
    for k, v in row.items():
        if isinstance(v, Decimal):
            out[k] = float(v)
        elif hasattr(v, "isoformat") and callable(getattr(v, "isoformat")):
            out[k] = v.isoformat() if v is not None else None
        else:
            out[k] = v
    return out


def _load_whitelist(cur) -> Set[str]:
    """include_in_domain_model = true 的表名集合。"""
    cur.execute(
        """
        SELECT table_name
        FROM meta.meta_tables
        WHERE deleted_at IS NULL
          AND include_in_domain_model = true
        """
    )
    return {r[0] for r in cur.fetchall()}


def _load_fields_for_table(cur, table: str) -> Set[str]:
    """某表在 meta_fields 中登记的全部字段名。"""
    cur.execute(
        """
        SELECT field_name
        FROM meta.meta_fields
        WHERE table_name = %s
          AND (deleted_at IS NULL)
        """,
        (table,),
    )
    return {r[0] for r in cur.fetchall()}


def _resolve_table_schema(cur, table: str) -> Optional[str]:
    """从 information_schema 解析物理表所在 schema（同名时优先 business）。"""
    cur.execute(
        """
        SELECT table_schema
        FROM information_schema.tables
        WHERE table_name = %s
          AND table_type = 'BASE TABLE'
        """,
        (table,),
    )
    rows = [r[0] for r in cur.fetchall()]
    if not rows:
        return None
    if "business" in rows:
        return "business"
    return rows[0]


def execute_query(payload: dict, db_conn) -> dict:
    """
    根据结构化 payload 拼装参数化 SELECT，不使用任何用户提供的原始 SQL。

    payload 约定：
        table: str，必填，须在 meta_tables 白名单（include_in_domain_model）内。
        fields: list[str] | 省略，字段须存在于 meta.meta_fields；省略则选取该表全部登记字段。
        filters: list[dict]，每项含 field、op，部分含 value。
        limit: int，可选，默认 20，最大 100。

    db_conn: 已打开的 psycopg2 连接（由调用方在 with 中传入）。
    """
    if not isinstance(payload, dict):
        return _fail("payload 必须是对象")

    table = payload.get("table")
    if not table or not isinstance(table, str):
        return _fail("缺少参数 table 或类型错误")

    err = _validate_identifier(table, "表名")
    if err:
        return _fail(err)

    limit = payload.get("limit", _DEFAULT_LIMIT)
    if limit is None:
        limit = _DEFAULT_LIMIT
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        return _fail("limit 必须是整数")
    if limit < 1:
        return _fail("limit 必须至少为 1")
    if limit > _MAX_LIMIT:
        return _fail(f"limit 最大为 {_MAX_LIMIT}")

    fields_raw = payload.get("fields")
    if fields_raw is None:
        fields_list: Optional[List[str]] = None
    elif isinstance(fields_raw, list):
        fields_list = fields_raw
    else:
        return _fail("fields 必须是数组或省略")

    if fields_list is not None:
        if any(x == "*" or x == "`*`" for x in fields_list):
            return _fail("不允许使用通配符 *，请显式列出字段或省略 fields")
        for fn in fields_list:
            e = _validate_identifier(str(fn), "字段名")
            if e:
                return _fail(e)

    filters_raw = payload.get("filters")
    if filters_raw is None:
        filters: List[Dict[str, Any]] = []
    elif isinstance(filters_raw, list):
        filters = filters_raw
    else:
        return _fail("filters 必须是数组")

    with db_conn.cursor() as cur:
        whitelist = _load_whitelist(cur)
        if table not in whitelist:
            return _fail(f"表「{table}」不在可查询白名单内（include_in_domain_model）")

        allowed_fields = _load_fields_for_table(cur, table)
        if not allowed_fields:
            return _fail(f"元数据中未找到表「{table}」的字段定义")

        if fields_list is None or len(fields_list) == 0:
            select_fields = sorted(allowed_fields)
        else:
            for fn in fields_list:
                if fn not in allowed_fields:
                    return _fail(f"字段「{fn}」不在表「{table}」的元数据定义中")
            select_fields = list(fields_list)

        schema = _resolve_table_schema(cur, table)
        if not schema:
            return _fail(f"数据库中不存在物理表「{table}」")

        # 解析 filters
        conditions: List[sql.Composable] = []
        params: List[Any] = []

        for i, flt in enumerate(filters):
            if not isinstance(flt, dict):
                return _fail(f"filters[{i}] 必须是对象")
            field = flt.get("field")
            op = flt.get("op")
            if not field or not isinstance(field, str):
                return _fail(f"filters[{i}] 缺少 field")
            e = _validate_identifier(field, "字段名")
            if e:
                return _fail(e)
            if field not in allowed_fields:
                return _fail(f"过滤字段「{field}」不在表「{table}」的元数据定义中")
            if not op or not isinstance(op, str):
                return _fail(f"filters[{i}] 缺少 op")
            op = op.strip().lower()
            if op not in _OPS:
                return _fail(
                    f"不支持的操作符「{op}」，仅允许：eq、neq、contains、gt、lt、gte、lte、in、is_null"
                )

            col = sql.Identifier(field)

            if op == "is_null":
                conditions.append(sql.SQL("{} IS NULL").format(col))
                continue

            val = flt.get("value")
            if op == "in":
                if not isinstance(val, (list, tuple)) or len(val) == 0:
                    return _fail(f"filters[{i}] 使用 in 时 value 必须为非空数组")
                placeholders = sql.SQL(", ").join(
                    sql.Placeholder() for _ in range(len(val))
                )
                conditions.append(sql.SQL("{} IN ({})").format(col, placeholders))
                params.extend(val)
            elif op == "contains":
                if val is None:
                    return _fail(f"filters[{i}] contains 需要提供 value")
                s = str(val)
                conditions.append(sql.SQL("{} ILIKE {}").format(col, sql.Placeholder()))
                params.append(f"%{s}%")
            elif op == "eq":
                conditions.append(sql.SQL("{} = {}").format(col, sql.Placeholder()))
                params.append(val)
            elif op == "neq":
                conditions.append(
                    sql.SQL("{} IS DISTINCT FROM {}").format(col, sql.Placeholder())
                )
                params.append(val)
            elif op in ("gt", "lt", "gte", "lte"):
                cmp_op = {"gt": ">", "lt": "<", "gte": ">=", "lte": "<="}[op]
                conditions.append(
                    sql.SQL("{} " + cmp_op + " {}").format(col, sql.Placeholder())
                )
                params.append(val)
            else:
                return _fail(f"内部错误：未处理的操作符 {op}")

        # 软删：若元数据中有 deleted_at 列且未在 filters 中显式约束，则默认排除已删行
        has_deleted_at_col = "deleted_at" in allowed_fields
        user_touched_deleted_at = any(
            isinstance(f, dict) and f.get("field") == "deleted_at" for f in filters
        )
        if has_deleted_at_col and not user_touched_deleted_at:
            conditions.append(
                sql.SQL("{} IS NULL").format(sql.Identifier("deleted_at"))
            )

        from_tbl = sql.SQL("{}.{}").format(
            sql.Identifier(schema),
            sql.Identifier(table),
        )
        query = sql.SQL("SELECT {} FROM {}").format(
            sql.SQL(", ").join(sql.Identifier(f) for f in select_fields),
            from_tbl,
        )
        if conditions:
            query = sql.SQL("{} WHERE {}").format(
                query, sql.SQL(" AND ").join(conditions)
            )
        query = sql.SQL("{} LIMIT {}").format(query, sql.Placeholder())
        params.append(limit)

        cur.execute(query, params)
        if cur.description is None:
            rows_raw = []
        else:
            cols = [d[0] for d in cur.description]
            rows_raw = [dict(zip(cols, r)) for r in cur.fetchall()]

    rows = [_jsonable_row(dict(r)) for r in rows_raw]
    return _ok(table, rows, len(rows))
