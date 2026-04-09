"""
统一对话引擎（第一步骨架）：单轮 system + user，业务知识注入，三工具 query_db / ask_user / submit。

不调用 detect_intent、不建 frame、不读 turns；与 collecting 并行存在，供 main 后续切换入口。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

import psycopg2.extras

from engine.agent_tools import load_tools_from_db
from engine.tool_executor import execute_tool_call
from engine.workflow_steps_util import load_workflow_steps_for_intent
from llm.client import chat_with_tools
from tools.database import db_conn, get_session_memo, update_session_memo

logger = logging.getLogger(__name__)

_MAX_TOOL_ROUNDS = 16
_TOOL_NAMES = ("query_db", "ask_user", "submit")

_MEMO_BLOCK = re.compile(r"<memo>\s*([\s\S]*?)\s*</memo>", re.IGNORECASE)


def _try_apply_memo_from_assistant_text(
    session_id: Optional[int], assistant_content: Optional[str]
) -> None:
    """
    从助手正文提取 <memo>...</memo> 内 JSON，成功则浅合并写入 sessions.memo；
    与 collecting._try_apply_memo_from_assistant_text 行为一致。
    """
    if session_id is None:
        return
    text = (assistant_content or "").strip()
    if not text:
        return
    m = _MEMO_BLOCK.search(text)
    if not m:
        return
    raw_json = (m.group(1) or "").strip()
    if not raw_json:
        return
    try:
        obj = json.loads(raw_json)
    except json.JSONDecodeError:
        return
    if not isinstance(obj, dict):
        return
    try:
        sid = int(session_id)
        cur = get_session_memo(sid)
        update_session_memo(sid, {**cur, **obj})
    except Exception:
        logger.debug("universal_engine: session memo 写入跳过", exc_info=True)


def _memo_prompt_suffix() -> str:
    return (
        "\n\n---\n"
        "【会话记忆 memo】若本轮涉及业务要点（如客户、产品、合同、用户意图等），"
        "可在回复末尾追加（纯闲聊可不输出）：\n"
        "<memo>\n"
        '{"entities": {...}, "facts": {...}, "hints": {...}}\n'
        "</memo>\n"
        "其中 entities / facts / hints 为 JSON 对象；整段必须是合法 JSON。"
    )


def _format_fields_for_prompt(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, (dict, list)):
        try:
            s = json.dumps(raw, ensure_ascii=False)
        except TypeError:
            s = str(raw)
    else:
        s = str(raw)
    if len(s) > 1200:
        return s[:1200] + "…"
    return s


def _load_active_workflow_intents() -> List[Dict[str, Any]]:
    """读取 is_active 的 workflows 元数据（intent / 描述）。"""
    with db_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT intent, display_name, description
                FROM business.workflows
                WHERE is_active IS TRUE
                ORDER BY intent
                """
            )
            rows = cur.fetchall()
    return [dict(r) for r in rows]


def _build_business_knowledge_text() -> str:
    """拼接全部 workflow + 各 intent 下 workflow_steps 摘要。"""
    meta_rows = _load_active_workflow_intents()
    chunks: List[str] = []
    for w in meta_rows:
        intent = (w.get("intent") or "").strip()
        if not intent:
            continue
        title = (w.get("display_name") or intent).strip()
        desc = (w.get("description") or "").strip()
        chunks.append(f"### {intent}（{title}）")
        if desc:
            chunks.append(f"- 说明：{desc}")
        steps = load_workflow_steps_for_intent(intent)
        if not steps:
            chunks.append("- 步骤：（无或尚未配置）")
            chunks.append("")
            continue
        for s in steps:
            seq = s.get("seq")
            act = (s.get("action") or "").strip()
            label = (s.get("label") or "").strip()
            tn = (s.get("table_name") or "").strip()
            inst = (s.get("instruction") or "").strip()
            fields = _format_fields_for_prompt(s.get("fields"))
            dk = (s.get("data_key") or "").strip()
            line = f"  - seq={seq} action={act} label={label or '-'}"
            if tn:
                line += f" table={tn}"
            if dk:
                line += f" data_key={dk}"
            chunks.append(line)
            if inst:
                chunks.append(f"    instruction：{inst}")
            if fields:
                chunks.append(f"    fields：{fields}")
        chunks.append("")
    return "\n".join(chunks).strip() or "（当前无可用 workflow 配置）"


