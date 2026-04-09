"""
engine/memory_manager.py
记忆管理员：session 清洗（对话→要点）和 longterm 压缩（合并+规律提炼）。

LLM 调用说明（与 llm/client.py 一致）：
- `chat()` 使用环境变量 LLM_MODEL_NAME / LLM_BASE_URL / LLM_API_KEY（主客户端）。
  当前仓库典型 .env 将上述三项指向 DeepSeek OpenAI 兼容接口，此时记忆管线即走 DeepSeek。
- `chat_expert_only()` 仅使用 FALLBACK_MODEL_NAME / FALLBACK_BASE_URL / FALLBACK_API_KEY。
当主 LLM_BASE_URL 为本机 Ollama（URL 含 11434）时，记忆管线改用 `chat_expert_only`，
以便在「主对话走本地、云端仅作兜底」的配置下仍用云端模型做记忆任务（需配置 FALLBACK_API_KEY）。
"""

from __future__ import annotations

import os
from typing import Any, Dict, List

from engine.prompt_builder import build_prompt_on_demand
from llm.client import chat, chat_expert_only
from tools.database import db_conn


def _memory_chat(messages: List[Dict[str, Any]]) -> str:
    """主模型为本地 Ollama 时用云端兜底模型，否则与主对话一致用 `chat()`。"""
    base = (os.environ.get("LLM_BASE_URL") or "").lower()
    if "11434" in base:
        return chat_expert_only(messages)
    return chat(messages)


def clean_session(session_id: int) -> Dict[str, Any]:
    """
    清洗指定 session 的对话历史，提取业务要点，写入 ai_memory。

    流程：
    1. 从 ai_chat 读取该 session 的全部对话
    2. 拉取 memory_session_clean 提示词
    3. 调 LLM 提取要点
    4. 写入 ai_memory(scope=session)
    """
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT role, content, created_at
                FROM business.ai_chat
                WHERE session_id = %s
                ORDER BY created_at
                """,
                (session_id,),
            )
            rows = cur.fetchall()

    if not rows:
        return {"success": False, "error": f"session {session_id} 无对话记录"}

    chat_history = "\n".join(f"[{row[0]}] {row[1]}" for row in rows)

    prompt_template = build_prompt_on_demand("memory_session_clean")
    if not prompt_template:
        return {"success": False, "error": "未找到 memory_session_clean 提示词"}

    system_prompt = prompt_template.replace("{chat_history}", chat_history)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "请从以上对话中提取业务要点。"},
    ]

    try:
        result = _memory_chat(messages)
    except Exception as e:
        return {"success": False, "error": f"LLM调用失败: {e}"}

    if not result or not result.strip():
        return {"success": False, "error": "LLM返回为空"}

    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT user_id FROM business.ai_sessions WHERE id = %s
                """,
                (session_id,),
            )
            row = cur.fetchone()
            if not row:
                return {"success": False, "error": f"session {session_id} 不存在"}
            user_id = row[0]

            cur.execute(
                """
                INSERT INTO business.ai_memory (user_id, scope, source_session_id, content)
                VALUES (%s, 'session', %s, %s)
                """,
                (user_id, session_id, result.strip()),
            )
        conn.commit()

    return {
        "success": True,
        "content": result.strip(),
        "session_id": session_id,
        "user_id": user_id,
    }


def compress_longterm(user_id: int) -> Dict[str, Any]:
    """
    长期记忆压缩：将当日 session 要点与旧 longterm 合并压缩。

    流程：
    1. 从 ai_memory 读取该用户今日所有 session 要点
    2. 从 ai_memory 读取现有 longterm 记忆
    3. 拉取 memory_longterm_compress 提示词
    4. 调 LLM 压缩合并
    5. 覆盖写入 ai_memory(scope=longterm)
    """
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT content FROM business.ai_memory
                WHERE user_id = %s AND scope = 'session'
                AND created_at >= CURRENT_DATE
                ORDER BY created_at
                """,
                (user_id,),
            )
            session_rows = cur.fetchall()

            cur.execute(
                """
                SELECT id, content FROM business.ai_memory
                WHERE user_id = %s AND scope = 'longterm'
                ORDER BY updated_at DESC LIMIT 1
                """,
                (user_id,),
            )
            longterm_row = cur.fetchone()

    today_sessions = (
        "\n\n".join(r[0] for r in session_rows) if session_rows else "（今日无新记录）"
    )
    existing_longterm = longterm_row[1] if longterm_row else "（暂无长期记忆）"

    prompt_template = build_prompt_on_demand("memory_longterm_compress")
    if not prompt_template:
        return {"success": False, "error": "未找到 memory_longterm_compress 提示词"}

    system_prompt = prompt_template.replace("{today_sessions}", today_sessions).replace(
        "{existing_longterm}", existing_longterm
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "请合并压缩为新的长期记忆。"},
    ]

    try:
        result = _memory_chat(messages)
    except Exception as e:
        return {"success": False, "error": f"LLM调用失败: {e}"}

    if not result or not result.strip():
        return {"success": False, "error": "LLM返回为空"}

    with db_conn() as conn:
        with conn.cursor() as cur:
            if longterm_row:
                cur.execute(
                    """
                    UPDATE business.ai_memory
                    SET content = %s, updated_at = NOW()
                    WHERE id = %s
                    """,
                    (result.strip(), longterm_row[0]),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO business.ai_memory (user_id, scope, content)
                    VALUES (%s, 'longterm', %s)
                    """,
                    (user_id, result.strip()),
                )
        conn.commit()

    return {"success": True, "content": result.strip(), "user_id": user_id}
