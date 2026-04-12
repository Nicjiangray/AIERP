"""
task_manager.py — V9 任务状态机

职责：读写 business.ai_tasks，供 orchestrator（main.py）调用。
LLM 不直接访问此模块，只通过 format_task_context() 生成的文本感知任务状态。

异步函数接收 asyncpg 连接池 pool；其余多为同步风格，
与项目现有 db 访问风格保持一致。
"""

import json
from datetime import datetime, timezone


# ─────────────────────────────────────────────
# 查询
# ─────────────────────────────────────────────

async def get_task_progress_text(pool, session_id: int, user_id: int) -> str:
    """
    返回当前 session 下 status=running 任务的进度一行文案；无则 ""。
    若 plan 中全部步骤均为 completed，则调用 complete_task 并返回「任务全部完成」。
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, plan
            FROM business.ai_tasks
            WHERE session_id = $1 AND user_id = $2 AND status = 'running'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            session_id,
            user_id,
        )
    if row is None:
        return ""
    plan = row["plan"]
    if isinstance(plan, str):
        plan = json.loads(plan)
    task_id = row["id"]
    if not plan:
        return ""
    completed = sum(1 for s in plan if s.get("status") == "completed")
    total = len(plan)
    if completed == total:
        await complete_task(pool, task_id, "任务全部完成")
        return "任务全部完成"
    next_intent = ""
    for s in plan:
        if s.get("status") == "pending":
            next_intent = (s.get("intent") or "").strip()
            break
    if not next_intent:
        next_intent = "（待定）"
    return f"任务进度：第{completed}步已完成 / 共{total}步，下一步：[{next_intent}]"


async def get_active_task(pool, session_id: int) -> dict | None:
    """
    返回当前 session 中状态为 planning 或 running 的最新任务。
    若无活跃任务返回 None。
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, session_id, user_id, status,
                   original_intent, plan, current_step,
                   replan_count, max_replans, final_summary,
                   created_at, updated_at
            FROM business.ai_tasks
            WHERE session_id = $1
              AND status IN ('planning', 'running')
            ORDER BY created_at DESC
            LIMIT 1
            """,
            session_id,
        )
    if row is None:
        return None
    task = dict(row)
    # asyncpg 返回 JSONB 为字符串，统一解析为 list
    if isinstance(task["plan"], str):
        task["plan"] = json.loads(task["plan"])
    return task


# ─────────────────────────────────────────────
# 写入
# ─────────────────────────────────────────────

async def create_task(pool, session_id: int, user_id: int,
                      original_intent: str, steps: list[str]) -> dict:
    """
    新建任务记录，status=running。

    steps: 步骤意图字符串列表，如 ["创建员工张圣忠", "创建账号zhangsz"]
    返回新建的 task dict。
    """
    plan = [
        {
            "step":    i + 1,       # 1-based，展示用
            "intent":  s,
            "details": "",          # orchestrator 后续注入上一步 returning 数据
            "status":  "pending",
            "result":  None,
        }
        for i, s in enumerate(steps)
    ]
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO business.ai_tasks
                (session_id, user_id, status, original_intent, plan, current_step)
            VALUES ($1, $2, 'running', $3, $4::jsonb, 0)
            RETURNING id, session_id, user_id, status,
                      original_intent, plan, current_step,
                      replan_count, max_replans, final_summary,
                      created_at, updated_at
            """,
            session_id, user_id, original_intent, json.dumps(plan, ensure_ascii=False),
        )
    task = dict(row)
    if isinstance(task["plan"], str):
        task["plan"] = json.loads(task["plan"])
    return task


async def update_step_result(pool, task_id: int, step_idx: int,
                              step_status: str, result: dict | None) -> None:
    """
    更新 plan[step_idx] 的 status 与 result，同时刷新 updated_at。
    step_status: 'running' | 'completed' | 'failed'
    result: confirm 返回的 returning 数据，或 {'error': '...'} 失败原因
    """
    async with pool.acquire() as conn:
        # 先取出当前 plan
        row = await conn.fetchrow(
            "SELECT plan FROM business.ai_tasks WHERE id = $1", task_id
        )
        plan = json.loads(row["plan"]) if isinstance(row["plan"], str) else list(row["plan"])
        plan[step_idx]["status"] = step_status
        plan[step_idx]["result"] = result

        await conn.execute(
            """
            UPDATE business.ai_tasks
            SET plan = $1::jsonb, updated_at = NOW()
            WHERE id = $2
            """,
            json.dumps(plan, ensure_ascii=False), task_id,
        )


