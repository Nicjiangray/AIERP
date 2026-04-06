"""对话轮次写入与查询（business.turns），使用 API 连接池 db_conn()。"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from tools.database import db_conn


def _sync_append_turn(
    session_id: int,
    frame_id: Optional[int],
    role: str,
    content: str,
) -> int:
    """
    写入一条对话记录，返回 turn id。
    role: 'user' 或 'assistant'
    frame_id: 当前关联的 frame，没有则传 None
    """
    with db_conn() as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO business.turns
                    (session_id, frame_id, role, content)
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (session_id, frame_id, role, content),
            )
            row = cur.fetchone()
            if not row:
                raise RuntimeError("INSERT business.turns 未返回 id")
            return int(row[0])


async def append_turn(
    session_id: int,
    frame_id: Optional[int],
    role: str,
    content: str,
) -> int:
    return await asyncio.to_thread(
        _sync_append_turn, session_id, frame_id, role, content
    )


def _sync_get_recent_turns(session_id: int, limit: int = 10) -> List[Dict[str, Any]]:
    """
    取最近 N 条对话记录，按时间正序返回。
    用于每次调用 LLM 时拼入 context。
    """
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT role, content, frame_id, created_at
                FROM business.turns
                WHERE session_id = %s
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (session_id, limit),
            )
            rows = cur.fetchall()
            return [
                {
                    "role": r[0],
                    "content": r[1],
                    "frame_id": r[2],
                    "created_at": str(r[3]),
                }
                for r in reversed(rows)
            ]


async def get_recent_turns(session_id: int, limit: int = 10) -> List[Dict[str, Any]]:
    return await asyncio.to_thread(_sync_get_recent_turns, session_id, limit)


def get_recent_turns_sync(session_id: int, limit: int = 10) -> List[Dict[str, Any]]:
    """同步版，供 run_collecting 等非异步代码使用。"""
    return _sync_get_recent_turns(session_id, limit)


def format_recent_turns_block(
    session_id: int,
    *,
    limit: int = 10,
    strip_last_user_if_matches: Optional[str] = None,
    heading: str = "近期对话",
) -> str:
    """
    将最近若干条 turns 拼成 prompt 前缀文本（用户：/ 助手：）。
    若本轮用户话已写入 turns 且与 strip_last_user_if_matches 一致，则去掉最后一条用户消息，避免与当前 user 消息重复。
    """
    turns = _sync_get_recent_turns(session_id, limit)
    if strip_last_user_if_matches is not None and turns:
        lu = strip_last_user_if_matches.strip()
        last = turns[-1]
        if (last.get("role") or "").strip() == "user" and (
            (last.get("content") or "").strip() == lu
        ):
            turns = turns[:-1]
    if not turns:
        return ""
    lines: List[str] = [f"{heading}："]
    for t in turns:
        role = (t.get("role") or "").strip()
        c = (t.get("content") or "").strip()
        if not c:
            continue
        if role == "user":
            label = "用户"
        elif role == "assistant":
            label = "助手"
        else:
            label = role or "unknown"
        if len(c) > 1500:
            c = c[:1500] + "…"
        lines.append(f"{label}：{c}")
    return "\n".join(lines) + "\n\n"


def append_turn_sync(
    session_id: int,
    frame_id: Optional[int],
    role: str,
    content: str,
) -> int:
    """同步版 append_turn。"""
    return _sync_append_turn(session_id, frame_id, role, content)
