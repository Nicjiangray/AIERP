"""数据库连接与元数据读取（从旧项目 tools 保留，供新项目与 llm.client 使用）。"""

from tools.database import (
    close_all_pools,
    db_conn,
    db_conn_llm,
    get_conn,
    get_conn_llm,
    get_meta_fields,
    get_meta_tables,
    get_session_memo,
    query_table,
    release_conn,
    release_conn_llm,
    update_session_memo,
    update_table,
    write_table,
)

__all__ = [
    'db_conn',
    'db_conn_llm',
    'get_conn',
    'get_conn_llm',
    'release_conn',
    'release_conn_llm',
    'close_all_pools',
    'get_meta_tables',
    'get_meta_fields',
    'query_table',
    'write_table',
    'update_table',
    'get_session_memo',
    'update_session_memo',
]
