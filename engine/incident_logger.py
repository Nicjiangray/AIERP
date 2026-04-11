"""
系统错误自动写入 business.ai_incidents；在 catch 或失败分支调用，失败时仅打日志不抛异常。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from psycopg2.extras import Json

from tools.database import db_conn

logger = logging.getLogger(__name__)


def is_audit_permission_only(reason: str) -> bool:
    """R7 等业务权限拒绝不记 incident（正常行为）。"""
    if not reason:
        return False
    return "权限不足" in reason


def log_incident(
    stage: str,
    error_message: str,
    session_id: Optional[int] = None,
    user_id: Optional[int] = None,
    user_input: Optional[str] = None,
    generated_sql: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
) -> None:
    """记录一条系统错误；写入失败只打日志，不影响主流程。"""
    try:
        em = (error_message or "")[:5000]
        ui = user_input[:2000] if user_input else None
        gs = generated_sql[:5000] if generated_sql else None
        ctx = Json(context) if context else None
        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO business.ai_incidents
                        (session_id, user_id, stage, user_input, generated_sql, error_message, context)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (session_id, user_id, stage, ui, gs, em, ctx),
                )
            conn.commit()
    except Exception as e:
        logger.warning("[incident_logger] 写入失败: %s", e)
