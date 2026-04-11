"""
PostgreSQL 连接池（API / LLM 双池）及工作流可调用的表级工具函数。

业务 HTTP 路由使用 `db_conn()`；LLM 工具链使用 `db_conn_llm()`。
`query_table` / `write_table` / `update_table` 供 `tools.TOOLS` 注册，入参/出参均为 context 字典。
"""

from __future__ import annotations

import os
import re
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List

import psycopg2.extras
from psycopg2.pool import ThreadedConnectionPool


def _api_pg_kwargs() -> Dict[str, Any]:
    return {
        "dbname": os.environ.get("PGDATABASE", "aierp"),
        "user": os.environ.get("PGUSER", "nicjiang"),
        "host": os.environ.get("PGHOST", "localhost"),
        "port": int(os.environ.get("PGPORT", "5432")),
        "password": os.environ.get("PG_API_PASSWORD", ""),
    }


def _llm_pg_kwargs() -> Dict[str, Any]:
    try:
        port = int(os.environ.get("PGPORT", "5432"))
    except ValueError:
        port = 5432
    return {
        "host": os.environ.get("PGHOST", "localhost"),
        "dbname": os.environ.get("PGDATABASE", "aierp"),
        "user": os.environ.get("PGUSER", "llm_assistant"),
        "password": os.environ.get("PG_LLM_PASSWORD", "llm_safe_2024"),
        "port": port,
    }


_pool_max = max(1, int(os.environ.get("PG_POOL_MAX", "20")))
_llm_pool_max = max(1, int(os.environ.get("PG_POOL_MAX_LLM", str(_pool_max))))

_conn_pool = ThreadedConnectionPool(1, _pool_max, **_api_pg_kwargs())
_llm_pool = ThreadedConnectionPool(1, _llm_pool_max, **_llm_pg_kwargs())


def get_conn():
    return _conn_pool.getconn()


def release_conn(conn, *, close: bool = False) -> None:
    _conn_pool.putconn(conn, close=close)


def get_conn_llm():
    return _llm_pool.getconn()


def release_conn_llm(conn, *, close: bool = False) -> None:
    _llm_pool.putconn(conn, close=close)


@contextmanager
def db_conn() -> Iterator[Any]:
    conn = get_conn()
    try:
        yield conn
    except Exception:
        release_conn(conn, close=True)
        raise
    else:
        release_conn(conn)


@contextmanager
def db_conn_llm() -> Iterator[Any]:
    conn = get_conn_llm()
    try:
        yield conn
    except Exception:
        release_conn_llm(conn, close=True)
        raise
    else:
        release_conn_llm(conn)


def close_all_pools() -> None:
    _conn_pool.closeall()
    _llm_pool.closeall()


def get_meta_tables() -> List[Dict[str, Any]]:
    """
    读取 meta.meta_tables：table_name, table_cn, description。
    返回 list of dict（与 LLM 工具链一致，使用 llm 连接）。
    """
    with db_conn_llm() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT table_name, table_cn, description
                FROM meta.meta_tables
                ORDER BY table_name
                """
            )
            return [dict(r) for r in cur.fetchall()]


def get_meta_fields(table_name: str) -> List[Dict[str, Any]]:
    """
    读取 meta.meta_fields：按表名过滤，返回
    field_name, field_cn, data_type, is_required, description。
    """
    with db_conn_llm() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT field_name, field_cn, data_type, is_required, description
                FROM meta.meta_fields
                WHERE table_name = %s
                ORDER BY id
                """,
                (table_name,),
            )
            return [dict(r) for r in cur.fetchall()]


def _single_sql(sql: str) -> str:
    s = sql.strip().rstrip(";").strip()
    if ";" in s:
        raise ValueError("仅允许单条 SQL，不能包含分号串联多条语句。")
    return s


