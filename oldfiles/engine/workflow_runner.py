"""
把 run_workflow 的执行结果转换为前端可消费的格式。
跑完后由 LLM 一次性用自然语言总结执行情况。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Sequence

from engine.collecting import COLLECTING_CUD_INTENTS
from engine.intent import _normalize_conditions_from_llm, extract_query_params
from engine.workflow import WorkflowStepError, run_workflow
from llm.client import chat_with_fallback
from tools.database import db_conn_llm, get_meta_fields, get_meta_tables

logger = logging.getLogger(__name__)

_IDENT = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
_MASTER_LIMIT = 50
_SUB_LIMIT = 500
_LIKE_ESC = "!"  # PostgreSQL ILIKE ESCAPE，避免与 % 语义混淆


def _validate_ident(name: str) -> bool:
    return bool(name and _IDENT.match(name))


def _like_escape(s: str, esc: str = _LIKE_ESC) -> str:
    if len(esc) != 1:
        raise ValueError("escape must be one character")
    return (
        s.replace(esc, esc + esc).replace("%", esc + "%").replace("_", esc + "_")
    )


def _normalize_conditions_for_run(raw: Any) -> List[Dict[str, str]]:
    """
    context['conditions'] 可能是新版 list[dict] 或旧版 dict。
    输出统一为 [{"field","value","match_type"}, ...]。
    """
    if raw is None:
        return []
    return _normalize_conditions_from_llm(raw)


def _stem_table(name: str) -> str:
    if name.endswith("s") and len(name) > 1:
        return name[:-1]
    return name


def _discover_sub_tables(target_table: str, all_table_names: Sequence[str]) -> List[str]:
    """meta 表名中包含 stem 且以 _items / _details 结尾的子表（如 contracts -> contract_items）。"""
    stem = _stem_table(target_table)
    names = set(all_table_names)
    out: List[str] = []
    for n in sorted(names):
        if n == target_table:
            continue
        if not (n.endswith("_items") or n.endswith("_details")):
            continue
        if n.startswith(stem + "_"):
            out.append(n)
    return out


def _parent_fk_column(parent_table: str) -> str:
    return f"{_stem_table(parent_table)}_id"


def _summarize_query_narrative(
    target_table: str,
    conditions: Any,
    main_rows: List[Dict[str, Any]],
    sub_rows: List[Dict[str, Any]],
    sub_name: Optional[str],
) -> str:
    blob = {
        "target_table": target_table,
        "conditions": conditions,
        "main_row_count": len(main_rows),
        "main_sample": main_rows[:20],
        "sub_table": sub_name,
        "sub_row_count": len(sub_rows),
        "sub_sample": sub_rows[:20],
    }
    system = """你是 ERP 助手。根据下列主数据查询结果，用简洁自然的中文向用户说明：
查了哪张表、用了哪些条件、共多少条；如有子表数据一并概括。不要用 JSON 代码块包裹全文。"""
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(blob, ensure_ascii=False, default=str)},
    ]
    try:
        text = chat_with_fallback(messages)
        return (text or "").strip() or json.dumps(blob, ensure_ascii=False, default=str)[:4000]
    except Exception as e:
        logger.exception("[workflow_runner] 查询结果润色失败: %s", e)
        return f"查询 {target_table} 得到 {len(main_rows)} 条主记录" + (
            f"，子表 {sub_name or ''} {len(sub_rows)} 条。" if sub_rows or sub_name else "。"
        )


def _persist_workflow_task(
    ctx: Dict[str, Any],
    target_table: str,
    conditions: Any,
) -> None:
    user_id = ctx.get("user_id")
    if user_id is None:
        user_id = 2
    intent = (ctx.get("workflow_intent") or "query_master_data")[:100]
    payload = json.dumps(
        {"target_table": target_table, "conditions": conditions},
        ensure_ascii=False,
    )
    with db_conn_llm() as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO business.frames (workflow_intent, user_id, status)
                VALUES (%s, %s, 'completed')
                RETURNING id
                """,
                (intent, int(user_id)),
            )
            row = cur.fetchone()
            frame_id = row[0] if row else None
            if frame_id is None:
                return
            cur.execute(
                """
                INSERT INTO business.slots
                    (frame_id, param_key, param_label, value, status)
                VALUES (%s, %s, %s, %s, 'supplied')
                ON CONFLICT (frame_id, param_key) DO UPDATE SET
                    value = EXCLUDED.value,
                    status = 'supplied'
                """,
                (frame_id, "query_conditions", "主数据查询条件", payload),
            )