async def advance_step(pool, task_id: int) -> None:
    """current_step + 1，updated_at 刷新。"""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE business.ai_tasks
            SET current_step = current_step + 1, updated_at = NOW()
            WHERE id = $1
            """,
            task_id,
        )


async def replan_task(pool, task_id: int, new_steps: list[str]) -> None:
    """
    重新规划：替换 plan，重置 current_step=0，replan_count+1。
    若 replan_count 已达 max_replans，调用方应先判断，不要直接调此函数。
    """
    plan = [
        {
            "step":    i + 1,
            "intent":  s,
            "details": "",
            "status":  "pending",
            "result":  None,
        }
        for i, s in enumerate(new_steps)
    ]
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE business.ai_tasks
            SET plan          = $1::jsonb,
                current_step  = 0,
                replan_count  = replan_count + 1,
                status        = 'running',
                updated_at    = NOW()
            WHERE id = $2
            """,
            json.dumps(plan, ensure_ascii=False), task_id,
        )


async def complete_task(pool, task_id: int, summary: str) -> None:
    """所有步骤完成，写入 final_summary，status=completed。"""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE business.ai_tasks
            SET status        = 'completed',
                final_summary = $1,
                updated_at    = NOW()
            WHERE id = $2
            """,
            summary, task_id,
        )


async def fail_task(pool, task_id: int, reason: str) -> None:
    """任务失败，写入失败原因到 final_summary，status=failed。"""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE business.ai_tasks
            SET status        = 'failed',
                final_summary = $1,
                updated_at    = NOW()
            WHERE id = $2
            """,
            reason, task_id,
        )


# ─────────────────────────────────────────────
# 判断
# ─────────────────────────────────────────────

def is_last_step(task: dict) -> bool:
    """当前步骤是否为最后一步。"""
    return task["current_step"] == len(task["plan"]) - 1


def all_steps_completed(task: dict) -> bool:
    """plan 中所有步骤是否均为 completed。"""
    return all(s["status"] == "completed" for s in task["plan"])


def replan_limit_reached(task: dict) -> bool:
    """重新规划次数是否已达上限。"""
    return task["replan_count"] >= task["max_replans"]


# ─────────────────────────────────────────────
# 格式化（生成注入 LLM 的文本）
# ─────────────────────────────────────────────

def format_task_context(task: dict) -> str:
    """
    生成 [TASK_STATUS] 文本，由 orchestrator 注入主助手的 user 消息。
    LLM 通过此文本感知任务状态，不直接访问 ai_tasks 表。
    """
    plan   = task["plan"]
    total  = len(plan)
    cur    = task["current_step"]

    lines = [
        "[TASK_STATUS]",
        f"任务：{task['original_intent']}",
        "步骤：",
    ]

    for s in plan:
        idx    = s["step"] - 1   # 0-based
        icon   = {"completed": "✓", "failed": "✗", "running": "→", "pending": "○"}.get(s["status"], "○")
        result = s.get("result")

        if s["status"] == "completed" and result:
            # 把 returning 数据格式化为 key=value 字符串
            result_str = ", ".join(f"{k}={v}" for k, v in result.items())
            lines.append(f"  步骤{s['step']} {s['intent']} {icon} 已完成，{result_str}")
        elif s["status"] == "failed" and result:
            err = result.get("error", "未知错误")
            lines.append(f"  步骤{s['step']} {s['intent']} {icon} 失败：{err}")
        else:
            lines.append(f"  步骤{s['step']} {s['intent']} {icon}")

    # 进度提示
    completed_count = sum(1 for s in plan if s["status"] == "completed")
    lines.append(f"当前进度：{completed_count}/{total}")

    # 最后一步提示，帮助主助手判断完成后进入汇总模式
    if is_last_step(task):
        lines.append("提示：当前为最后一步，完成后请汇总整体结果。")

    lines.append("[/TASK_STATUS]")
    return "\n".join(lines)


# ─────────────────────────────────────────────
# 解析主助手输出的 [PLAN] 块
# ─────────────────────────────────────────────

def parse_plan(text: str) -> tuple[str, list[str]] | tuple[None, None]:
    """
    从主助手回复中提取 [PLAN] 块，返回 (intent, steps) 元组。
    解析失败返回 (None, None)。

    期望格式：
        [PLAN]
        intent: 建账号zhangsz
        steps:
          1. 创建员工张圣忠
          2. 创建账号zhangsz
        [/PLAN]
    """
    import re

    match = re.search(r"\[PLAN\](.*?)\[/PLAN\]", text, re.DOTALL)
    if not match:
        return None, None

    body   = match.group(1).strip()
    intent = None
    steps  = []

    for line in body.splitlines():
        line = line.strip()
        if line.lower().startswith("intent:"):
            intent = line[len("intent:"):].strip()
        elif re.match(r"^\d+\.", line):
            # 去掉序号，只保留意图文本
            step_text = re.sub(r"^\d+\.\s*", "", line).strip()
            if step_text:
                steps.append(step_text)

    if intent and steps:
        return intent, steps
    return None, None
