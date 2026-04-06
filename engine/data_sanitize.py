"""V15-D：submit / confirm 前通用数据清洗（strip、空串→null、按 meta 类型转换）。"""
from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from typing import Any, Dict, Set

from engine.collecting import _validate_ident
from tools.database import db_conn

logger = logging.getLogger(__name__)

_SYS_DROP_ALWAYS = frozenset({"created_at", "updated_at", "deleted_at"})
_SYS_DROP_INSERT = frozenset({"id"})


@lru_cache(maxsize=256)
def _field_types_for_table(table_name: str) -> Dict[str, str]:
    t = (table_name or "").strip()
    if not t or not _validate_ident(t):
        return {}
    try:
        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT field_name, COALESCE(data_type::text, '')
                    FROM meta.meta_fields
                    WHERE table_name = %s
                    """,
                    (t,),
                )
                rows = cur.fetchall()
    except Exception:
        logger.debug("读取 meta_fields 失败 table=%s", t, exc_info=True)
        return {}
    out: Dict[str, str] = {}
    for fn, dt in rows:
        if fn:
            out[str(fn).strip()] = (dt or "").strip().lower()
    return out


def _coerce_value(field_name: str, val: Any, pg_type: str) -> Any:
    if val is None:
        return None
    base = pg_type.split("(")[0].strip()
    if base in (
        "int2",
        "int4",
        "int8",
        "integer",
        "bigint",
        "smallint",
        "serial",
        "bigserial",
    ):
        if isinstance(val, bool):
            return int(val)
        if isinstance(val, int):
            return val
        s = str(val).strip()
        if s == "":
            return None
        try:
            return int(s)
        except ValueError:
            return val
    if base in ("numeric", "decimal", "real", "double precision", "float4", "float8"):
        if isinstance(val, (int, float, Decimal)):
            return val
        s = str(val).strip()
        if s == "":
            return None
        try:
            return Decimal(s)
        except (InvalidOperation, ValueError):
            return val
    if base == "boolean" or base == "bool":
        if isinstance(val, bool):
            return val
        s = str(val).strip().lower()
        if s in ("true", "1", "yes", "y"):
            return True
        if s in ("false", "0", "no", "n"):
            return False
        return val
    if base in ("date", "timestamp", "timestamptz", "time"):
        if isinstance(val, (date, datetime)):
            return val
        s = str(val).strip()
        if not s:
            return None
        for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(s[:19], fmt)
            except ValueError:
                continue
        return val
    return val


def sanitize_row(table_name: str, row: dict, operation: str) -> dict:
    """
    单行清洗：strip、空串→None、按 meta.data_type 转换；
    去掉系统字段；insert 时去掉 id。
    """
    op = (operation or "").strip().lower()
    insert_like = op == "insert"
    tn = (table_name or "").strip()
    types = _field_types_for_table(tn) if tn else {}
    drop_ids: Set[str] = set(_SYS_DROP_INSERT) if insert_like else set()

    cleaned: Dict[str, Any] = {}
    for k_raw, v in row.items():
        k = str(k_raw).strip()
        if not k:
            continue
        if k in _SYS_DROP_ALWAYS:
            continue
        if k in drop_ids:
            continue

        if isinstance(v, str):
            v = v.strip()
            if v == "":
                v = None

        if k in types and v is not None:
            v = _coerce_value(k, v, types[k])

        cleaned[k] = v

    return cleaned


def sanitize_step_data(table_name: str, data: Any, operation: str) -> Any:
    """
    通用数据清洗。在校验或执行前调用。
    dict → 单行；list[dict] → 逐行；其他原样返回。
    """
    if isinstance(data, list):
        return [
            sanitize_row(table_name, r, operation)
            for r in data
            if isinstance(r, dict)
        ]
    if isinstance(data, dict):
        return sanitize_row(table_name, data, operation)
    return data