def _build_system_prompt(session_id: int, memo: Dict[str, Any]) -> str:
    knowledge = _build_business_knowledge_text()
    memo_txt = ""
    if memo:
        try:
            memo_txt = json.dumps(memo, ensure_ascii=False, indent=2)
        except TypeError:
            memo_txt = str(memo)
    else:
        memo_txt = "{}"

    return (
        "你是 AI·ERP 业务助手。本系统通过三个工具完成工作：\n"
        "1) query_db：只读查询 business / meta（SELECT），用于核对事实、枚举、关联。\n"
        "2) ask_user：信息不足或存在歧义时向用户追问。\n"
        "3) submit：提交写操作预览（新建/更新/删除/合同等），由用户确认后落库；"
        "需要已有任务帧时 frame_id 由系统注入；若当前无帧，submit 会失败，请先完成查询或用 ask_user 说明。\n"
        "请根据下方「业务知识」自主决定调用顺序，不要依赖意图分类器。\n"
        "回复用户时使用自然语言；需要持久化会话要点时按文末说明输出 <memo> 块。\n\n"
        "---\n【业务知识】\n"
        f"{knowledge}\n\n"
        "---\n【当前 sessions.memo】\n"
        f"{memo_txt}"
        + _memo_prompt_suffix()
    )


def _merge_tool_context(
    tool_name: str, arguments: Dict[str, Any], ctx: Dict[str, Any]
) -> Dict[str, Any]:
    """为 submit 等注入 frame_id（若 ctx 提供且参数未带）。"""
    out = dict(arguments)
    if tool_name != "submit":
        return out
    if out.get("frame_id") is not None:
        return out
    fid = ctx.get("frame_id")
    if fid is None:
        return out
    try:
        out["frame_id"] = int(fid)
    except (TypeError, ValueError):
        pass
    return out


def run_chat(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """
    统一引擎入口：system（业务知识 + memo）+ 单条 user，tool 循环最多 16 轮。

    ctx 约定：
      - session_id: int（必填）
      - user_input / user_text：用户本轮输入
      - frame_id：可选；若提供则自动并入 submit 参数（submit 依赖 frame.workflow_intent）

    返回：
      - reply, data, status, session_id
    """
    session_raw = ctx.get("session_id")
    if session_raw is None:
        return {
            "reply": "缺少 session_id。",
            "data": {"main": [], "sub": []},
            "status": "error",
            "session_id": None,
        }
    try:
        session_id = int(session_raw)
    except (TypeError, ValueError):
        return {
            "reply": "session_id 无效。",
            "data": {"main": [], "sub": []},
            "status": "error",
            "session_id": None,
        }

    user_text = (ctx.get("user_input") or ctx.get("user_text") or "").strip()
    if not user_text:
        user_text = " "

    memo = get_session_memo(session_id)
    system_content = _build_system_prompt(session_id, memo)

    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_text},
    ]

    tools_list = load_tools_from_db(list(_TOOL_NAMES))
    out_data: Dict[str, Any] = {"main": [], "sub": []}

    reply_text = ""
    last_assistant: Dict[str, Any] = {}

    for round_i in range(_MAX_TOOL_ROUNDS):
        logger.info(
            "[universal_engine.轮次] round=%s/%s session=%s",
            round_i + 1,
            _MAX_TOOL_ROUNDS,
            session_id,
        )
        assistant_msg = chat_with_tools(
            messages, tools_list, prefer_fallback=False
        )
        last_assistant = assistant_msg if isinstance(assistant_msg, dict) else {}
        assistant_content = last_assistant.get("content")
        _try_apply_memo_from_assistant_text(session_id, assistant_content)

        tcs = last_assistant.get("tool_calls") or []
        if not tcs:
            reply_text = (assistant_content or "").strip() or "好的。"
            break

        messages.append(last_assistant)
        stop_for_ask = False

        for tc in tcs:
            fn = tc.get("function") or {}
            name = (fn.get("name") or "").strip()
            raw_args = fn.get("arguments") or "{}"
            try:
                if isinstance(raw_args, str):
                    args = json.loads(raw_args) if raw_args.strip() else {}
                else:
                    args = dict(raw_args) if isinstance(raw_args, dict) else {}
            except json.JSONDecodeError:
                args = {}

            args = _merge_tool_context(name, args, ctx)
            tid = (tc.get("id") or "").strip() or f"call_{round_i}"

            result = execute_tool_call(name, args)
            logger.info(
                "[universal_engine.tool结果] name=%s keys=%s",
                name,
                list(result.keys()) if isinstance(result, dict) else type(result),
            )

            if name == "query_db" and isinstance(result, dict) and "error" not in result:
                rows = result.get("rows")
                if isinstance(rows, list):
                    out_data["main"] = list(rows)

            if (
                name == "ask_user"
                and isinstance(result, dict)
                and result.get("status") == "pending_user_reply"
            ):
                q = (result.get("question") or "").strip()
                hint = result.get("hint")
                if hint:
                    reply_text = f"{q}\n{hint}"
                else:
                    reply_text = q or "请补充更多信息。"
                stop_for_ask = True

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tid,
                    "content": json.dumps(result, ensure_ascii=False, default=str),
                }
            )

        if stop_for_ask:
            break
    else:
        reply_text = "需要更多信息，请简化需求或分步说明。"

    if not reply_text:
        reply_text = (last_assistant.get("content") or "").strip() or "好的。"

    return {
        "reply": reply_text,
        "data": out_data,
        "status": "done",
        "session_id": session_id,
    }
