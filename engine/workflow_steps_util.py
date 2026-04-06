"""workflow_steps 加载与 submit 拆数辅助（V15-B，供 tool_executor / workflow_engine 共用）。"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import psycopg2.errors
import psycopg2.extras

from tools.database import db_conn

logger = logging.getLogger(__name__)


def load_workflow_steps_for_intent(intent: str) -> List[Dict[str, Any]]:
    """按 seq 读取 workflow_steps（含 data_key / input_fields 等列，缺列时降级）。"""
    if not (intent or "").strip():
        return []
    sql_full = """
        SELECT id, workflow_intent, seq, action, label, table_name, instruction, tool,
               fields, validation_rules, input_fields, output_fields, depends_on, data_key,
               where_field
        FROM business.workflow_steps
        WHERE workflow_intent = %s
        ORDER BY seq ASC
        """
    sql_mid = """
        SELECT id, workflow_intent, seq, action, label, table_name, instruction, tool,
               fields, validation_rules, input_fields, output_fields, depends_on
        FROM business.workflow_steps
        WHERE workflow_intent = %s
        ORDER BY seq ASC
        """
    sql_legacy = """
        SELECT id, workflow_intent, seq, action, label, table_name, instruction, tool,
               fields, validation_rules
        FROM business.workflow_steps
        WHERE workflow_intent = %s
        ORDER BY seq ASC
        """
    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                try:
                    cur.execute(sql_full, (intent.strip(),))
                except psycopg2.errors.UndefinedColumn:
                    try:
                        cur.execute(sql_mid, (intent.strip(),))
                    except psycopg2.errors.UndefinedColumn:
                        cur.execute(sql_legacy, (intent.strip(),))
                rows = cur.fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            if "data_key" not in d:
                d["data_key"] = None
            if "where_field" not in d:
                d["where_field"] = None
            out.append(d)
        return out
    except psycopg2.errors.UndefinedTable:
        return []
    except Exception:
        logger.warning("读取 business.workflow_steps 失败", exc_info=True)
        return []


def infer_operation_from_intent(intent: str, step: Dict[str, Any]) -> str:
    """从意图与步骤推断 insert / update / soft_delete。"""
    table = (step.get("table_name") or "").strip()
    if table == "contract_changes":
        return "insert"
    low = (intent or "").strip().lower()
    if "create" in low:
        return "insert"
    if "update" in low:
        return "update"
    if "delete" in low:
        return "soft_delete"
    return "insert"


def infer_write_operation(intent: str, step: Dict[str, Any]) -> str:
    """
    细粒度 write 操作类型（V15-D）。
    update_contract 按 data_key 区分主表 update / 明细 insert|update|soft_delete。
    """
    tn = (step.get("table_name") or "").strip()
    dk = (step.get("data_key") or "").strip()
    if tn == "contract_changes":
        return "insert"
    low = (intent or "").strip().lower()
    if low == "update_contract":
        if dk == "header_updates":
            return "update"
        if dk == "items_add":
            return "insert"
        if dk == "items_update":
            return "update"
        if dk == "items_remove":
            return "soft_delete"
        return infer_operation_from_intent(intent, step)
    return infer_operation_from_intent(intent, step)


def extract_data_for_step(
    data: Any,
    step: Dict[str, Any],
    write_steps: List[Dict[str, Any]],
) -> Any:
    """
    从 LLM / 规范化 data 中取当前 write 步骤对应的数据片段。
    data 可为 dict（单表、合同主表+明细、删除规范化包等）。
    """
    table = (step.get("table_name") or "").strip()
    dk = (step.get("data_key") or "").strip()

    if dk and isinstance(data, dict) and dk in data:
        return data[dk]

    if table and isinstance(data, dict) and table in data:
        return data[table]

    aliases: Dict[str, List[str]] = {
        "contracts": ["contract"],
        "contract_items": ["items", "contract_items"],
        "contract_changes": [],
    }
    for alias in aliases.get(table, []):
        if isinstance(data, dict) and alias in data:
            return data[alias]

    n_write = len(write_steps)
    if n_write == 1 and isinstance(data, dict):
        if "fields" in data and "table_name" in data:
            return data.get("fields")
        if "record_id" in data and "table_name" in data:
            return {
                "record_id": data.get("record_id"),
                "fields": data.get("fields") if isinstance(data.get("fields"), dict) else {},
                "table_name": data.get("table_name"),
                "explanation": data.get("explanation"),
            }
        if "record_id" in data and ("explanation" in data or "reason" in data):
            return data
    return data if isinstance(data, (dict, list)) else {}


def resolved_table_name(step: Dict[str, Any], data: Any) -> str:
    """write 项使用的表名：优先 step.table_name，否则从 data.table_name。"""
    t = (step.get("table_name") or "").strip()
    if t:
        return t
    if isinstance(data, dict):
        t2 = (data.get("table_name") or "").strip()
        if t2:
            return t2
    return ""
