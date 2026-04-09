"""
collecting Agent：供 LLM tool calling 注册的工具定义（OpenAI function 格式）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "query_db",
            "description": (
                "查询数据库获取参考信息。只读 SELECT。"
                "适用场景：查现有编号、查枚举值、查关联记录、核实数据是否存在。"
                "只允许查 business.* 和 meta.* schema。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {"type": "string", "description": "只读 SELECT 语句"},
                    "reason": {"type": "string", "description": "为什么要查这个"},
                },
                "required": ["sql", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit",
            "description": (
                "提交写操作预览（新建/更新/删除/合同等），不直接写库；"
                "具体字段由当前 workflow_intent 决定，放在 data 中；"
                "更新与删除时可将原因放在 reason 或 data 内。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "data": {
                        "type": "object",
                        "description": "业务数据（字段名依意图而定，如 table_name/fields、contract/items 等）",
                        "additionalProperties": True,
                    },
                    "reason": {
                        "type": "string",
                        "description": "操作原因（更新/删除时建议填写）",
                    },
                },
                "required": ["data"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_user",
            "description": (
                "需要向用户追问缺失、模糊或冲突信息时调用。"
                "调用后由系统将问题展示给用户，并等待其回复。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "要向用户提出的问题（单条、清晰）",
                    },
                    "hint": {
                        "type": "string",
                        "description": "可选：枚举值、格式示例或填写提示",
                    },
                },
                "required": ["question"],
            },
        },
    },
]


def load_tools_from_db(filter_names: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """
    从 meta.meta_tools 读取 enabled=true 的工具，构建 OpenAI function calling 格式列表。
    filter_names 非空时仅保留白名单内工具名（顺序与库中 sort_order 一致）。
    失败时 fallback 到硬编码的 TOOLS。
    """
    try:
        from tools.database import db_conn
        import psycopg2.extras

        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT tool_name, description, parameters_schema
                    FROM meta.meta_tools
                    WHERE enabled = true
                    ORDER BY sort_order
                    """
                )
                rows = cur.fetchall()
        if not rows:
            base = list(TOOLS)
        else:
            base = []
            for r in rows:
                base.append(
                    {
                        "type": "function",
                        "function": {
                            "name": r["tool_name"],
                            "description": r["description"],
                            "parameters": r["parameters_schema"]
                            if isinstance(r["parameters_schema"], dict)
                            else {},
                        },
                    }
                )
        if not filter_names:
            return base if base else list(TOOLS)
        allow = {str(x).strip() for x in filter_names if x and str(x).strip()}
        if not allow:
            return base if base else list(TOOLS)
        filtered = [
            x
            for x in base
            if (x.get("function") or {}).get("name") in allow
        ]
        if filtered:
            return filtered
        import logging

        logging.getLogger(__name__).warning(
            "load_tools_from_db: filter_names 过滤后无工具，忽略过滤",
            extra={"filter_names": list(allow)},
        )
        return base if base else list(TOOLS)
    except Exception:
        import logging

        logging.getLogger(__name__).warning(
            "load_tools_from_db 失败，fallback 到硬编码 TOOLS", exc_info=True
        )
        return list(TOOLS)
