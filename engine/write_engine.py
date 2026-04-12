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

from psycopg2 import errors as pg_errors
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

# 可选：按 PostgreSQL 外键约束名覆盖「请先在【YYY】…」中的展示名（缺省用 meta.meta_tables.table_cn）
# 可由 build_fk_hint_map_from_db() 根据 information_schema / pg_constraint 预填
FK_HINT_MAP: Dict[str, str] = {}


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


# --- 写库异常业务化翻译（供 sql_engine 确认执行层使用；不改动查询逻辑） ---


def _fk_operation_label(pgerror: str) -> str:
    """从 PostgreSQL 主错误行推断操作动词（不含「失败」）。"""
    low = (pgerror or "").lower()
    if "delete on table" in low:
        return "删除"
    if "insert or update on table" in low or "insert or update" in low:
        return "保存"
    if "update on table" in low:
        return "更新"
    if "insert on table" in low:
        return "新建"
    return "操作"


def _ref_table_from_constraint_name(cur, constraint_name: str) -> Optional[str]:
    """由 pg 外键约束名解析被引用表（无 schema 前缀的 relname）。"""
    if not constraint_name or cur is None:
        return None
    cur.execute(
        """
        SELECT rel2.relname
        FROM pg_constraint c
        JOIN pg_class rel2 ON rel2.oid = c.confrelid
        WHERE c.conname = %s AND c.contype = 'f'
        LIMIT 1
        """,
        (constraint_name,),
    )
    row = cur.fetchone()
    return str(row[0]) if row and row[0] else None


def _parse_fk_violation_detail(detail: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    解析 DETAIL: Key (col)=(val) is not present in table "ref".
    返回 (key列, 值, 被引用表名)。
    """
    if not detail:
        return None, None, None
    m = re.search(
        r'Key \(([^)]+)\)=\((.*)\)\s+is not present in table "([^"]+)"',
        detail,
        re.DOTALL | re.IGNORECASE,
    )
    if not m:
        return None, None, None
    return m.group(1).strip(), m.group(2).strip(), m.group(3).strip()


def _build_fk_user_message(exc: BaseException, cur) -> Optional[str]:
    """构造外键业务提示；无法解析时返回 None 交由上层按 unknown 处理。"""
    diag = getattr(exc, "diag", None)
    pgerror = getattr(exc, "pgerror", None) or str(exc)
    detail = (getattr(diag, "message_detail", None) or "").strip()
    constraint_name = getattr(diag, "constraint_name", None) if diag else None

    _cols, bad_val, ref_table = _parse_fk_violation_detail(detail)
    if not ref_table and constraint_name and cur is not None:
        ref_table = _ref_table_from_constraint_name(cur, constraint_name)
    if not ref_table:
        return None

    if constraint_name and constraint_name in FK_HINT_MAP:
        yyy = FK_HINT_MAP[constraint_name]
    elif cur is not None:
        yyy = _load_table_label(cur, ref_table)
    else:
        yyy = ref_table

    op = _fk_operation_label(pgerror)
    val_disp = bad_val if bad_val else "关联值"
    return (
        f"{op}失败：系统中不存在 [{val_disp}]，请先在【{yyy}】中建立对应记录"
    )


def translate_db_write_exception(exc: BaseException, cur) -> Dict[str, Any]:
    """
    将 PostgreSQL 写操作异常转为统一结构（确认执行层返回给编排 / LLM）。

    - dependency_missing：外键，message 为业务中文
    - duplicate：唯一约束 / 重复键
    - unknown：其余，message 为原始错误文本
    """
    raw = str(exc).strip()

    def _out(error_type: str, message: str) -> Dict[str, Any]:
        return {
            "success": False,
            "error_type": error_type,
            "message": message,
            "error": message,
        }

    if isinstance(exc, pg_errors.UniqueViolation):
        return _out("duplicate", "该记录已存在，请勿重复操作")
    if isinstance(exc, pg_errors.ForeignKeyViolation):
        msg = _build_fk_user_message(exc, cur)
        if msg:
            return _out("dependency_missing", msg)
        return _out("unknown", raw)
    return _out("unknown", raw)


def build_fk_hint_map_from_db(cur) -> Dict[str, str]:
    """
    根据当前库中全部外键约束，预填「约束名 -> 被引用表中文名」。
    便于维护 FK_HINT_MAP；可在迁移或启动时调用一次写回配置。
    """
    out: Dict[str, str] = {}
    cur.execute(
        """
        SELECT c.conname, rel2.relname
        FROM pg_constraint c
        JOIN pg_class rel2 ON rel2.oid = c.confrelid
        WHERE c.contype = 'f'
        ORDER BY c.conname
        """
    )
    for conname, relname in cur.fetchall() or []:
        if not conname or not relname:
            continue
        out[str(conname)] = _load_table_label(cur, str(relname))
    return out
