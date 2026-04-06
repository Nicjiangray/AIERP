#!/usr/bin/env python3
"""
向 business.workflows / business.workflow_steps_legacy 写入 query_master_data 工作流（单步 code）。

实际执行由 engine.workflow_runner.run_query_master_data() / tools.query_master_data 接管。

注意：business.workflows.is_active 列为 integer（0/1），插入使用 1 而非布尔 true。

示例（项目根目录）：
  .venv/bin/python scripts/seed_query_master_data_workflow.py
"""

from __future__ import annotations

import os

import psycopg2


def _conn_kwargs() -> dict:
    return {
        "host": os.environ.get("PGHOST", "localhost"),
        "port": int(os.environ.get("PGPORT", "5432")),
        "dbname": os.environ.get("PGDATABASE", "aierp"),
        "user": os.environ.get("PGUSER", "nicjiang"),
    }


def main() -> None:
    wf_intent = "query_master_data"
    wf_row = (
        wf_intent,
        "查询主数据",
        "查询客户、产品、合同、联系人等基础数据。用户说\"查一下客户\"、\"看看产品列表\"、\"查合同\"、\"有哪些供应商\"等查询类意图时触发。",
        1,
    )

    sql_upsert_wf = """
        INSERT INTO business.workflows (intent, display_name, description, is_active)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (intent) DO UPDATE SET
          display_name = EXCLUDED.display_name,
          description = EXCLUDED.description,
          is_active = EXCLUDED.is_active
    """

    sql_insert_step = """
        INSERT INTO business.workflow_steps_legacy
          (workflow_intent, step_order, step_name, step_type, action, on_fail)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (workflow_intent, step_order) DO NOTHING
    """

    step_row = (
        wf_intent,
        1,
        "查询主数据",
        "code",
        "query_master_data",
        "stop",
    )

    conn = psycopg2.connect(**_conn_kwargs())
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(sql_upsert_wf, wf_row)
                cur.execute(sql_insert_step, step_row)
        print(
            "已 upsert workflows 1 条（intent=query_master_data）；"
            "workflow_steps_legacy 若不存在则插入 1 步（ON CONFLICT 已存在则跳过）。"
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
