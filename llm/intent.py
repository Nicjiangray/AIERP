# DEPRECATED: 占位文件，意图识别逻辑在 engine/intent.py
"""
对话意图识别与槽位检查：识别 intent、校验必填字段、生成追问与解析用户补充回答（占位实现）。
"""

from __future__ import annotations

from typing import Any, Dict, List


def identify_intent(user_input: str) -> str:
    return ""  # 占位


def check_fields(context: Dict[str, Any]) -> List[str]:
    return []  # 占位


def ask_user(context: Dict[str, Any]) -> str:
    return ""  # 占位


def parse_reply(user_input: str, context: Dict[str, Any]) -> Dict[str, Any]:
    return dict(context)  # 占位
