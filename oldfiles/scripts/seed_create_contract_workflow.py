#!/usr/bin/env python3
"""
向 business.workflows / business.workflow_steps_legacy 写入 create_contract 工作流及 6 步定义。

使用 psycopg2 参数化占位符 %s，由驱动处理字符串中的单引号转义。

示例（项目根目录）：
  .venv/bin/python scripts/seed_create_contract_workflow.py
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
    wf_intent = "create_contract"
    wf_row = (
        wf_intent,
        "新建采购合同",
        "当用户想要新建、创建、签订采购合同时触发。关键词：新建合同、创建合同、签合同、采购合同、新增合同",
        1,
    )

    steps: list[tuple] = [
        (
            wf_intent,
            1,
            "检查用户权限",
            "query_sql",
            None,
            "SELECT role FROM business.employees WHERE id = '{user_id}' AND is_active = 1",
            None,
            "user_id",
            "user_role",
            "stop",
        ),
        (
            wf_intent,
            2,
            "验证产品",
            "query_sql",
            None,
            "SELECT id, product_name FROM business.products WHERE product_code = '{product_code}' AND status = 'active'",
            None,
            "product_code",
            "product_id",
            "ask_user",
        ),
        (
            wf_intent,
            3,
            "验证供应商",
            "query_sql",
            None,
            "SELECT id, name FROM business.suppliers WHERE name = '{supplier_name}' AND is_active = 1",
            None,
            "supplier_name",
            "supplier_id",
            "ask_user",
        ),
        (
            wf_intent,
            4,
            "读取上传文件",
            "code",
            "read_file",
            None,
            None,
            "file_path",
            "file_data",
            "skip",
        ),
        (
            wf_intent,
            5,
            "检查必填字段",
            "llm",
            None,
            None,
            "根据context中已有信息，对照合同必填字段清单，判断缺少哪些信息，返回missing_fields列表",
            "file_data,product_id,supplier_id,user_role",
            "missing_fields",
            "ask_user",
        ),
        (
            wf_intent,
            6,
            "写入合同记录",
            "query_sql",
            None,
            "INSERT INTO business.contracts (supplier_id, product_id, amount, created_by, status) "
            "VALUES ('{supplier_id}', '{product_id}', '{amount}', '{user_id}', 'draft')",
            None,
            "supplier_id,product_id,amount,user_id",
            "contract_id",
            "stop",
        ),
    ]

    sql_delete_steps = "DELETE FROM business.workflow_steps_legacy WHERE workflow_intent = %s"
    sql_delete_wf = "DELETE FROM business.workflows WHERE intent = %s"
    sql_insert_wf = """
        INSERT INTO business.workflows (intent, display_name, description, is_active)
        VALUES (%s, %s, %s, %s)
    """
    sql_insert_step = """
        INSERT INTO business.workflow_steps_legacy (
            workflow_intent, step_order, step_name, step_type, action, sql_template,
            prompt_template, input_keys, output_key, on_fail
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """

    conn = psycopg2.connect(**_conn_kwargs())
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(sql_delete_steps, (wf_intent,))
                cur.execute(sql_delete_wf, (wf_intent,))
                cur.execute(sql_insert_wf, wf_row)
                cur.executemany(sql_insert_step, steps)
        print("已写入 workflows 1 条、workflow_steps_legacy 6 条（intent=create_contract）。")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
