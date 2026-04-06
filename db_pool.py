# DEPRECATED: 已迁移至 tools/database.py，请勿引用
# 已迁移至 tools/database.py，待确认后删除
"""
进程级 PostgreSQL 连接池与安全的 `with` 用法。

**业务代码请始终使用 `with db_conn() as conn:` / `with db_conn_llm() as conn:`**，
不要手写 `get_conn()` + `release_conn()`（易漏归还或异常路径遗漏）。游标一律
`with conn.cursor(...) as cur:`，勿手动 `cur.close()`。

写事务可在 `with db_conn() as conn:` 内再套一层 `with conn:`（psycopg2 在块结束时
自动 commit / 有异常则 rollback），避免散落的 `conn.commit()` / `conn.rollback()`。
注意：`with conn:` 只界定**事务**（不关闭连接、不把连接还池）；连接仍由外层的
`with db_conn() as conn:` / `db_conn_llm()` 在退出时 `putconn` 归还。

- `db_conn()`：HTTP API 默认账号（PGUSER 默认 nicjiang）。
- `db_conn_llm()`：LLM 工具（与 main.get_llm_db_config() 一致，PGUSER 默认 llm_assistant）。

两套池分离；若显式设置 PGUSER，两池读到同一用户名（与原先 get_llm_db_config 行为一致）。
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Dict, Iterator

from psycopg2.pool import ThreadedConnectionPool


def _api_pg_kwargs() -> Dict[str, Any]:
    return {
        "dbname": os.environ.get("PGDATABASE", "aierp"),
        "user": os.environ.get("PGUSER", "nicjiang"),
        "host": os.environ.get("PGHOST", "localhost"),
        "port": int(os.environ.get("PGPORT", "5432")),
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
        "password": os.environ.get("PGPASSWORD", "llm_safe_2024"),
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
