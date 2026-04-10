"""引擎层：查询与安全执行等。

统一 SQL 审计与执行入口见 `engine.sql_engine`（execute_sql / preview_sql / confirm_execute_sql / confirm_batch_sql）。
`query_engine` 与 `write_engine` 为历史结构化 payload 路径，已 deprecated，仅保留兼容。
"""
