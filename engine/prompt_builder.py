"""
engine/prompt_builder.py
统一 prompt 拼装：按 agent_role 从 meta.prompts 取静态片段，按 user_id 从 business.ai_memory 取动态记忆。
"""

from __future__ import annotations

from typing import Optional

from tools.database import db_conn


def build_prompt(agent_role: str, user_id: Optional[int] = None) -> str:
    """
    拼装指定 agent 的完整 system prompt。

    参数：
        agent_role: 'assistant' | 'tool_expert' | 'memory_manager'
        user_id: 用户 ID，assistant 角色时用于注入记忆，其他角色可不传

    返回：
        拼装好的 system prompt 字符串
    """
    parts: list[str] = []

    # 1. 从 meta.prompts 拉该角色的静态提示词片段
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT prompt_code, content
                FROM meta.prompts
                WHERE agent_role = %s
                  AND is_active = TRUE
                  AND inject_mode = 'always'
                ORDER BY sort_order
                """,
                (agent_role,),
            )
            rows = cur.fetchall()
            for row in rows:
                parts.append(row[1])  # content 字段

    # 2. 如果是主助手且有 user_id，注入记忆
    if agent_role == "assistant" and user_id is not None:
        memory_parts = _load_user_memory(user_id)
        if memory_parts:
            parts.append(memory_parts)

    return "\n\n".join(parts)


def build_prompt_on_demand(prompt_code: str) -> str:
    """
    按需拉取单条 prompt，用于记忆管理员等非 always 场景。

    参数：
        prompt_code: 如 'memory_session_clean' 或 'memory_longterm_compress'

    返回：
        该条 prompt 的 content 字符串
    """
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT content
                FROM meta.prompts
                WHERE prompt_code = %s AND is_active = TRUE
                """,
                (prompt_code,),
            )
            row = cur.fetchone()
            return row[0] if row else ""


def _load_user_memory(user_id: int) -> str:
    """
    从 business.ai_memory 拉取用户记忆，拼成注入文本。
    长期记忆 + 今日 session 记忆。
    """
    sections: list[str] = []

    with db_conn() as conn:
        with conn.cursor() as cur:
            # 长期记忆（只有一条）
            cur.execute(
                """
                SELECT content FROM business.ai_memory
                WHERE user_id = %s AND scope = 'longterm'
                ORDER BY updated_at DESC LIMIT 1
                """,
                (user_id,),
            )
            row = cur.fetchone()
            if row and row[0]:
                sections.append(f"【长期记忆】\n{row[0]}")

            # 今日 session 记忆
            cur.execute(
                """
                SELECT content FROM business.ai_memory
                WHERE user_id = %s AND scope = 'session'
                AND created_at >= CURRENT_DATE
                ORDER BY created_at
                """,
                (user_id,),
            )
            rows = cur.fetchall()
            if rows:
                today_items = "\n".join(r[0] for r in rows)
                sections.append(f"【今日记录】\n{today_items}")

    return "\n\n".join(sections)
