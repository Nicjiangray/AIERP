"""
工具包：数据库与文件 I/O 的可调用入口，统一暴露在 TOOLS 字典中供工作流按名称调度。
"""

from __future__ import annotations

from tools.database import query_table, update_table, write_table
from tools.file_reader import read_excel, read_image, read_pdf
from tools.file_writer import export_excel, generate_pdf

def query_master_data(context):
    """工作流 code 步骤：主数据查询（惰性导入，避免 tools ↔ workflow_runner 循环依赖）。"""
    from engine.workflow_runner import run_query_master_data

    return run_query_master_data(context)


TOOLS = {
    "query_table": query_table,
    "write_table": write_table,
    "update_table": update_table,
    "query_master_data": query_master_data,
    "read_pdf": read_pdf,
    "read_excel": read_excel,
    "read_image": read_image,
    "export_excel": export_excel,
    "generate_pdf": generate_pdf,
}

__all__ = [
    "TOOLS",
    "query_table",
    "write_table",
    "update_table",
    "query_master_data",
    "read_pdf",
    "read_excel",
    "read_image",
    "export_excel",
    "generate_pdf",
]
