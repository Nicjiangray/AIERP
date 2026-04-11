"""
engine/prompt_builder.py
统一 prompt 拼装：按 agent_role 从 meta.prompts 取静态片段，按 user_id 从 business.ai_memory 取动态记忆。
"""

from __future__ import annotations

import os
from typing import List, Optional

from tools.database import db_conn

# 主助手注入记忆总长度上限；超出时先裁 session，再裁 longterm，core 不裁（可用环境变量覆盖）
_MEMORY_INJECT_MAX_CHARS = int(os.environ.get("MEMORY_INJECT_MAX_CHARS") or "12000")

_CORE_HEADER = (
    "【核心经验】\n"
    "（以下是你从长期工作中积累的业务规律和操作经验，优先参考）\n"
)


def build_prompt(agent_role: str, user_id: Optional[int] = None) -> str:
    """
    拼装指定 agent 的完整 system prompt。

    参数：
        agent_role: 'assistant' | 'tool_expert' | 'memory_manager'
        user_id: employees.id；assistant 注入记忆；tool_expert 注入当前操作人与角色上下文

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

    # 3. tool_expert：当前操作人（员工、角色、owner_id / 审批 / 主管可见范围）
    if agent_role == "tool_expert" and user_id is not None:
        ctx = _build_tool_expert_operator_context(user_id)
        if ctx:
            parts.append(ctx)

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


def _trim_end(s: str, max_len: int) -> str:
    if max_len <= 0:
        return ""
    if len(s) <= max_len:
        return s
    return s[: max_len - 1].rstrip() + "…"


def _pack_memory(core_text: str, longterm_text: str, session_text: str) -> str:
    parts: list[str] = []
    if core_text.strip():
        parts.append(_CORE_HEADER + core_text.strip())
    if longterm_text.strip():
        parts.append("【长期记忆】\n" + longterm_text.strip())
    if session_text.strip():
        parts.append("【近期要点】\n" + session_text.strip())
    return "\n\n".join(parts)


def _load_user_memory(user_id: int) -> str:
    """
    从 business.ai_memory 拉取用户记忆。
    顺序：core → longterm → session；超长时先裁 session 正文，再裁 longterm 正文，core 正文不裁。
    """
    core_lines: list[str] = []
    longterm_body = ""
    session_lines: list[str] = []

    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT content FROM business.ai_memory
                WHERE user_id = %s AND scope = 'core'
                ORDER BY id
                """,
                (user_id,),
            )
            for r in cur.fetchall():
                if r and r[0] and str(r[0]).strip():
                    core_lines.append(str(r[0]).strip())

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
                longterm_body = str(row[0]).strip()

            cur.execute(
                """
                SELECT content FROM business.ai_memory
                WHERE user_id = %s AND scope = 'session'
                AND created_at >= CURRENT_DATE
                ORDER BY created_at
                """,
                (user_id,),
            )
            for r in cur.fetchall():
                if r and r[0] and str(r[0]).strip():
                    session_lines.append(str(r[0]).strip())

    core_text = "\n".join(core_lines)
    session_text = "\n".join(session_lines)

    packed = _pack_memory(core_text, longterm_body, session_text)
    if not packed:
        return ""
    if len(packed) <= _MEMORY_INJECT_MAX_CHARS:
        return packed

    # core 不裁；先减 session，再减 longterm
    se = session_text
    lt = longterm_body
    budget = _MEMORY_INJECT_MAX_CHARS
    cur_pack = packed
    for _ in range(200):
        cur_pack = _pack_memory(core_text, lt, se)
        if len(cur_pack) <= budget:
            return cur_pack
        if se:
            se = _trim_end(se, max(0, len(se) - max(80, len(se) // 4)))
            continue
        if lt:
            lt = _trim_end(lt, max(0, len(lt) - max(80, len(lt) // 4)))
            continue
        # 仅 core 仍超长：整段返回（满足「core 不裁」）
        return _pack_memory(core_text, "", "")

    return cur_pack


def _tool_expert_role_codes(cur, employee_id: int) -> List[str]:
    cur.execute(
        """
        SELECT DISTINCT rg.role_code
        FROM business.user_role_grants rg
        JOIN business.user_accounts ua ON ua.id = rg.user_account_id
        WHERE ua.employee_id = %s AND ua.is_active = true
        """,
        (employee_id,),
    )
    return [str(r[0]) for r in cur.fetchall() if r and r[0]]


def _fetch_subordinates(cur, manager_employee_id: int) -> List[dict]:
    """直属下属（employees.manager_id）；表或列未就绪时返回空列表。"""
    try:
        cur.execute(
            """
            SELECT id, name FROM business.employees
            WHERE manager_id = %s
              AND (status IS NULL OR LOWER(TRIM(status)) = 'active')
            ORDER BY id
            """,
            (manager_employee_id,),
        )
        return [{"id": int(r[0]), "name": r[1] or ""} for r in cur.fetchall() if r]
    except Exception:
        return []


def _build_tool_expert_operator_context(user_id: int) -> str:
    """tool_expert 注入：操作人、角色、下属与 owner_id 可见范围说明。"""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, emp_code, name, department, role_tags
                FROM business.employees
                WHERE id = %s
                """,
                (user_id,),
            )
            row = cur.fetchone()
            if not row:
                return ""
            eid, emp_code, name, dept, role_tags = row
            roles = _tool_expert_role_codes(cur, int(eid))
            subs = _fetch_subordinates(cur, int(eid))
    lines = [
        "【当前操作人】",
        f"- 员工 id（employees.id，与 SQL 引擎 user_id 一致）: {eid}",
        f"- 工号: {emp_code or ''}  姓名: {name or ''}  部门: {dept or ''}",
    ]
    if role_tags:
        lines.append(f"- 员工标签: {role_tags}")
    if roles:
        lines.append(f"- 账号角色（user_role_grants）: {', '.join(roles)}")
    else:
        lines.append("- 账号角色: （未分配，表级权限可能拒绝）")

    if subs:
        sub_names = "、".join(s["name"] for s in subs if s.get("name"))
        sub_ids = [s["id"] for s in subs]
        id_list = ", ".join(str(i) for i in [eid] + sub_ids)
        lines.append(f"- 下属: {sub_names}")
        lines.append(f"- 数据可见范围（有 owner_id 的表）: owner_id IN ({id_list})")
        lines.append(
            "- 查询这类表时，用上述 IN 条件；不要用仅本人 owner_id = 单值，除非用户只要自己的数据。"
        )
    else:
        lines.append(
            f"- 查询有 owner_id 的表时，通常加 WHERE owner_id = {eid}（除非用户明确要求看他人数据且你有权）。"
        )

    lines.extend(
        [
            "- 写入 customers / contracts / purchase_orders / shipping_notes 时，业务上需要请设置 owner_id 为当前员工 id。",
            "- 新建合同、采购订单在用户确认写入后，系统会将单据置为待审批并生成审批记录；无需在 SQL 中插入 business.approvals。",
        ]
    )
    return "\n".join(lines)