def query_table(context: Dict[str, Any]) -> Dict[str, Any]:
    """
    只读查询。约定 context 含键 `sql`（单条 SELECT）。
    成功时在 context 中写入 `query_columns`、`query_rows`、`query_row_count`。
    """
    ctx = dict(context)
    sql = ctx.get("sql")
    if not isinstance(sql, str) or not sql.strip():
        ctx["error"] = "query_table: 需要非空字符串 context['sql']"
        return ctx
    try:
        validated = _single_sql(sql)
        if not re.match(r"\s*SELECT\b", validated, re.IGNORECASE | re.DOTALL):
            ctx["error"] = "query_table: 仅允许以 SELECT 开头的查询"
            return ctx
        with db_conn_llm() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(validated)
                rows = cur.fetchall()
                cols = [d[0] for d in cur.description] if cur.description else []
                plain = [dict(r) for r in rows]
        ctx["query_columns"] = cols
        ctx["query_rows"] = plain
        ctx["query_row_count"] = len(plain)
        ctx.pop("error", None)
    except Exception as e:
        ctx["error"] = f"query_table: {e}"
    return ctx


def write_table(context: Dict[str, Any]) -> Dict[str, Any]:
    """
    写入（INSERT 等）。约定 context 含键 `sql`（单条写语句，以 INSERT 等开头，非 UPDATE）。
    成功时写入 `write_rows_affected`。
    """
    ctx = dict(context)
    sql = ctx.get("sql")
    if not isinstance(sql, str) or not sql.strip():
        ctx["error"] = "write_table: 需要非空字符串 context['sql']"
        return ctx
    try:
        validated = _single_sql(sql)
        head = validated.lstrip()
        first = re.split(r"\s+", head, maxsplit=1)[0].upper()
        if first == "UPDATE":
            ctx["error"] = "write_table: 请使用 update_table 执行 UPDATE"
            return ctx
        allowed = {"INSERT", "DELETE", "CREATE", "DROP"}
        if first not in allowed:
            ctx["error"] = f"write_table: 仅支持 {', '.join(sorted(allowed))} 开头，当前为 {first}"
            return ctx
        with db_conn_llm() as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(validated)
                n = cur.rowcount
        ctx["write_rows_affected"] = n
        ctx.pop("error", None)
    except Exception as e:
        ctx["error"] = f"write_table: {e}"
    return ctx


def update_table(context: Dict[str, Any]) -> Dict[str, Any]:
    """
    更新。约定 context 含键 `sql`（单条 UPDATE）。
    成功时写入 `update_rows_affected`。
    """
    ctx = dict(context)
    sql = ctx.get("sql")
    if not isinstance(sql, str) or not sql.strip():
        ctx["error"] = "update_table: 需要非空字符串 context['sql']"
        return ctx
    try:
        validated = _single_sql(sql)
        head = validated.lstrip()
        first = re.split(r"\s+", head, maxsplit=1)[0].upper()
        if first != "UPDATE":
            ctx["error"] = "update_table: 仅允许以 UPDATE 开头的语句"
            return ctx
        with db_conn_llm() as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(validated)
                n = cur.rowcount
        ctx["update_rows_affected"] = n
        ctx.pop("error", None)
    except Exception as e:
        ctx["error"] = f"update_table: {e}"
    return ctx


def get_session_memo(session_id: int) -> Dict[str, Any]:
    """
    读取 business.sessions.memo。
    无此行或 memo 为 NULL 时返回空 dict。
    """
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT memo FROM business.sessions WHERE id = %s",
                (session_id,),
            )
            row = cur.fetchone()
            if not row:
                return {}
            raw = row[0]
            if raw is None:
                return {}
            if isinstance(raw, dict):
                return dict(raw)
            return {}


def update_session_memo(session_id: int, memo_dict: Dict[str, Any]) -> None:
    """将指定 session 的 memo 整列更新为 memo_dict（覆盖写入）。"""
    with db_conn() as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE business.sessions SET memo = %s WHERE id = %s",
                (psycopg2.extras.Json(memo_dict), session_id),
            )