def run_query_master_data(context: Dict[str, Any]) -> Dict[str, Any]:
    """
    从自然语言抽取表名与条件，参数化查询 business 主表及推断的子表，
    生成自然语言 reply，并写入 frames / slots。
    """
    ctx = dict(context)
    user_text = (ctx.get("user_input") or ctx.get("user_text") or "").strip()
    if not user_text:
        msg = "请说明要查询的内容。"
        ctx["reply"] = msg
        ctx["data"] = {"main": [], "sub": []}
        return ctx

    ex = extract_query_params(user_text, ctx)
    if ex.get("follow_up"):
        reply = ex["follow_up"]
        ctx["reply"] = reply
        ctx["data"] = {"main": [], "sub": []}
        return ctx

    target_table = (ctx.get("target_table") or "").strip()
    conditions = _normalize_conditions_for_run(ctx.get("conditions"))

    all_meta = get_meta_tables()
    all_names = [str(r["table_name"]) for r in all_meta if r.get("table_name")]

    if not _validate_ident(target_table):
        msg = "无效的表名，请使用字母数字下划线组成的英文表名。"
        ctx["reply"] = msg
        ctx["data"] = {"main": [], "sub": []}
        return ctx

    fields = get_meta_fields(target_table)
    field_names = {str(f["field_name"]) for f in fields if f.get("field_name")}

    where_parts: List[str] = []
    params: List[Any] = []
    for spec in conditions:
        key = (spec.get("field") or "").strip()
        val = spec.get("value")
        mt = (spec.get("match_type") or "contains").strip().lower()
        if mt not in ("exact", "contains"):
            mt = "contains"
        if not key:
            logger.warning(
                "[workflow_runner] 丢弃查询条件：缺少 field（表 %s）spec=%s",
                target_table,
                spec,
            )
            continue
        if not _validate_ident(key):
            logger.warning(
                "[workflow_runner] 丢弃查询条件：字段名 %r 不是合法 SQL 标识符（表 %s）",
                key,
                target_table,
            )
            continue
        if key not in field_names:
            logger.warning(
                "[workflow_runner] 丢弃查询条件：字段 %r 不在 get_meta_fields(%r) "
                "返回的合法字段列表中（LLM 可能误提取；本条 condition 已忽略）",
                key,
                target_table,
            )
            continue
        vs = "" if val is None else str(val)
        if mt == "exact":
            where_parts.append(f'"{key}"::text = %s')
            params.append(vs)
        else:
            pat = "%" + _like_escape(vs, _LIKE_ESC) + "%"
            where_parts.append(
                f'"{key}"::text ILIKE %s ESCAPE \'{_LIKE_ESC}\''
            )
            params.append(pat)

    where_sql = " AND ".join(where_parts) if where_parts else "TRUE"
    limit_clause = f" LIMIT {_MASTER_LIMIT}"

    main_rows: List[Dict[str, Any]] = []
    sub_rows: List[Dict[str, Any]] = []
    sub_name: Optional[str] = None

    try:
        with db_conn_llm() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = 'business' AND table_name = %s
                    """,
                    (target_table,),
                )
                if cur.fetchone() is None:
                    ctx["reply"] = f"业务库中不存在表 business.{target_table}。"
                    ctx["data"] = {"main": [], "sub": []}
                    return ctx

                sql_main = (
                    f'SELECT * FROM business."{target_table}" WHERE {where_sql}'
                    + limit_clause
                )

                cur.execute(sql_main, tuple(params))
                cols = [d[0] for d in cur.description] if cur.description else []
                for row in cur.fetchall():
                    main_rows.append({cols[i]: row[i] for i in range(len(cols))})

                subs = _discover_sub_tables(target_table, all_names)
                for st in subs:
                    if not _validate_ident(st):
                        continue
                    cur.execute(
                        """
                        SELECT 1 FROM information_schema.tables
                        WHERE table_schema = 'business' AND table_name = %s
                        """,
                        (st,),
                    )
                    if cur.fetchone() is None:
                        continue
                    fk = _parent_fk_column(target_table)
                    sub_fields = get_meta_fields(st)
                    sub_field_names = {str(f["field_name"]) for f in sub_fields if f.get("field_name")}
                    if fk not in sub_field_names:
                        continue
                    ids = [r.get("id") for r in main_rows if r.get("id") is not None]
                    sub_name = st
                    if not ids:
                        break
                    cur.execute(
                        f'SELECT * FROM business."{st}" WHERE "{fk}" = ANY(%s) LIMIT {_SUB_LIMIT}',
                        (ids,),
                    )
                    scols = [d[0] for d in cur.description] if cur.description else []
                    for row in cur.fetchall():
                        sub_rows.append({scols[i]: row[i] for i in range(len(scols))})
                    break
    except Exception as e:
        logger.exception("[workflow_runner] run_query_master_data 执行失败: %s", e)
        ctx["reply"] = f"查询执行失败：{e}"
        ctx["data"] = {"main": [], "sub": []}
        ctx["error"] = str(e)
        return ctx

    reply = _summarize_query_narrative(
        target_table, conditions, main_rows, sub_rows, sub_name
    )
    ctx["reply"] = reply
    ctx["data"] = {"main": main_rows, "sub": sub_rows}

    try:
        _persist_workflow_task(ctx, target_table, conditions)
    except Exception as e:
        logger.exception("[workflow_runner] 写入 frames 失败: %s", e)

    return ctx


def _collect_execution_facts(intent: str, ctx: Dict[str, Any]) -> Dict[str, Any]:
    """从 context 汇总可供总结的事实（不依赖 workflow.py 逐步日志）。"""
    business = {
        k: v
        for k, v in ctx.items()
        if not str(k).startswith("workflow_")
    }
    facts: Dict[str, Any] = {
        "intent": intent,
        "configured_total_steps": ctx.get("workflow_total_steps"),
        "workflow_error": ctx.get("workflow_error"),
        "awaiting_user": bool(ctx.get("workflow_awaiting_user")),
        "workflow_user_message": ctx.get("workflow_user_message"),
        "workflow_last_error": ctx.get("workflow_last_error"),
        "failed_step_order": ctx.get("workflow_failed_step_order"),
        "failed_step_name": ctx.get("workflow_failed_step_name"),
        "failed_step_type": ctx.get("workflow_failed_step_type"),
        "business_context": business,
        "status_hint": _infer_status_hint(ctx),
    }
    return facts


def _infer_status_hint(ctx: Dict[str, Any]) -> str:
    if ctx.get("workflow_error"):
        return "工作流加载或配置阶段失败，未进入逐步执行。"
    if ctx.get("workflow_awaiting_user"):
        return "某步 on_fail=ask_user：流程暂停，需用户补充信息。"
    if ctx.get("workflow_last_error"):
        return "至少一步因 on_fail=skip 失败并记录错误，后续步骤可能仍继续执行。"
    if ctx.get("workflow_total_steps") is not None and not ctx.get("workflow_error"):
        return "工作流已跑完配置中的步骤（未因 ask_user 提前返回）。"
    return "请结合各字段判断。"


def _llm_summarize_workflow_result(
    intent: str,
    ctx: Dict[str, Any],
    *,
    error_hint: Optional[str] = None,
) -> str:
    """
    将执行情况组装为 prompt，调用 chat_with_fallback 生成面向用户的中文总结。
    """
    facts = _collect_execution_facts(intent, ctx)
    if error_hint:
        facts["runner_exception_or_fatal"] = error_hint

    system = """你是企业 ERP 工作流助手。根据用户提供的「工作流执行事实」JSON，写一段给用户看的说明正文。
要求（用自然中文，不要用 JSON/Markdown 代码块包裹全文）：
1. 用 ✅ 简要说明哪些环节已顺利（例如业务 context 里已有 user_role、产品/供应商等数据可视为对应步骤已通过；若有 workflow_last_error，说明被跳过的步骤及原因，并说明此前步骤已执行）。
2. 若还缺信息、流程在等待用户、或存在 workflow_error / runner_exception，清楚说明缺什么、原因、建议用户下一步做什么。
3. 若流程已完整完成且无阻塞，明确告知已完成及关键结果摘要。
4. 语气专业、友好，篇幅适中。"""

    user_content = json.dumps(facts, ensure_ascii=False, default=str)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"执行事实如下：\n{user_content}"},
    ]
    try:
        text = chat_with_fallback(messages)
        return (text or "").strip() or _fallback_plain_summary(intent, ctx, error_hint)
    except Exception as e:
        logger.exception("[workflow_runner] LLM 总结失败: %s", e)
        return _fallback_plain_summary(intent, ctx, error_hint)


def _fallback_plain_summary(
    intent: str,
    ctx: Dict[str, Any],
    error_hint: Optional[str],
) -> str:
    """LLM 不可用时退回可读文本。"""
    parts = [f"【{intent}】执行摘要（自动生成）"]
    if error_hint:
        parts.append(f"异常：{error_hint}")
    if ctx.get("workflow_error"):
        parts.append(f"错误：{ctx['workflow_error']}")
    if ctx.get("workflow_awaiting_user"):
        parts.append(ctx.get("workflow_user_message") or "需要您补充信息。")
    elif ctx.get("workflow_last_error"):
        parts.append(f"跳过步骤说明：{ctx['workflow_last_error']}")
    if ctx.get("workflow_total_steps") is not None:
        parts.append(f"配置步骤数：{ctx['workflow_total_steps']}")
    biz = {k: v for k, v in ctx.items() if not str(k).startswith("workflow_")}
    if biz:
        parts.append("当前数据：" + json.dumps(biz, ensure_ascii=False, default=str)[:2000])
    return "\n".join(parts)


def _normalize_workflow_data_payload(raw: Any) -> Dict[str, List[Any]]:
    """统一 data.main / data.sub 为列表。"""
    if not isinstance(raw, dict):
        return {"main": [], "sub": []}
    main = raw.get("main")
    sub = raw.get("sub")
    if not isinstance(main, list):
        main = []
    if not isinstance(sub, list):
        sub = []
    return {"main": main, "sub": sub}


def execute_workflow_for_chat(
    intent: str,
    context: Dict[str, Any],
) -> Dict[str, Any]:
    """
    执行 workflow 并返回统一格式的响应字典，供 /ai/chat 路由直接 return。

    若某步骤（如 query_master_data）已在 context 写入 ``reply``，则不再调用
    ``_llm_summarize_workflow_result``，避免与步骤内文案重复。

    返回格式（含 ``content`` 与 ``reply`` 同值，兼容旧前端）：
    {
        "reply": str,
        "data": {"main": [...], "sub": [...]},
        "content": str,
        "intent": str,
        "requiresConfirm": False,
        "pendingToken": None,
        "workflow_context": dict
    }
    """
    if intent in COLLECTING_CUD_INTENTS or intent == "free_chat":
        from engine.collecting import run_collecting

        context.setdefault("matched_intent", intent)
        context.setdefault("workflow_intent", intent)
        ctx_out = run_collecting(context)
        reply_text = str(ctx_out.get("reply") or "")
        raw_data = ctx_out.get("data")
        if not isinstance(raw_data, dict):
            raw_data = {}
        main_rows = raw_data.get("main")
        sub_rows = raw_data.get("sub")
        if not isinstance(main_rows, list):
            main_rows = []
        if not isinstance(sub_rows, list):
            sub_rows = []
        return {
            "reply": reply_text,
            "data": {"main": main_rows, "sub": sub_rows},
            "content": reply_text,
            "intent": intent,
            "requiresConfirm": False,
            "pendingToken": None,
            "workflow_context": ctx_out,
        }

    if intent == "query_master_data":
        from engine.collecting import run_free_chat

        context = dict(context)
        context.setdefault("matched_intent", intent)
        context.setdefault("workflow_intent", intent)
        context.setdefault("free_chat_intent_label", "query_master_data")
        ctx_out = run_free_chat(context)
        reply_text = str(ctx_out.get("reply") or "")
        raw_data = ctx_out.get("data")
        if not isinstance(raw_data, dict):
            raw_data = {}
        main_rows = raw_data.get("main")
        sub_rows = raw_data.get("sub")
        if not isinstance(main_rows, list):
            main_rows = []
        if not isinstance(sub_rows, list):
            sub_rows = []
        return {
            "reply": reply_text,
            "data": {"main": main_rows, "sub": sub_rows},
            "content": reply_text,
            "intent": intent,
            "requiresConfirm": False,
            "pendingToken": None,
            "workflow_context": ctx_out,
        }

    result_ctx: Dict[str, Any] = {}
    error_hint: Optional[str] = None

    try:
        result_ctx = run_workflow(intent, context)
    except WorkflowStepError as e:
        logger.error("[workflow_runner] 步骤失败: %s", e)
        error_hint = str(e)
        result_ctx = {
            k: v
            for k, v in {
                "workflow_failed_step_order": e.step_order,
                "workflow_failed_step_name": e.step_name,
                "workflow_failed_step_type": e.step_type,
            }.items()
            if v is not None
        }
    except Exception as e:
        logger.exception("[workflow_runner] 未知错误: %s", e)
        error_hint = str(e)
        result_ctx = {}

    empty_data = {"main": [], "sub": []}

    if "reply" in result_ctx:
        reply_text = result_ctx.get("reply")
        if reply_text is None:
            reply_text = ""
        else:
            reply_text = str(reply_text)
        data_out = _normalize_workflow_data_payload(result_ctx.get("data"))
        return {
            "reply": reply_text,
            "data": data_out,
            "content": reply_text,
            "intent": intent,
            "requiresConfirm": False,
            "pendingToken": None,
            "workflow_context": result_ctx,
        }

    try:
        content = _llm_summarize_workflow_result(intent, result_ctx, error_hint=error_hint)
    except Exception as e:
        logger.exception("[workflow_runner] 总结包装失败: %s", e)
        content = _fallback_plain_summary(intent, result_ctx, error_hint or str(e))

    return {
        "reply": content,
        "data": empty_data,
        "content": content,
        "intent": intent,
        "requiresConfirm": False,
        "pendingToken": None,
        "workflow_context": result_ctx,
    }
