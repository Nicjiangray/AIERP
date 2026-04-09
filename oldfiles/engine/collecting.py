"""
create_simple_entity：collecting 流程（frames / slots + 写库）。
同步函数；数据库读用 db_conn()，业务表写入用 db_conn_llm()。
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set, Tuple

import psycopg2.errors
import psycopg2.extras
from psycopg2 import sql as psql

from engine.agent_tools import load_tools_from_db
from engine.context_builder import build_table_detail
from engine.field_normalization import normalize_products_category
from engine.tool_executor import execute_tool_call
from engine.turns import get_recent_turns_sync
from llm.client import chat_with_fallback, chat_with_tools
from tools.database import (
    db_conn,
    db_conn_llm,
    get_session_memo,
    update_session_memo,
)

logger = logging.getLogger(__name__)

# 与 main._sync_ai_confirm 取消分支 DELETE … source = %s 对齐，仅清理预览写入
SLOT_SOURCE_AGENT_PREVIEW = "agent_preview"

# create/update/delete 类 collecting（与 main / workflow_runner 分流一致）
COLLECTING_CUD_INTENTS = frozenset(
    {
        "create_simple_entity",
        "update_simple_entity",
        "delete_simple_entity",
        "create_contract",
        "update_contract",
        "delete_contract",
    }
)
_FREE_CHAT_TOOL_NAMES = ("query_db", "ask_user")

_IDENT = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
_MEMO_BLOCK = re.compile(r"<memo>\s*([\s\S]*?)\s*</memo>", re.IGNORECASE)


def _validate_ident(name: str) -> bool:
    return bool(name and _IDENT.match(name))


def _allowed_values_as_dict(raw: Any) -> Optional[Dict[str, Any]]:
    """meta_fields.allowed_values 多为 JSON 对象；非 dict 时返回 None。"""
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw if raw else None
    if isinstance(raw, str) and raw.strip():
        try:
            j = json.loads(raw)
            return j if isinstance(j, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def _enhance_param_label_for_prompt(param_label: str, allowed_values: Any) -> str:
    """拼 LLM 字段说明：有枚举时附加「只能填以下值之一：k=v, ...」。"""
    label = (param_label or "").strip() or "该字段"
    av = _allowed_values_as_dict(allowed_values)
    if not av:
        return label
    opts = ", ".join(f"{k}={v}" for k, v in av.items())
    if not opts:
        return label
    return f"{label}，只能填以下值之一：{opts}"


def _followup_enum_line(allowed_values: Any) -> str:
    """追问第二行：可选：special（增值税专用发票）/ normal（普通发票）"""
    av = _allowed_values_as_dict(allowed_values)
    if not av:
        return ""
    parts = [f"{k}（{v}）" for k, v in av.items()]
    if not parts:
        return ""
    return "可选：" + " / ".join(parts)


def _format_messages_history_for_prompt(raw: Any) -> str:
    """将 messages_history 格式化为「对话历史供参考：[assistant: … user: …]」。"""
    if not raw:
        return ""
    if not isinstance(raw, list):
        return ""
    lines: List[str] = []
    for m in raw[-10:]:
        if not isinstance(m, dict):
            continue
        role = (m.get("role") or "").strip()
        content = (m.get("content") or "").strip()
        if not content:
            continue
        if len(content) > 500:
            content = content[:500] + "…"
        lines.append(f"{role}: {content}")
    if not lines:
        return ""
    inner = " ".join(lines)
    return f"对话历史供参考：[{inner}]"


def _try_apply_memo_from_assistant_text(
    session_id: Optional[int], assistant_content: Optional[str]
) -> None:
    """
    从助手正文提取 <memo>...</memo> 内 JSON，成功则浅合并写入 sessions.memo；
    无块、解析失败或非对象时不改库、不抛错。
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
        logger.debug("collecting: session memo 写入跳过", exc_info=True)


def _collecting_memo_prompt_suffix() -> str:
    return (
        "\n\n---\n"
        "【会话记忆 memo】若本轮涉及业务要点（如客户、产品、合同、数量、价格、用户意图等），"
        "请在回复末尾追加一段（纯闲聊可不输出）：\n"
        "<memo>\n"
        '{"entities": {...}, "facts": {...}, "hints": {...}}\n'
        "</memo>\n"
        "其中 entities / facts / hints 为 JSON 对象，按需填写键值；整段必须是合法 JSON。"
        "不要在此块外重复大段结构化数据。"
    )


def _parse_llm_json_object(text: str) -> Dict[str, Any]:
    raw = (text or "").strip()
    raw = _JSON_FENCE.sub("", raw).strip()
    # 尝试截取第一个 { ... } 块
    if not raw.startswith("{"):
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            raw = m.group(0)
    obj = json.loads(raw)
    if not isinstance(obj, dict):
        raise ValueError("LLM 返回不是 JSON 对象")
    return obj


def _load_simple_table_whitelist() -> Set[str]:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_name FROM meta.meta_tables
                WHERE entity_type = 'simple'
                """
            )
            return {str(r[0]) for r in cur.fetchall() if r and r[0]}


def _load_delete_entity_whitelist() -> Set[str]:
    """delete_entity：simple 实体 + 合同主表（意图 delete_contract 专用）。"""
    return _load_simple_table_whitelist() | {"contracts"}


def _get_meta_table_row(table_name: str) -> Optional[Dict[str, Any]]:
    with db_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT table_name, table_cn, description
                FROM meta.meta_tables
                WHERE table_name = %s AND entity_type = 'simple'
                """,
                (table_name,),
            )
            row = cur.fetchone()
            return dict(row) if row else None


def _get_meta_table_row_any(table_name: str) -> Optional[Dict[str, Any]]:
    """不限制 entity_type（如 contracts 非 simple 时取中文名）。"""
    with db_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT table_name, table_cn, description
                FROM meta.meta_tables
                WHERE table_name = %s
                LIMIT 1
                """,
                (table_name,),
            )
            row = cur.fetchone()
            return dict(row) if row else None


def _parse_related_tables_raw(raw: Any) -> List[Dict[str, Any]]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    if isinstance(raw, str) and raw.strip():
        try:
            j = json.loads(raw)
            return j if isinstance(j, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _load_workflow_context_schema(intent: str) -> Optional[Dict[str, Any]]:
    if not (intent or "").strip():
        return None
    try:
        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT context_schema
                    FROM business.workflows
                    WHERE intent = %s
                    LIMIT 1
                    """,
                    (intent.strip(),),
                )
                row = cur.fetchone()
        if not row or row[0] is None:
            return None
        raw = row[0]
        if isinstance(raw, str):
            raw = json.loads(raw)
        if not isinstance(raw, dict):
            return None
        return raw
    except psycopg2.errors.UndefinedColumn:
        return None
    except Exception:
        logger.warning(
            "读取 workflows.context_schema 失败，将使用 collecting 内置 fallback",
            exc_info=True,
        )
        return None


def _load_workflow_steps(intent: str) -> List[Dict[str, Any]]:
    """从 business.workflow_steps 读取某意图的步骤（按 seq）；表不存在或异常时返回 []。"""
    if not (intent or "").strip():
        return []
    sql_full = """
                    SELECT id, workflow_intent, seq, action, label, table_name, instruction, tool,
                           fields, validation_rules, input_fields, output_fields, depends_on
                    FROM business.workflow_steps
                    WHERE workflow_intent = %s
                    ORDER BY seq ASC
                    """
    sql_legacy = """
                    SELECT id, workflow_intent, seq, action, label, table_name, instruction, tool,
                           fields, validation_rules
                    FROM business.workflow_steps
                    WHERE workflow_intent = %s
                    ORDER BY seq ASC
                    """
    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                try:
                    cur.execute(sql_full, (intent.strip(),))
                except psycopg2.errors.UndefinedColumn:
                    cur.execute(sql_legacy, (intent.strip(),))
                rows = cur.fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            tn = (d.get("table_name") or "").strip()
            if tn:
                d["table"] = tn
            out.append(d)
        return out
    except psycopg2.errors.UndefinedTable:
        return []
    except Exception:
        logger.warning(
            "读取 business.workflow_steps 失败，将使用 collecting 内置 fallback",
            exc_info=True,
        )
        return []


def _generic_tool_constraints_block() -> str:
    return (
        "\n【通用工具与数据约束】\n"
        "1. 不要编造业务表记录 id；record_id / customer_id / contract_id 等须来自 query_db 的查询结果。\n"
        "2. 不要编造 contract_no；新建合同编号由系统生成。\n"
        "3. 字段值须符合上文「参考流程」中的类型与 allowed_values。\n"
        "4. 每一轮对话须至少调用允许工具之一，不要只输出文字而不调用工具。\n"
    )


def _format_context_field_line(fd: Dict[str, Any]) -> str:
    fn = (fd.get("field") or "").strip()
    fcn = (fd.get("field_cn") or "").strip() or fn
    tp = (fd.get("type") or "").strip() or "varchar"
    req = "，必填" if fd.get("required") else ""
    av = fd.get("allowed_values")
    avs = ""
    if av is not None and av != [] and av != {}:
        try:
            avs = "，allowed_values：" + json.dumps(av, ensure_ascii=False)
        except (TypeError, ValueError):
            avs = f"，allowed_values：{av!r}"
    return f"  - {fcn}（`{fn}`）：类型 {tp}{req}{avs}\n"


def _build_system_prompt_from_context_schema(
    ctx: Dict[str, Any],
    schema: Dict[str, Any],
    steps: List[Dict[str, Any]],
) -> str:
    wi = (ctx.get("workflow_intent") or ctx.get("matched_intent") or "").strip()
    table_name = (ctx.get("target_table") or "").strip()
    table_cn = (ctx.get("table_cn") or "").strip() or table_name
    fid = ctx.get("frame_id")
    slots: List[Dict[str, Any]] = []
    if fid is not None:
        slots = _load_slots_for_agent(int(fid))
    supplied = [s for s in slots if (s.get("status") or "") == "supplied"]
    pending = [s for s in slots if (s.get("status") or "") == "pending"]

    parts: List[str] = [
        "你是 ERP 数据收集助手。以下「参考流程」「业务规则」由系统根据当前意图预置；"
        "请严格使用允许列表中的工具完成任务。\n\n",
        "## 参考流程\n",
    ]
    sorted_steps = sorted(
        [s for s in steps if isinstance(s, dict)],
        key=lambda s: int(s.get("seq") or 0),
    )
    for st in sorted_steps:
        seq = st.get("seq")
        label = (st.get("label") or "").strip()
        action = (st.get("action") or "").strip()
        tbl = (st.get("table") or st.get("table_name") or "").strip()
        instr = (st.get("instruction") or "").strip()
        parts.append(f"### 步骤 {seq} [{action}] {label}\n")
        if tbl:
            parts.append(f"- 表：`{tbl}`\n")
        if instr:
            parts.append(f"- 说明：{instr}\n")
        flds = st.get("fields")
        if isinstance(flds, list) and flds:
            parts.append("- 字段：\n")
            for fd in flds:
                if isinstance(fd, dict):
                    parts.append(_format_context_field_line(fd))
        tool = (st.get("tool") or "").strip()
        if action == "submit" and tool:
            parts.append(f"- 提交工具：`{tool}`\n")
        parts.append("\n")

    prules = schema.get("prompt_rules")
    if isinstance(prules, list) and prules:
        parts.append("## 业务规则\n")
        for i, rule in enumerate(prules, 1):
            if rule:
                parts.append(f"{i}. {rule}\n")
        parts.append("\n")

    parts.append(_generic_tool_constraints_block())

    at = schema.get("allowed_tools")
    blob = {
        "task": f"{wi}（collecting）",
        "workflow_intent": wi,
        "target_table": table_name,
        "table_cn": table_cn,
        "slots_supplied": supplied,
        "slots_pending": pending,
        "allowed_tools": at if isinstance(at, list) else None,
    }
    parts.append(
        "\n"
        + json.dumps(blob, ensure_ascii=False, default=str)
    )
    return "".join(parts)


_DEFAULT_CONTRACT_RELATED_TABLES = (
    "customers",
    "products",
    "contract_items",
    "contract_changes",
)


def _related_table_names_for_contracts() -> List[str]:
    """从 meta.meta_tables 中 contracts.related_tables 读取关联表名（去重、不含 contracts）。"""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT related_tables FROM meta.meta_tables
                WHERE table_name = 'contracts'
                LIMIT 1
                """
            )
            row = cur.fetchone()
    if not row or row[0] is None:
        return list(_DEFAULT_CONTRACT_RELATED_TABLES)
    items = _parse_related_tables_raw(row[0])
    seen: Set[str] = set()
    out: List[str] = []
    for item in items:
        t = (item.get("table") or "").strip()
        if not t or t == "contracts":
            continue
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out if out else list(_DEFAULT_CONTRACT_RELATED_TABLES)


def _build_contract_related_field_details_block() -> str:
    """
    create_contract / update_contract：在主表 contracts 字段清单之后注入关联表字段清单
   （与 build_table_detail 主表格式一致）。
    """
    names = _related_table_names_for_contracts()
    chunks: List[str] = []
    for tn in names:
        block = build_table_detail(tn)
        if block.strip():
            chunks.append(block)
    if not chunks:
        return ""
    intro = (
        "## 关联表字段（与合同相关的 business 表；列名以 meta 为准："
        "customers 客户名称列为 `name`，products 产品名称列为 `product_name`）\n\n"
    )
    return intro + "\n\n".join(chunks) + "\n\n"


def _list_simple_tables_for_prompt() -> List[Dict[str, Any]]:
    with db_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT table_name, table_cn, description
                FROM meta.meta_tables
                WHERE entity_type = 'simple'
                ORDER BY table_name
                """
            )
            return [dict(r) for r in cur.fetchall()]


def _extract_target_table(ctx: Dict[str, Any]) -> str:
    """
    LLM 从用户输入提取目标表名（英文），必须在 simple 表白名单中。
    """
    user_text = (ctx.get("user_input") or ctx.get("user_text") or "").strip()
    tables = _list_simple_tables_for_prompt()
    if not tables:
        raise RuntimeError("元数据中无 entity_type=simple 的表，无法新建。")

    blob = {
        "user_input": user_text,
        "candidate_tables": tables,
    }
    system = (
        "你是 ERP 元数据助手。根据用户输入，从 candidate_tables 中选出唯一目标表，"
        "只输出一个 JSON 对象，不要其它文字："
        '{"table_name":"<英文表名>"} 。\n'
        "table_name 必须是 candidate_tables 里某一项的 table_name，例如 customers / products / contacts。"
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(blob, ensure_ascii=False)},
    ]
    text = chat_with_fallback(messages)
    data = _parse_llm_json_object(text)
    raw_name = (data.get("table_name") or data.get("table") or "").strip()
    if not raw_name:
        raise ValueError("未能从输入中确定目标表")

    allowed = _load_simple_table_whitelist()
    if raw_name not in allowed:
        raise ValueError(f"目标表 {raw_name!r} 不在白名单内，请明确说要新建客户、产品还是联系人。")

    return raw_name


def _init_slots(frame_id: int, table_name: str) -> None:
    with db_conn() as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT field_name,
                       COALESCE(NULLIF(field_cn, ''), field_name) AS label
                FROM meta.meta_fields
                WHERE table_name = %s AND ask_if_missing = true
                ORDER BY id
                """,
                (table_name,),
            )
            rows = cur.fetchall()
            for field_name, label in rows:
                fn = str(field_name)
                if not _validate_ident(fn):
                    continue
                cur.execute(
                    """
                    INSERT INTO business.slots
                        (frame_id, param_key, param_label, status, source)
                    VALUES (%s, %s, %s, 'pending', 'system_default')
                    ON CONFLICT (frame_id, param_key) DO NOTHING
                    """,
                    (frame_id, fn, str(label)),
                )


def get_or_create_frame(workflow_ctx: Dict[str, Any]) -> Dict[str, Any]:
    """
    查找或新建 collecting 状态的 frame，写入 frame_id / target_table 等。
    """
    ctx = workflow_ctx
    user_id = int(ctx.get("user_id") or 2)
    matched = (ctx.get("matched_intent") or ctx.get("workflow_intent") or "").strip()
    if not matched:
        raise ValueError("缺少 matched_intent / workflow_intent")

    session_id = ctx.get("session_id")

    with db_conn() as conn:
        conn.autocommit = True
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, subject, context_snapshot, updated_at
                FROM business.frames
                WHERE user_id = %s
                  AND workflow_intent = %s
                  AND status = 'collecting'
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (user_id, matched),
            )
            row = cur.fetchone()

        if row:
            fid = int(row["id"])
            ctx["frame_id"] = fid
            ctx["subject"] = row.get("subject") or ""
            snap = row.get("context_snapshot")
            if isinstance(snap, str):
                try:
                    snap = json.loads(snap)
                except json.JSONDecodeError:
                    snap = {}
            if not isinstance(snap, dict):
                snap = {}
            tt = snap.get("target_table")
            if isinstance(tt, str) and tt.strip():
                ctx["target_table"] = tt.strip()
                meta = _get_meta_table_row(ctx["target_table"])
                if not meta and ctx["target_table"] == "contracts":
                    meta = _get_meta_table_row_any("contracts")
                ctx["table_cn"] = (meta.get("table_cn") if meta else "") or ""
            else:
                if matched in ("query_master_data", "free_chat"):
                    ctx["target_table"] = ""
                    ctx["table_cn"] = ""
                else:
                    raise ValueError("会话数据不完整（缺少目标表），请重新开始。")
            return ctx

    if matched == "free_chat":
        subject = "自由对话"
        snap = json.dumps({"target_table": ""}, ensure_ascii=False)
        with db_conn() as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO business.frames
                        (workflow_intent, user_id, status, subject, session_id, context_snapshot)
                    VALUES (%s, %s, 'collecting', %s, %s, %s::jsonb)
                    RETURNING id
                    """,
                    (matched, user_id, subject, session_id, snap),
                )
                new_id = cur.fetchone()[0]
        fid = int(new_id)
        ctx["frame_id"] = fid
        ctx["target_table"] = ""
        ctx["table_cn"] = ""
        ctx["subject"] = subject
        return ctx

    if matched == "query_master_data":
        preset = (ctx.get("target_table") or "").strip()
        allowed_simple = _load_simple_table_whitelist()
        table_name = ""
        if preset and _validate_ident(preset) and preset in allowed_simple:
            table_name = preset
        else:
            try:
                from engine.intent import infer_target_table_from_user_text

                ut = (ctx.get("user_input") or ctx.get("user_text") or "").strip()
                inf = infer_target_table_from_user_text(ut)
                if inf and inf in allowed_simple:
                    table_name = inf
            except Exception:
                pass
        table_cn = ""
        meta = None
        if table_name:
            meta = _get_meta_table_row(table_name)
            if meta:
                table_cn = (meta.get("table_cn") or table_name).strip()
        subject = "主数据查询" if not table_cn else f"{table_cn}查询"
        snap = json.dumps({"target_table": table_name}, ensure_ascii=False)

        with db_conn() as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO business.frames
                        (workflow_intent, user_id, status, subject, session_id, context_snapshot)
                    VALUES (%s, %s, 'collecting', %s, %s, %s::jsonb)
                    RETURNING id
                    """,
                    (matched, user_id, subject, session_id, snap),
                )
                new_id = cur.fetchone()[0]

        fid = int(new_id)
        ctx["frame_id"] = fid
        ctx["target_table"] = table_name
        ctx["table_cn"] = table_cn
        ctx["subject"] = subject
        if table_name:
            _init_slots(fid, table_name)
        return ctx

    if matched == "create_contract":
        subject = "合同创建"
        snap = json.dumps({"target_table": "contracts"}, ensure_ascii=False)
        meta_c = _get_meta_table_row("contracts") or _get_meta_table_row_any("contracts")
        table_cn = ((meta_c.get("table_cn") if meta_c else "") or "合同").strip()
        with db_conn() as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO business.frames
                        (workflow_intent, user_id, status, subject, session_id, context_snapshot)
                    VALUES (%s, %s, 'collecting', %s, %s, %s::jsonb)
                    RETURNING id
                    """,
                    (matched, user_id, subject, session_id, snap),
                )
                new_id = cur.fetchone()[0]
        fid = int(new_id)
        ctx["frame_id"] = fid
        ctx["target_table"] = "contracts"
        ctx["table_cn"] = table_cn
        ctx["subject"] = subject
        return ctx

    if matched == "update_contract":
        subject = "合同修改"
        snap = json.dumps({"target_table": "contracts"}, ensure_ascii=False)
        meta_c = _get_meta_table_row("contracts") or _get_meta_table_row_any("contracts")
        table_cn = ((meta_c.get("table_cn") if meta_c else "") or "合同").strip()
        with db_conn() as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO business.frames
                        (workflow_intent, user_id, status, subject, session_id, context_snapshot)
                    VALUES (%s, %s, 'collecting', %s, %s, %s::jsonb)
                    RETURNING id
                    """,
                    (matched, user_id, subject, session_id, snap),
                )
                new_id = cur.fetchone()[0]
        fid = int(new_id)
        ctx["frame_id"] = fid
        ctx["target_table"] = "contracts"
        ctx["table_cn"] = table_cn
        ctx["subject"] = subject
        return ctx

    if matched == "delete_contract":
        subject = "合同删除"
        snap = json.dumps({"target_table": "contracts"}, ensure_ascii=False)
        meta_c = _get_meta_table_row("contracts") or _get_meta_table_row_any("contracts")
        table_cn = ((meta_c.get("table_cn") if meta_c else "") or "合同").strip()
        with db_conn() as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO business.frames
                        (workflow_intent, user_id, status, subject, session_id, context_snapshot)
                    VALUES (%s, %s, 'collecting', %s, %s, %s::jsonb)
                    RETURNING id
                    """,
                    (matched, user_id, subject, session_id, snap),
                )
                new_id = cur.fetchone()[0]
        fid = int(new_id)
        ctx["frame_id"] = fid
        ctx["target_table"] = "contracts"
        ctx["table_cn"] = table_cn
        ctx["subject"] = subject
        return ctx

    preset = (ctx.get("target_table") or "").strip()
    allowed_simple = _load_simple_table_whitelist()
    if preset and _validate_ident(preset) and preset in allowed_simple:
        table_name = preset
    else:
        table_name = _extract_target_table(ctx)
    meta = _get_meta_table_row(table_name)
    if not meta:
        raise ValueError(f"表 {table_name!r} 无元数据或不是 simple 实体")
    table_cn = (meta.get("table_cn") or table_name).strip()
    if matched == "update_simple_entity":
        subject = f"{table_cn}修改"
    elif matched == "delete_simple_entity":
        subject = f"{table_cn}删除"
    else:
        subject = f"{table_cn}新建"

    snap = json.dumps({"target_table": table_name}, ensure_ascii=False)

    with db_conn() as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO business.frames
                    (workflow_intent, user_id, status, subject, session_id, context_snapshot)
                VALUES (%s, %s, 'collecting', %s, %s, %s::jsonb)
                RETURNING id
                """,
                (matched, user_id, subject, session_id, snap),
            )
            new_id = cur.fetchone()[0]

    fid = int(new_id)
    ctx["frame_id"] = fid
    ctx["target_table"] = table_name
    ctx["table_cn"] = table_cn
    ctx["subject"] = subject
    _init_slots(fid, table_name)
    return ctx


def fill_slots_from_input(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """LLM 从用户输入填 pending slots。"""
    frame_id = ctx.get("frame_id")
    if frame_id is None:
        ctx["filled_count"] = 0
        return ctx

    user_text = (ctx.get("user_input") or ctx.get("user_text") or "").strip()

    with db_conn() as conn:
        conn.autocommit = True
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT s.param_key, s.param_label, mf.allowed_values
                FROM business.slots s
                LEFT JOIN meta.meta_fields mf
                    ON mf.table_name = (
                        SELECT (context_snapshot->>'target_table')
                        FROM business.frames
                        WHERE id = %s
                    )
                    AND mf.field_name = s.param_key
                WHERE s.frame_id = %s AND s.status = 'pending'
                ORDER BY s.id
                """,
                (int(frame_id), int(frame_id)),
            )
            pending = cur.fetchall()

    if not pending:
        ctx["filled_count"] = 0
        return ctx

    fields_blob = [
        {
            "param_key": r["param_key"],
            "param_label": _enhance_param_label_for_prompt(
                str(r.get("param_label") or ""),
                r.get("allowed_values"),
            ),
        }
        for r in pending
    ]
    system = (
        "你是 ERP 数据录入助手。根据用户输入，为下列待填字段提取值。"
        "只输出一个 JSON 对象：键为 param_key（英文），值为用户提供的字符串；"
        "若某字段标明「只能填以下值之一」，JSON 的值必须是其中的英文键（如 special、normal），"
        "不要用中文描述。"
        "没有提到的字段不要出现在 JSON 中。不要 Markdown 代码块以外的废话。\n"
        "【相关性】只提取与字段明确相关的值。"
        "如果用户输入与某字段完全无关，不要返回该字段。"
        "不要用用户的整句话填入字段值。"
    )
    hist_line = _format_messages_history_for_prompt(ctx.get("messages_history"))
    user_blob: Dict[str, Any] = {
        "user_input": user_text,
        "fields": fields_blob,
    }
    if hist_line:
        user_blob["conversation_context"] = hist_line
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user_blob, ensure_ascii=False)},
    ]
    filled = 0
    try:
        text = chat_with_fallback(messages)
        data = _parse_llm_json_object(text)
    except Exception as e:
        logger.warning("[collecting] fill_slots LLM/JSON 失败: %s", e)
        ctx["filled_count"] = 0
        return ctx

    target_table = (ctx.get("target_table") or "").strip()
    allowed_fields: Set[str] = set()
    if target_table and _validate_ident(target_table):
        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT field_name FROM meta.meta_fields
                    WHERE table_name = %s
                    """,
                    (target_table,),
                )
                allowed_fields = {str(r[0]) for r in cur.fetchall() if r and r[0]}

    if not allowed_fields:
        logger.warning("[collecting] fill_slots: 无 meta 字段白名单，跳过填充")
        ctx["filled_count"] = 0
        return ctx

    with db_conn() as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            for pk, val in data.items():
                key = str(pk).strip()
                if not key or not _validate_ident(key):
                    continue
                if key not in allowed_fields:
                    continue
                raw_val = val
                if raw_val is None:
                    continue
                sval = str(raw_val).strip()
                if not sval:
                    continue
                cur.execute(
                    """
                    UPDATE business.slots
                    SET value = %s,
                        value_raw = %s,
                        status = 'supplied',
                        supplied_at = now(),
                        source = 'user_input',
                        confidence = 1.0
                    WHERE frame_id = %s AND param_key = %s AND status = 'pending'
                    """,
                    (sval, sval, int(frame_id), key),
                )
                if cur.rowcount:
                    filled += 1

    ctx["filled_count"] = filled
    return ctx


# 合同主表/明细：非 simple，但确认执行与 submit 组装后需允许 INSERT（字段仍仅 meta.meta_fields）
_CONTRACT_WRITE_TABLES = frozenset({"contracts", "contract_items"})


def build_col_vals_from_fields(
    target_table: str, fields: Dict[str, Any]
) -> Tuple[List[Tuple[str, str]], Optional[str]]:
    """
    与 commit_insert_from_slots 相同的表名、字段白名单与标识符校验，从字段字典得到 INSERT 列值。
    成功返回 (col_vals, None)，失败返回 ([], 错误信息)。
    """
    target_table = (target_table or "").strip()
    if not target_table:
        return [], "缺少目标表。"
    allowed_tables = _load_simple_table_whitelist() | _CONTRACT_WRITE_TABLES
    if target_table not in allowed_tables:
        return [], "安全校验失败：目标表不在白名单。"
    if not _validate_ident(target_table):
        return [], "安全校验失败：非法表名。"

    field_whitelist: Set[str] = set()
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT field_name FROM meta.meta_fields WHERE table_name = %s",
                (target_table,),
            )
            field_whitelist = {str(r[0]) for r in cur.fetchall() if r and r[0]}

    col_vals: List[Tuple[str, str]] = []
    for param_key, raw_val in fields.items():
        col = str(param_key).strip()
        if not col or col not in field_whitelist or not _validate_ident(col):
            continue
        if raw_val is None:
            continue
        sval = str(raw_val).strip()
        if not sval:
            continue
        col_vals.append((col, sval))

    if not col_vals:
        return [], "没有可写入的字段，请补充必填信息。"
    return col_vals, None


def validate_write_entity_fields_meta(table_name: str, fields: Dict[str, Any]) -> Optional[str]:
    """
    按 meta_fields 校验写入预览：is_required 必填、allowed_values 枚举。
    ask_if_missing 仅用于 prompt 字段注入，不参与提交必填校验。
    返回错误文案或 None（通过）。
    """
    target_table = (table_name or "").strip()
    if not target_table or not _validate_ident(target_table):
        return None
    allowed_tables = _load_simple_table_whitelist()
    if target_table not in allowed_tables:
        return None
    if not isinstance(fields, dict):
        return "fields 必须是对象。"

    with db_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT field_name,
                       COALESCE(NULLIF(field_cn, ''), field_name) AS field_cn,
                       COALESCE(is_required, false) AS is_required,
                       allowed_values
                FROM meta.meta_fields
                WHERE table_name = %s
                ORDER BY id
                """,
                (target_table,),
            )
            meta_rows = [dict(r) for r in cur.fetchall()]
    if not meta_rows:
        return None

    missing: List[Tuple[str, str]] = []
    for r in meta_rows:
        if not r.get("is_required"):
            continue
        fn = str(r.get("field_name") or "").strip()
        if not fn:
            continue
        label = str(r.get("field_cn") or fn)
        v = fields.get(fn)
        if v is None:
            missing.append((fn, label))
            continue
        if isinstance(v, str) and not str(v).strip():
            missing.append((fn, label))
    if missing:
        parts_cn = [m[1] for m in missing]
        parts_key = [m[0] for m in missing]
        return (
            f"缺少必填字段：{', '.join(parts_cn)}（"
            f"{', '.join(parts_key)}）。请先补全后再提交。"
        )

    for r in meta_rows:
        fn = str(r.get("field_name") or "").strip()
        if not fn or fn not in fields:
            continue
        av = _allowed_values_as_dict(r.get("allowed_values"))
        if not av:
            continue
        raw = fields[fn]
        if raw is None:
            continue
        sval = str(raw).strip()
        if not sval:
            continue
        if sval not in av:
            fcn = str(r.get("field_cn") or fn)
            opts = ", ".join(av.keys())
            return (
                f"字段 {fn}（{fcn}）的值 {sval!r} 不在允许范围内（允许：{opts}）。"
            )
    return None


def _row_jsonable(row: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in row.items():
        if isinstance(v, (date, datetime)):
            out[k] = v.isoformat()
        elif isinstance(v, Decimal):
            out[k] = str(v)
        elif isinstance(v, (bytes, memoryview)):
            out[k] = str(v)[:200]
        else:
            out[k] = v
    return out


def _format_write_success_summary(row: Dict[str, Any]) -> str:
    """从数据库行摘要多数字段，用于「✅ 已写入」回复。"""
    skip = frozenset({"id", "created_at", "updated_at", "created_by"})
    priority = (
        "name",
        "product_name",
        "customer_code",
        "product_code",
        "title",
        "invoice_type",
        "status",
    )
    items: List[Tuple[str, Any]] = []
    for pk in priority:
        if pk not in row:
            continue
        v = row[pk]
        if v is None:
            continue
        sv = str(v).strip()
        if not sv:
            continue
        items.append((pk, v))
    seen = {x[0] for x in items}
    for k, v in row.items():
        if k in skip or k in seen:
            continue
        if v is None:
            continue
        sv = str(v).strip()
        if not sv:
            continue
        items.append((k, v))
        if len(items) >= 8:
            break
    if not items:
        return f"id={row.get('id', '')}"
    return "，".join(f"{k}={v}" for k, v in items[:8])


def merge_execution_result_into_frame(frame_id: int, payload: Dict[str, Any]) -> None:
    """将执行结果合并进 frame.context_snapshot.execution_result。"""
    blob = json.dumps({"execution_result": payload}, ensure_ascii=False, default=str)
    try:
        with db_conn() as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE business.frames
                    SET context_snapshot = COALESCE(context_snapshot, '{}'::jsonb) || %s::jsonb,
                        updated_at = now()
                    WHERE id = %s
                    """,
                    (blob, int(frame_id)),
                )
    except Exception as e:
        logger.warning("[collecting] merge_execution_result_into_frame 失败: %s", e)


def apply_preview_to_slots(frame_id: int, table_name: str, row: Dict[str, Any]) -> None:
    """
    将待确认的预览行写入 business.slots（supplied）；V15 主路径为 pending_submit + workflow_engine。
    source 固定为 SLOT_SOURCE_AGENT_PREVIEW，取消确认时仅删除该来源槽位。
    """
    with db_conn() as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            for key, raw_val in row.items():
                if raw_val is None:
                    continue
                sval = str(raw_val).strip()
                if not sval:
                    continue
                if not _validate_ident(str(key).strip()):
                    continue
                cur.execute(
                    """
                    SELECT COALESCE(NULLIF(field_cn, ''), field_name)
                    FROM meta.meta_fields
                    WHERE table_name = %s AND field_name = %s
                    """,
                    (table_name, str(key)),
                )
                r = cur.fetchone()
                label = (r[0] if r else None) or str(key)
                cur.execute(
                    """
                    INSERT INTO business.slots
                        (frame_id, param_key, param_label, value, value_raw,
                         status, source, confidence, supplied_at)
                    VALUES (%s, %s, %s, %s, %s, 'supplied', %s, 1.0, now())
                    ON CONFLICT (frame_id, param_key) DO UPDATE SET
                        value = EXCLUDED.value,
                        value_raw = EXCLUDED.value_raw,
                        status = 'supplied',
                        source = EXCLUDED.source,
                        confidence = EXCLUDED.confidence,
                        supplied_at = now()
                    """,
                    (
                        int(frame_id),
                        str(key),
                        str(label),
                        sval,
                        sval,
                        SLOT_SOURCE_AGENT_PREVIEW,
                    ),
                )


def _load_meta_fields_for_agent(table_name: str) -> List[Dict[str, Any]]:
    with db_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT field_name, field_cn, ask_if_missing, allowed_values
                FROM meta.meta_fields
                WHERE table_name = %s
                ORDER BY id
                """,
                (table_name,),
            )
            return [dict(r) for r in cur.fetchall()]


def _load_slots_for_agent(frame_id: int) -> List[Dict[str, Any]]:
    with db_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT param_key, param_label, status, value, value_raw
                FROM business.slots
                WHERE frame_id = %s
                ORDER BY id
                """,
                (int(frame_id),),
            )
            return [dict(r) for r in cur.fetchall()]


def _build_collecting_system_prompt(ctx: Dict[str, Any]) -> str:
    wi = (ctx.get("workflow_intent") or ctx.get("matched_intent") or "").strip()
    schema = _load_workflow_context_schema(wi) or {}
    steps_db = _load_workflow_steps(wi)
    if steps_db:
        logger.info(
            "[collecting.context_schema] 从 workflow_steps 表读取 intent=%s steps=%s",
            wi,
            len(steps_db),
        )
        base = _build_system_prompt_from_context_schema(ctx, schema, steps_db)
    else:
        base = _build_collecting_system_prompt_legacy(ctx)
    return base + _collecting_memo_prompt_suffix()


def _build_collecting_system_prompt_legacy(ctx: Dict[str, Any]) -> str:
    """business.workflow_steps 无记录或未迁移时的兼容路径（保留运行时 meta 表拼接）。"""
    _STRICT = (
        "\n\n【重要行为规则】\n"
        "1. 你必须通过工具调用来完成任务，不要用文字描述你「将要做什么」然后等用户确认。\n"
        "2. 当你已经通过 query_db 确认了目标记录，并且用户的修改/删除/新建意图明确时，立即调用 "
        "submit（统一提交预览）。系统会自动生成确认卡片给用户，不需要你用文字询问「是否确认」。\n"
        "3. 禁止输出类似「请确认以下修改是否符合预期」这样的文字然后等待用户回复。"
        "直接调用工具，工具会触发确认流程。\n"
        "4. 只有在信息确实不足（比如用户没说改成什么值、没指定哪条记录）时，才用 ask_user 工具追问。\n"
        "5. 每一轮对话你必须至少调用一个工具（query_db / submit / ask_user），不要只输出文字。"
    )
    _QUERY = (
        "\n\n【主数据查询场景】\n"
        "1. 本任务为只读查询，不要调用 submit。\n"
        "2. 使用 query_db 执行 SELECT，仅 business / meta schema；查询结果会在界面以表格展示，"
        "回复中请用自然语言简要说明要点。\n"
        "3. 查询业务表时 WHERE 条件应包含 deleted_at IS NULL（除非用户明确要求查看已删除记录）；"
        "系统也会在未写 deleted_at 时自动过滤已删行。\n"
        "4. 若用户意图模糊或缺少表名/条件，用 ask_user 追问。\n"
        "5. 每一轮至少调用 query_db 或 ask_user 之一，不要只输出文字而不查库。"
    )
    _CONTRACT = (
        "\n\n【重要行为规则】\n"
        "1. 你必须通过工具完成任务，不要用纯文字代替工具调用。\n"
            "2. 信息足够后必须调用 submit，data 中含 contract 与 items，由系统弹出确认卡片；"
        "不要用文字询问「是否确认」。\n"
        "3. 只有在缺客户、缺明细、或条件模糊时，才用 ask_user 追问。\n"
        "4. 用 query_db 查 customers 时，对 name 或 short_name 使用 ILIKE '%关键词%' 模糊匹配，不要仅用精确匹配。\n"
        "5. 每一轮至少调用 query_db、submit 或 ask_user 之一，不要只输出文字。\n"
        "6. 【禁止】对 contracts 直接 write；新建合同必须且只能使用 submit。\n"
        "7. 付款方式、交货日期、签约日期为可选字段；用户未提供时不追问，直接 submit。\n"
    )
    _CONTRACT_UPD = (
        "\n\n【重要行为规则】\n"
        "1. 先用 query_db 查询 business.contracts / contract_items，确认合同存在且唯一（单行命中 id）。\n"
        "2. 必须问清变更原因；无原因不得 submit（顶层 reason 必填）。\n"
        "3. 条件满足后调用 submit：data 须含 record_id（合同 id），"
        "以及按需任选 header_updates（主表字段对象）、items_add（新增明细数组）、"
        "items_update（每项含 record_id 或 item_id 与要改字段）、items_remove（每项含 record_id）；"
        "未改动的部分可省略。系统会按 workflow_steps 拆成多步写库并写 contract_changes。\n"
        "4. 可用工具：submit、query_db、ask_user。\n"
        "5. 每一轮至少调用其一工具，不要只输出文字。\n"
    )
    _CONTRACT_DEL = (
        "\n\n【重要行为规则】\n"
        "1. 先用 query_db 确认要删除的合同（结果须唯一一行且含 id）。\n"
            "2. 必须问清删除原因；submit 的 data 中须含 explanation（删除原因）。\n"
            "3. 可用工具：query_db、submit、ask_user。\n"
    )

    table_name = (ctx.get("target_table") or "").strip()
    table_cn = (ctx.get("table_cn") or "").strip() or table_name
    fields = _load_meta_fields_for_agent(table_name)
    fid = ctx.get("frame_id")
    slots: List[Dict[str, Any]] = []
    if fid is not None:
        slots = _load_slots_for_agent(int(fid))

    fields_blob = []
    for f in fields:
        fields_blob.append(
            {
                "field_name": f.get("field_name"),
                "field_cn": f.get("field_cn"),
                "ask_if_missing": f.get("ask_if_missing"),
                "allowed_values": f.get("allowed_values"),
            }
        )
    supplied = [s for s in slots if (s.get("status") or "") == "supplied"]
    pending = [s for s in slots if (s.get("status") or "") == "pending"]

    wi = (ctx.get("workflow_intent") or ctx.get("matched_intent") or "").strip()
    if wi == "update_simple_entity":
        principles = (
            "你在帮用户修改已有记录。先用 query_db 找到目标记录并确认唯一性（必要时用 ask_user 让用户选定）；"
            "再使用 submit 提交要改的字段（data 中含 table_name、fields），触发确认（不直接 UPDATE）。"
            "需要核对编号、枚举或现值时只用 query_db（仅 SELECT business/meta）。"
            "用 query_db 查业务表时，除非用户明确要求查看已删除记录，否则 WHERE 条件应包含 deleted_at IS NULL；"
            "系统也会在未写 deleted_at 时自动过滤已删行。"
        ) + _STRICT
        task_title = "修改简单实体（collecting）"
        tool_line = "通过工具 query_db / submit / ask_user 完成。"
    elif wi == "delete_simple_entity":
        principles = (
            "你在帮用户软删除已有记录。先用 query_db 找到目标记录并确认唯一性（必要时用 ask_user 让用户选定）；"
            "再使用 submit 触发删除确认（不直接 UPDATE）。"
            "需要核对编号或现值时只用 query_db（仅 SELECT business/meta）。"
            "用 query_db 查业务表时，除非用户明确要求查看已删除记录，否则 WHERE 条件应包含 deleted_at IS NULL；"
            "系统也会在未写 deleted_at 时自动过滤已删行。"
        ) + _STRICT
        task_title = "删除简单实体（collecting）"
        tool_line = "通过工具 query_db / submit / ask_user 完成。"
    elif wi == "create_contract":
        principles = (
            "你在帮用户创建销售合同：主表 contracts + 明细 contract_items。\n"
            "第一步：用 query_db 在 business.customers 中确认客户存在，取得 customer_id 与客户名称（作 customer_name）。\n"
            "第二步：收集合同明细（至少一行）：product_name、quantity、unit_price；可用 query_db 查 business.products 填写 product_code、product_id。\n"
            "第三步：信息齐全后调用 submit，data 中一次性传入 contract 与 items，触发界面确认（不要直接写库）。\n"
            "不要向用户索要 contract_no；编号由系统按 CT-YYYYMM-NNN 生成。\n"
            "不要向用户索要每条 amount 或主表 total_amount；amount = quantity × unit_price，总额由系统汇总。\n"
            "currency 默认 CNY；用户未说明币种则不必追问。\n"
            "用 query_db 查业务表时，除非用户明确要求查看已删除记录，否则 WHERE 应包含 deleted_at IS NULL；"
            "系统也会在未写 deleted_at 时自动过滤已删行。"
        ) + _CONTRACT
        task_title = "创建销售合同（collecting）"
        tool_line = "通过工具 query_db、submit、ask_user 完成。"
    elif wi == "update_contract":
        principles = (
            "你在帮用户修改已有销售合同：主表 contracts 与明细 contract_items。\n"
            "先用 query_db 拉取当前合同与明细；弄清用户要改主表哪些字段、明细如何增删改；"
            "变更原因必须明确后再提交预览。"
        ) + _CONTRACT_UPD
        task_title = "修改销售合同（collecting）"
        tool_line = "通过工具 query_db、submit、ask_user 完成。"
    elif wi == "delete_contract":
        principles = (
            "你在帮用户软删除整份合同（主表与子表明细一并作废）。\n"
            "先用 query_db 确认目标合同；再让用户说明删除原因，然后用 submit 触发确认。"
        ) + _CONTRACT_DEL
        task_title = "删除销售合同（collecting）"
        tool_line = "通过工具 query_db、submit、ask_user 完成。"
    elif wi == "query_master_data":
        principles = (
            "你在帮用户查询业务主数据（只读）。"
            "根据用户问题构造 query_db 的 SELECT，从 business / meta 获取数据；"
            "若未指定具体表，请结合用户表述与 meta 表目录自行选择合理的 business.* 表。"
        ) + _QUERY
        task_title = "主数据查询（collecting）"
        tool_line = (
            "通过 query_db 执行查询，必要时用 ask_user 追问。"
            "（工具列表由系统注入，本场景请勿使用写库类工具。）"
        )
    elif wi == "free_chat":
        principles = (
            "你在与用户自由对话：可闲聊、分析、建议；涉及业务数据时用 query_db 只读查询（business/meta），"
            "需要澄清时用 ask_user；需按 workflow 持久化要点时用 submit。不必每轮都调用工具。"
        )
        task_title = "自由对话（collecting）"
        tool_line = "通过工具 query_db、submit、ask_user 完成。"
    else:
        principles = (
            "原则：能从用户本轮及历史输入中可靠推断的字段请自行填入 submit 的 data.fields；"
            "不确定或缺关键信息时用 ask_user 追问；"
            "需要核对库中已有编号、枚举或关联记录时用 query_db（仅 SELECT business/meta）。"
            "字段齐全且值合规后调用 submit 触发确认（不直接写库）。"
            "若用户在一轮话里已给出产品编号、名称、品类、单位、单价等可映射到 meta 字段的信息，"
            "禁止再用纯文字追问或列举待填项，必须直接调用 submit（由系统弹出确认卡片）。"
            "用 query_db 查业务表时，除非用户明确要求查看已删除记录，否则 WHERE 条件应包含 deleted_at IS NULL；"
            "系统也会在未写 deleted_at 时自动过滤已删行。"
        ) + _STRICT
        task_title = "新建简单实体（collecting）"
        tool_line = "通过工具 query_db / submit / ask_user 完成信息收集。"

    blob = {
        "task": task_title,
        "target_table": table_name,
        "table_cn": table_cn,
        "meta_fields": fields_blob,
        "slots_supplied": supplied,
        "slots_pending": pending,
        "principles": principles,
    }
    detail_block = ""
    if table_name:
        detail_block = build_table_detail(table_name) + "\n\n"
    contract_rel_fields = ""
    if wi in ("create_contract", "update_contract"):
        contract_rel_fields = _build_contract_related_field_details_block()
    return (
        detail_block
        + contract_rel_fields
        + "你是 ERP 数据收集助手。根据系统提供的元数据与当前槽位状态，"
        + tool_line
        + "\n\n"
        + json.dumps(blob, ensure_ascii=False, default=str)
    )



def collect_update_fields_validated(
    table_name: str, fields: Dict[str, Any]
) -> Tuple[Optional[Dict[str, str]], Optional[str]]:
    """
    校验 UPDATE 字段：表白名单、meta 字段白名单、标识符；返回 {列名: 字符串值} 或错误信息。
    """
    target_table = (table_name or "").strip()
    if not target_table:
        return None, "缺少目标表。"
    allowed_tables = _load_simple_table_whitelist() | _CONTRACT_WRITE_TABLES
    if target_table not in allowed_tables:
        return None, "安全校验失败：目标表不在白名单。"
    if not _validate_ident(target_table):
        return None, "安全校验失败：非法表名。"
    if not isinstance(fields, dict) or not fields:
        return None, "没有可更新的字段。"

    field_whitelist: Set[str] = set()
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT field_name FROM meta.meta_fields WHERE table_name = %s",
                (target_table,),
            )
            field_whitelist = {str(r[0]) for r in cur.fetchall() if r and r[0]}

    out: Dict[str, str] = {}
    for param_key, raw_val in fields.items():
        col = str(param_key).strip()
        if col == "id":
            continue
        if not col or not _validate_ident(col):
            continue
        if col not in field_whitelist:
            continue
        if raw_val is None:
            continue
        sval = str(raw_val).strip()
        if not sval:
            continue
        out[col] = sval

    if not out:
        return None, "没有可更新的字段（请检查字段名是否在元数据内）。"
    return out, None


def _parse_optional_date_str(val: Any) -> Optional[date]:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    s = str(val).strip()
    if not s:
        return None
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def persist_verified_record_id_to_frame(frame_id: int, verified_id: Any) -> None:
    """
    读出现有 context_snapshot，写入 verified_record_id 后整包写回，确保 query_db 结果落库。
    """
    try:
        vid = int(verified_id)
    except (TypeError, ValueError):
        return
    with db_conn() as conn:
        conn.autocommit = True
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT context_snapshot FROM business.frames WHERE id = %s",
                (int(frame_id),),
            )
            row = cur.fetchone()
            if not row:
                return
            raw = row.get("context_snapshot")
            if raw is None:
                snap: Dict[str, Any] = {}
            elif isinstance(raw, dict):
                snap = dict(raw)
            elif isinstance(raw, str):
                try:
                    snap = json.loads(raw) if raw.strip() else {}
                except json.JSONDecodeError:
                    snap = {}
            else:
                snap = {}
            if not isinstance(snap, dict):
                snap = {}
            snap["verified_record_id"] = vid
            out = json.dumps(snap, ensure_ascii=False)
            cur.execute(
                """
                UPDATE business.frames
                SET context_snapshot = %s::jsonb,
                    updated_at = now()
                WHERE id = %s
                """,
                (out, int(frame_id)),
            )


def get_frame_verified_record_id(frame_id: int) -> Optional[int]:
    """读取 frame.context_snapshot.verified_record_id（由 query_db 单行结果写入）。"""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT context_snapshot->>'verified_record_id'
                FROM business.frames
                WHERE id = %s
                """,
                (int(frame_id),),
            )
            row = cur.fetchone()
            if not row or row[0] is None:
                return None
            raw = str(row[0]).strip()
            if not raw:
                return None
            try:
                return int(raw)
            except ValueError:
                return None


def apply_update_preview_to_frame(
    frame_id: int, table_name: str, record_id: int, fields: Dict[str, Any]
) -> None:
    """把 update 预览并入 context_snapshot，供确认后执行。"""
    payload = json.dumps(
        {
            "action": "update",
            "pending_update": {
                "table_name": table_name,
                "record_id": int(record_id),
                "fields": fields,
            },
        },
        ensure_ascii=False,
    )
    with db_conn() as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE business.frames
                SET context_snapshot = COALESCE(context_snapshot, '{}'::jsonb) || %s::jsonb,
                    updated_at = now()
                WHERE id = %s
                """,
                (payload, int(frame_id)),
            )


def apply_delete_preview_to_frame(
    frame_id: int,
    table_name: str,
    record_id: int,
    reason: Optional[str] = None,
) -> None:
    """把 delete 预览并入 context_snapshot，供确认后执行软删除。"""
    pd: Dict[str, Any] = {
        "table_name": table_name,
        "record_id": int(record_id),
    }
    if reason:
        pd["reason"] = reason
    payload = json.dumps(
        {
            "action": "delete",
            "pending_delete": pd,
        },
        ensure_ascii=False,
    )
    with db_conn() as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE business.frames
                SET context_snapshot = COALESCE(context_snapshot, '{}'::jsonb) || %s::jsonb,
                    updated_at = now()
                WHERE id = %s
                """,
                (payload, int(frame_id)),
            )


def apply_contract_preview_to_frame(frame_id: int, pending_contract: Dict[str, Any]) -> None:
    """把 submit_contract 预览并入 context_snapshot，供确认后事务写入。"""
    payload = json.dumps(
        {
            "action": "create_contract",
            "pending_contract": pending_contract,
        },
        ensure_ascii=False,
        default=str,
    )
    with db_conn() as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE business.frames
                SET context_snapshot = COALESCE(context_snapshot, '{}'::jsonb) || %s::jsonb,
                    updated_at = now()
                WHERE id = %s
                """,
                (payload, int(frame_id)),
            )


def apply_contract_update_preview_to_frame(
    frame_id: int, pending_contract_update: Dict[str, Any]
) -> None:
    """把 update_contract 预览并入 context_snapshot。"""
    payload = json.dumps(
        {
            "action": "update_contract",
            "pending_contract_update": pending_contract_update,
        },
        ensure_ascii=False,
        default=str,
    )
    with db_conn() as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE business.frames
                SET context_snapshot = COALESCE(context_snapshot, '{}'::jsonb) || %s::jsonb,
                    updated_at = now()
                WHERE id = %s
                """,
                (payload, int(frame_id)),
            )


def apply_pending_submit_to_frame(
    frame_id: int, pending_submit: Dict[str, Any]
) -> None:
    """V15：pending_submit 写入 frame，确认后由 workflow_engine.run_confirm_execution 执行。"""
    payload = json.dumps(
        {"action": "submit_confirm", "pending_submit": pending_submit},
        ensure_ascii=False,
        default=str,
    )
    with db_conn() as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE business.frames
                SET context_snapshot = COALESCE(context_snapshot, '{}'::jsonb) || %s::jsonb,
                    updated_at = now()
                WHERE id = %s
                """,
                (payload, int(frame_id)),
            )


def check_and_proceed(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """有 pending 则追问，否则写库。"""
    frame_id = ctx.get("frame_id")
    if frame_id is None:
        ctx.setdefault("reply", "无法继续：缺少 frame。")
        ctx["status"] = "error"
        return ctx

    with db_conn() as conn:
        conn.autocommit = True
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT COUNT(*)::int AS c FROM business.slots
                WHERE frame_id = %s AND status = 'pending'
                """,
                (int(frame_id),),
            )
            n_pending = cur.fetchone()["c"]

            if n_pending > 0:
                cur.execute(
                    """
                    SELECT s.param_label, mf.allowed_values
                    FROM business.slots s
                    LEFT JOIN meta.meta_fields mf
                        ON mf.table_name = (
                            SELECT (context_snapshot->>'target_table')
                            FROM business.frames
                            WHERE id = %s
                        )
                        AND mf.field_name = s.param_key
                    WHERE s.frame_id = %s AND s.status = 'pending'
                    ORDER BY s.id ASC
                    LIMIT 1
                    """,
                    (int(frame_id), int(frame_id)),
                )
                r = cur.fetchone()
                label = (r.get("param_label") if r else None) or "该项"
                base = f"好的，已记录。请问{label}是什么？"
                extra = _followup_enum_line(r.get("allowed_values") if r else None)
                ctx["reply"] = base if not extra else f"{base}\n{extra}"
                ctx["status"] = "collecting"
                return ctx

    from engine.workflow_engine import commit_insert_from_slots

    commit_insert_from_slots(ctx)
    if ctx.get("status") != "completed":
        ctx.setdefault("status", "error")
    return ctx


def _load_frame_context_snapshot(frame_id: int) -> Dict[str, Any]:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT context_snapshot FROM business.frames WHERE id = %s",
                (int(frame_id),),
            )
            row = cur.fetchone()
    if not row or row[0] is None:
        return {}
    raw = row[0]
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        try:
            j = json.loads(raw) if raw.strip() else {}
            return j if isinstance(j, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _list_ask_if_missing_field_names(table_name: str) -> List[str]:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT field_name FROM meta.meta_fields
                WHERE table_name = %s AND ask_if_missing = true
                ORDER BY id
                """,
                (table_name,),
            )
            return [str(r[0]) for r in cur.fetchall() if r and r[0]]


def _apply_slot_values_from_dict(frame_id: int, extracted: Dict[str, Any]) -> int:
    """将提取结果写入 slots（pending→supplied）。返回更新行数。"""
    n = 0
    for key, raw in extracted.items():
        col = str(key).strip()
        if not col or not _validate_ident(col):
            continue
        if raw is None:
            continue
        val = str(raw).strip()
        if not val:
            continue
        with db_conn() as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE business.slots
                    SET value = %s,
                        value_raw = %s,
                        status = 'supplied',
                        source = 'code_prefill',
                        confidence = 1.0,
                        supplied_at = now()
                    WHERE frame_id = %s AND param_key = %s
                    """,
                    (val, val, int(frame_id), col),
                )
                n += int(cur.rowcount)
    return n


def _merge_snapshot_prefill_into_slots(frame_id: int) -> None:
    snap = _load_frame_context_snapshot(frame_id)
    for key in ("prefilled_fields", "extracted_fields"):
        raw = snap.get(key)
        if isinstance(raw, dict):
            _apply_slot_values_from_dict(
                frame_id,
                {str(k): v for k, v in raw.items() if v is not None},
            )


def _extract_create_fields_from_text(table_name: str, text: str) -> Dict[str, str]:
    """
    从用户/助手自然语言中确定性提取 ask_if_missing 字段（按 meta 的 field_cn 分段匹配）。
    """
    out: Dict[str, str] = {}
    if not (text or "").strip():
        return out
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT field_name,
                       COALESCE(NULLIF(field_cn, ''), field_name) AS field_cn
                FROM meta.meta_fields
                WHERE table_name = %s AND ask_if_missing = true
                ORDER BY id
                """,
                (table_name,),
            )
            meta_rows = cur.fetchall()
    labels: List[Tuple[str, List[str]]] = []
    for field_name, field_cn in meta_rows:
        fn = str(field_name)
        labs: List[str] = []
        if field_cn:
            labs.append(str(field_cn))
        labs.append(fn)
        if table_name == "products" and fn == "category":
            if "品类" not in labs:
                labs.append("品类")
        seen: Set[str] = set()
        uniq: List[str] = []
        for x in labs:
            if x and x not in seen:
                seen.add(x)
                uniq.append(x)
        labels.append((fn, uniq))

    for part in re.split(r"[，,；;\n]", text):
        part = part.strip()
        if not part:
            continue
        for field_name, lab_list in labels:
            if field_name in out:
                continue
            for lab in sorted(lab_list, key=len, reverse=True):
                if part.startswith(lab):
                    val = part[len(lab) :].strip().lstrip("：:").strip()
                    if val:
                        out[field_name] = val
                    break

    for field_name, lab_list in labels:
        if field_name in out:
            continue
        for lab in sorted(lab_list, key=len, reverse=True):
            m = re.search(re.escape(lab) + r"[：:\s]*([^\s，,；;]+)", text)
            if m:
                out[field_name] = m.group(1).strip()
                break
    if table_name == "products" and out.get("category"):
        out["category"] = normalize_products_category(out["category"])
    return out


def _collect_supplied_slot_values_for_table(frame_id: int, table_name: str) -> Dict[str, str]:
    whitelist: Set[str] = set()
    rows: List[Tuple[Any, ...]] = []
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT field_name FROM meta.meta_fields WHERE table_name = %s",
                (table_name,),
            )
            whitelist = {str(r[0]) for r in cur.fetchall() if r and r[0]}
            cur.execute(
                """
                SELECT param_key, value FROM business.slots
                WHERE frame_id = %s AND status = 'supplied'
                """,
                (int(frame_id),),
            )
            rows = cur.fetchall()
    out: Dict[str, str] = {}
    for pk, val in rows:
        pk = str(pk)
        if pk not in whitelist:
            continue
        if val is None or not str(val).strip():
            continue
        out[pk] = str(val).strip()
    return out


def _required_ask_if_missing_filled(frame_id: int, table_name: str) -> bool:
    required = _list_ask_if_missing_field_names(table_name)
    if not required:
        return False
    vals = _collect_supplied_slot_values_for_table(frame_id, table_name)
    for k in required:
        if k not in vals or not str(vals[k]).strip():
            return False
    return True


def _force_write_entity_from_slots(ctx: Dict[str, Any], frame_id: int, table_name: str) -> bool:
    """槽位已齐时执行 write_entity 并写入 pending_confirm 状态。成功返回 True。"""
    fields = _collect_supplied_slot_values_for_table(frame_id, table_name)
    if not fields:
        return False
    logger.info("collecting: 必填字段齐全，代码强制调用 submit")
    result = execute_tool_call(
        "submit",
        {
            "data": {
                "table_name": table_name,
                "fields": fields,
                "explanation": "系统根据槽位与原文提取结果自动提交，待用户界面确认。",
            },
            "frame_id": int(frame_id),
        },
    )
    if result.get("error"):
        logger.warning(
            "collecting: 强制 submit 未通过校验: %s", result.get("error")
        )
        return False
    if result.get("status") != "pending_confirm":
        return False
    preview = result.get("preview") or {}
    ps = result.get("pending_submit")
    if ctx.get("frame_id") is not None and isinstance(ps, dict):
        apply_pending_submit_to_frame(int(ctx["frame_id"]), ps)
    ctx["status"] = "pending_confirm"
    ctx["preview"] = preview
    ctx["explanation"] = result.get("explanation") or ""
    ctx["frame_id"] = ctx.get("frame_id")
    ctx["reply"] = (ctx.get("reply") or "") or (
        "请确认以下新建内容是否符合预期。"
    )
    return True


def _run_create_prefill_and_force_write(
    ctx: Dict[str, Any], assistant_msg: Dict[str, Any]
) -> bool:
    """
    create_simple_entity：LLM 本轮未调用任何工具时，用代码从快照/用户话/助手话填槽；
    必填齐则强制 write_entity。
    """
    wi = (ctx.get("workflow_intent") or ctx.get("matched_intent") or "").strip()
    if wi != "create_simple_entity":
        return False
    frame_id = ctx.get("frame_id")
    table_name = (ctx.get("target_table") or "").strip()
    if frame_id is None or not table_name:
        return False
    fid = int(frame_id)
    user_text = (ctx.get("user_input") or ctx.get("user_text") or "").strip()
    assist_text = (assistant_msg.get("content") or "").strip()

    _merge_snapshot_prefill_into_slots(fid)
    _apply_slot_values_from_dict(fid, _extract_create_fields_from_text(table_name, user_text))
    if _required_ask_if_missing_filled(fid, table_name):
        return _force_write_entity_from_slots(ctx, fid, table_name)

    _apply_slot_values_from_dict(fid, _extract_create_fields_from_text(table_name, assist_text))
    if _required_ask_if_missing_filled(fid, table_name):
        return _force_write_entity_from_slots(ctx, fid, table_name)

    return False


def _most_recent_tool_message_was_error(messages: List[Dict[str, Any]]) -> bool:
    """从对话末尾向前找最近一条 tool 消息；若为 JSON 且含 error 则 True。"""
    for m in reversed(messages):
        if (m.get("role") or "").strip() != "tool":
            continue
        raw = m.get("content")
        if not raw:
            return False
        try:
            d = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            return False
        if isinstance(d, dict) and d.get("error"):
            return True
        return False
    return False


def _build_free_chat_system_prompt() -> str:
    return (
        "你是 AI·ERP 系统的助手。你可以在对话中调用工具：\n"
        "- query_db：执行单条只读 SELECT（business / meta schema），用于核实数据、列表、统计；\n"
        "- ask_user：信息不足或存在歧义时向用户追问。\n"
        "不要编造查询结果；需要库中数据时务必先 query_db。\n"
        "本模式无 workflow_steps / collecting frame 约束，用自然、专业中文回复。\n"
        + _collecting_memo_prompt_suffix()
    )


def run_free_chat(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """
    轻量对话：仅 query_db + ask_user，不建 frame、不加载 workflow_steps。
    上下文：sessions.memo 拼入 system；本轮仅 user_input/user_text 一条，不加载 turns 历史。
    返回结构与 execute_workflow_for_chat（run_collecting 分支）一致。
    """
    try:
        ctx = dict(ctx)
        ctx.setdefault("data", {"main": [], "sub": []})
        session_id = ctx.get("session_id")
        intent_label = (ctx.get("free_chat_intent_label") or "query_master_data").strip() or "query_master_data"

        system_prompt = _build_free_chat_system_prompt()
        memo: Dict[str, Any] = {}
        if session_id is not None:
            memo = get_session_memo(int(session_id))
        if not isinstance(memo, dict):
            memo = {}
        try:
            memo_blob = json.dumps(memo, ensure_ascii=False)
        except (TypeError, ValueError):
            memo_blob = "{}"
        system_prompt = (
            system_prompt
            + "\n\n【本会话已保存的记忆（sessions.memo，只读上下文）】\n"
            + memo_blob
        )

        user_text = (ctx.get("user_input") or ctx.get("user_text") or "").strip()
        user_body = user_text if user_text else " "
        uploaded = ctx.get("uploaded_file_text")
        if uploaded:
            user_body = user_body + "\n\n" + str(uploaded)
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_body},
        ]

        tools_list = load_tools_from_db(filter_names=list(_FREE_CHAT_TOOL_NAMES))
        max_rounds = 16
        tool_calls_used = 0
        max_tools = 10
        reply_text = ""
        collecting_end_reason = "max_reached"

        logger.info(
            "[free_chat.开始] session=%s intent_label=%s",
            session_id,
            intent_label,
        )

        for _round in range(max_rounds):
            assistant_msg = chat_with_tools(messages, tools_list, prefer_fallback=False)
            assistant_content = (
                assistant_msg.get("content") if isinstance(assistant_msg, dict) else None
            )
            _try_apply_memo_from_assistant_text(
                int(session_id) if session_id is not None else None,
                assistant_content,
            )
            logger.info(
                "[free_chat.memo_debug] session_id=%s content_head=%s",
                session_id,
                (assistant_content or "")[:200],
            )
            tcs = assistant_msg.get("tool_calls")
            logger.info(
                "[free_chat.轮次] round=%s/%s tool_calls=%s",
                _round + 1,
                max_rounds,
                [tc.get("function", {}).get("name") for tc in tcs]
                if tcs
                else "NONE",
            )
            if not tcs:
                reply_text = (assistant_content or "").strip() or "好的。"
                ctx["reply"] = reply_text
                ctx["status"] = "collecting"
                collecting_end_reason = "no_tool_calls"
                break

            messages.append(assistant_msg)
            stop_loop = False
            for tc in tcs:
                if tool_calls_used >= max_tools:
                    reply_text = "请求过于复杂，请简化后重试。"
                    ctx["reply"] = reply_text
                    ctx["status"] = "collecting"
                    collecting_end_reason = "max_tools_cap"
                    stop_loop = True
                    break

                tool_calls_used += 1
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

                tid = (tc.get("id") or "").strip() or f"call_{tool_calls_used}"
                result = execute_tool_call(name, args)
                _st_d, _det_d = _collecting_tool_status_detail(name, result)
                logger.info(
                    "[free_chat.tool结果] name=%s status=%s detail=%s",
                    name,
                    _st_d,
                    _det_d,
                )

                if name == "query_db" and isinstance(result, dict) and "error" not in result:
                    rows = result.get("rows")
                    if isinstance(rows, list):
                        ctx.setdefault("data", {"main": [], "sub": []})
                        ctx["data"]["main"] = list(rows)
                        ctx["data"].setdefault("sub", [])

                if name == "ask_user" and result.get("status") == "pending_user_reply":
                    q = (result.get("question") or "").strip()
                    hint = result.get("hint")
                    if hint:
                        reply_text = f"{q}\n{hint}"
                    else:
                        reply_text = q or "请补充更多信息。"
                    ctx["reply"] = reply_text
                    ctx["status"] = "collecting"
                    collecting_end_reason = "ask_user"
                    stop_loop = True
                    break

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tid,
                        "content": json.dumps(result, ensure_ascii=False, default=str),
                    }
                )

            if stop_loop:
                break
        else:
            reply_text = "我需要更多信息，能否重新描述一下需求？"
            ctx["reply"] = reply_text
            ctx["status"] = "collecting"
            collecting_end_reason = "no_reply"

        if not ctx.get("reply"):
            ctx["reply"] = reply_text or "好的。"
        ctx["status"] = ctx.get("status") or "collecting"

        logger.info(
            "[free_chat.结束] session=%s reason=%s",
            session_id,
            collecting_end_reason,
        )

        reply_final = str(ctx.get("reply") or "")
        raw_data = ctx.get("data")
        if not isinstance(raw_data, dict):
            raw_data = {}
        main_rows = raw_data.get("main")
        sub_rows = raw_data.get("sub")
        if not isinstance(main_rows, list):
            main_rows = []
        if not isinstance(sub_rows, list):
            sub_rows = []

        return {
            "reply": reply_final,
            "data": {"main": main_rows, "sub": sub_rows},
            "content": reply_final,
            "intent": intent_label,
            "requiresConfirm": False,
            "pendingToken": None,
            "workflow_context": ctx,
        }
    except Exception as e:
        logger.exception("[free_chat] 失败: %s", e)
        ctx_err = dict(ctx) if isinstance(ctx, dict) else {}
        err_reply = f"处理失败：{e}"
        ctx_err["reply"] = err_reply
        ctx_err["status"] = "error"
        return {
            "reply": err_reply,
            "data": {"main": [], "sub": []},
            "content": err_reply,
            "intent": (ctx_err.get("free_chat_intent_label") or "未匹配"),
            "requiresConfirm": False,
            "pendingToken": None,
            "workflow_context": ctx_err,
        }


def _collecting_tool_status_detail(name: str, result: Any) -> Tuple[str, str]:
    """供 [collecting.tool结果]：status 与短摘要。"""
    if not isinstance(result, dict):
        return "error", "非 dict 返回"
    if result.get("error"):
        return "error", str(result.get("error"))[:400]
    st = (result.get("status") or "").strip()
    if st == "pending_confirm":
        ps = result.get("pending_submit")
        if isinstance(ps, dict) and ps.get("mode") == "create_contract":
            pc = ps.get("payload") or {}
            n = len((pc.get("items") or [])) if isinstance(pc, dict) else 0
            return "pending_confirm", f"明细行数={n}"
        if name in ("submit_contract", "submit") and isinstance(
            result.get("pending_contract"), dict
        ):
            pc = result.get("pending_contract") or {}
            n = len((pc.get("items") or [])) if isinstance(pc, dict) else 0
            return "pending_confirm", f"明细行数={n}"
        return "pending_confirm", "待确认"
    if st == "pending_user_reply":
        return "pending_user_reply", "追问"
    if name == "query_db" and "rows" in result:
        return "success", f"{result.get('count', 0)}行"
    return "success", "ok"


def run_collecting(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """collecting 统一入口：LLM tool use 循环（最多 6 次工具调用）。"""
    try:
        ctx = get_or_create_frame(ctx)
        ctx.setdefault("data", {"main": [], "sub": []})
        session_id = ctx.get("session_id")
        turns: List[Dict[str, Any]] = []
        if session_id is not None:
            turns = get_recent_turns_sync(int(session_id), limit=20)

        system_prompt = _build_collecting_system_prompt(ctx)
        messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        for t in turns:
            role = (t.get("role") or "").strip()
            if role not in ("user", "assistant"):
                continue
            messages.append(
                {"role": role, "content": (t.get("content") or "").strip()}
            )

        tool_calls_used = 0
        _wi_tools = (
            ctx.get("workflow_intent") or ctx.get("matched_intent") or ""
        ).strip()
        max_tools = 10 if _wi_tools in ("create_contract", "update_contract") else 6
        max_rounds = 16
        reply_set = False
        contract_error_nudge_done = False
        collecting_end_reason = "max_reached"

        logger.info(
            "collecting system prompt:\n%s",
            system_prompt,
        )
        logger.info("collecting max_tools=%s workflow_intent=%r", max_tools, _wi_tools)
        _fid = ctx.get("frame_id")
        _tt = (ctx.get("target_table") or "").strip()
        logger.info(
            "[collecting.开始] frame=%s intent=%s target_table=%s max_tools=%s",
            _fid,
            _wi_tools,
            _tt or None,
            max_tools,
        )

        _schema_tools = _load_workflow_context_schema(_wi_tools)
        _allowed_tool_names = None
        if isinstance(_schema_tools, dict):
            _at = _schema_tools.get("allowed_tools")
            if isinstance(_at, list) and _at:
                _allowed_tool_names = [str(x) for x in _at if x]
        tools_list = load_tools_from_db(filter_names=_allowed_tool_names)

        for _round in range(max_rounds):
            assistant_msg = chat_with_tools(messages, tools_list, prefer_fallback=False)
            _try_apply_memo_from_assistant_text(
                int(session_id) if session_id is not None else None,
                assistant_msg.get("content") if isinstance(assistant_msg, dict) else None,
            )
            tcs = assistant_msg.get("tool_calls")
            _names = (
                [tc.get("function", {}).get("name") for tc in tcs]
                if tcs
                else "NONE"
            )
            logger.info(
                "[collecting.轮次] round=%s/%s tool_calls=%s",
                _round + 1,
                max_rounds,
                _names,
            )
            if not tcs:
                if _run_create_prefill_and_force_write(ctx, assistant_msg):
                    reply_set = True
                    collecting_end_reason = "prefill_write"
                    break
                wi_no_tools = (
                    ctx.get("workflow_intent") or ctx.get("matched_intent") or ""
                ).strip()
                if (
                    wi_no_tools in ("create_contract", "update_contract")
                    and _most_recent_tool_message_was_error(messages)
                    and not contract_error_nudge_done
                ):
                    contract_error_nudge_done = True
                    messages.append(assistant_msg)
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "（系统提示）上一步工具调用返回了错误。请根据错误信息修正："
                                "创建合同请使用 submit(data 含 contract/items)，不要手写 contracts；"
                                "修改合同请使用 submit(data 含 reason 等)，不要对 contracts 使用 update_entity。"
                                "请再次调用正确的工具。"
                            ),
                        }
                    )
                    _last_err = ""
                    for m in reversed(messages):
                        if (m.get("role") or "").strip() == "tool":
                            try:
                                d = json.loads(m.get("content") or "")
                                if isinstance(d, dict) and d.get("error"):
                                    _last_err = str(d.get("error"))[:200]
                            except (json.JSONDecodeError, TypeError):
                                pass
                            break
                    logger.warning(
                        "collecting: %s 流程中模型本轮未调工具，但最近一条 tool 结果为 error，"
                        "已追加提示并继续对话",
                        wi_no_tools,
                    )
                    logger.info(
                        "[collecting.补救] frame=%s last_error=%s retry_count=%s",
                        ctx.get("frame_id"),
                        _last_err,
                        1 if contract_error_nudge_done else 0,
                    )
                    continue
                messages.append(assistant_msg)
                ctx["reply"] = (assistant_msg.get("content") or "").strip() or "好的。"
                ctx["status"] = "collecting"
                reply_set = True
                collecting_end_reason = "no_tool_calls"
                _llm_text = (
                    (assistant_msg.get("content") or "")
                    if isinstance(assistant_msg, dict)
                    else ""
                )
                logger.info("[collecting.LLM文本] %s", _llm_text[:300])
                break

            messages.append(assistant_msg)
            stop_loop = False
            for tc in tcs:
                if tool_calls_used >= max_tools:
                    ctx["reply"] = "我需要更多信息，能否重新描述一下需求？"
                    ctx["status"] = "collecting"
                    reply_set = True
                    collecting_end_reason = "max_tools_cap"
                    stop_loop = True
                    break

                tool_calls_used += 1
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

                if name in (
                    "submit",
                    "write_entity",
                    "update_entity",
                    "delete_entity",
                    "submit_contract",
                    "update_contract",
                ):
                    args = dict(args)
                    args["frame_id"] = ctx.get("frame_id")

                tid = (tc.get("id") or "").strip() or f"call_{tool_calls_used}"
                logger.info(
                    "[collecting.tool执行] name=%s args_keys=%s",
                    name,
                    list(args.keys()) if isinstance(args, dict) else [],
                )
                logger.info(
                    "[collecting.tool参数] %s",
                    json.dumps(args, ensure_ascii=False, default=str)[:500],
                )
                result = execute_tool_call(name, args)
                _st_d, _det_d = _collecting_tool_status_detail(name, result)
                logger.info(
                    "[collecting.tool结果] name=%s status=%s detail=%s",
                    name,
                    _st_d,
                    _det_d,
                )

                if isinstance(result, dict) and result.get("error"):
                    logger.warning(
                        "collecting: tool %s 返回 error，将作为 tool 结果反馈模型继续循环: %s",
                        name,
                        result.get("error"),
                    )

                if name == "query_db" and isinstance(result, dict) and "error" not in result:
                    rows = result.get("rows")
                    wi_loop = (
                        ctx.get("workflow_intent") or ctx.get("matched_intent") or ""
                    ).strip()
                    if wi_loop in ("query_master_data", "free_chat") and isinstance(
                        rows, list
                    ):
                        ctx.setdefault("data", {"main": [], "sub": []})
                        ctx["data"]["main"] = list(rows)
                        ctx["data"].setdefault("sub", [])
                    id_vals: List[Any] = []
                    if isinstance(rows, list):
                        for r in rows:
                            if isinstance(r, dict) and "id" in r:
                                id_vals.append(r.get("id"))
                    logger.info(
                        "collecting: query_db 返回 count=%s truncated=%s 各行 id=%s",
                        result.get("count"),
                        result.get("truncated"),
                        id_vals,
                    )
                    _dump = json.dumps(result, ensure_ascii=False, default=str)
                    if len(_dump) > 6000:
                        _dump = _dump[:6000] + "…(truncated)"
                    logger.info("collecting: query_db 完整结果:\n%s", _dump)

                    _cnt = result.get("count")
                    if (
                        ctx.get("frame_id") is not None
                        and _cnt == 1
                        and isinstance(rows, list)
                        and len(rows) == 1
                        and isinstance(rows[0], dict)
                        and "id" in rows[0]
                    ):
                        try:
                            vid = int(rows[0]["id"])
                            fid = int(ctx["frame_id"])
                            persist_verified_record_id_to_frame(fid, vid)
                            logger.info(
                                "collecting: 写入 verified_record_id=%s 到 frame %s",
                                rows[0]["id"],
                                fid,
                            )
                        except (TypeError, ValueError) as _vid_e:
                            logger.warning(
                                "collecting: query_db 单行 id 无法转为 int: %s",
                                _vid_e,
                            )
                elif name == "query_db" and isinstance(result, dict) and result.get("error"):
                    logger.warning(
                        "collecting: query_db 错误: %s", result.get("error")
                    )

                _submit_names = (
                    "submit",
                    "write_entity",
                    "update_entity",
                    "delete_entity",
                    "submit_contract",
                    "update_contract",
                )
                if name in _submit_names and result.get("status") == "pending_confirm":
                    ps = result.get("pending_submit")
                    if ctx.get("frame_id") is not None and isinstance(ps, dict):
                        apply_pending_submit_to_frame(int(ctx["frame_id"]), ps)
                        logger.info(
                            "[collecting.写frame] func=apply_pending_submit_to_frame "
                            "frame=%s mode=%s",
                            int(ctx["frame_id"]),
                            ps.get("mode"),
                        )
                    ctx["status"] = "pending_confirm"
                    ctx["action"] = "submit_confirm"
                    ctx["preview"] = result.get("preview") or {}
                    ctx["explanation"] = result.get("explanation") or ""
                    ctx["frame_id"] = ctx.get("frame_id")
                    wi_cf = (
                        ctx.get("workflow_intent") or ctx.get("matched_intent") or ""
                    ).strip()
                    if wi_cf == "create_contract":
                        default_reply = "请确认以下合同内容是否符合预期。"
                    elif wi_cf == "update_contract":
                        default_reply = "请确认以下合同修改是否符合预期。"
                    elif wi_cf == "delete_contract" or (
                        wi_cf == "delete_simple_entity"
                    ):
                        default_reply = "请确认是否删除该记录。"
                    elif wi_cf == "update_simple_entity":
                        default_reply = "请确认以下修改是否符合预期。"
                    else:
                        default_reply = "请确认以下新建内容是否符合预期。"
                    ctx["reply"] = (ctx.get("reply") or "") or default_reply
                    reply_set = True
                    stop_loop = True
                    break

                if name == "ask_user" and result.get("status") == "pending_user_reply":
                    q = (result.get("question") or "").strip()
                    hint = result.get("hint")
                    if hint:
                        ctx["reply"] = f"{q}\n{hint}"
                    else:
                        ctx["reply"] = q or "请补充更多信息。"
                    ctx["status"] = "collecting"
                    reply_set = True
                    stop_loop = True
                    break

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tid,
                        "content": json.dumps(result, ensure_ascii=False, default=str),
                    }
                )

            if stop_loop:
                if collecting_end_reason != "max_tools_cap":
                    collecting_end_reason = "stop_loop"
                break

        if not reply_set:
            ctx["reply"] = "我需要更多信息，能否重新描述一下需求？"
            ctx["status"] = "collecting"
            collecting_end_reason = "no_reply"

        logger.info(
            "[collecting.结束] frame=%s reason=%s final_status=%s",
            ctx.get("frame_id"),
            collecting_end_reason,
            ctx.get("status"),
        )
    except Exception as e:
        logger.exception("[collecting] run_collecting 失败: %s", e)
        ctx["reply"] = f"处理失败：{e}"
        ctx["status"] = "error"
        logger.info(
            "[collecting.结束] frame=%s reason=%s final_status=%s",
            ctx.get("frame_id"),
            "error",
            "error",
        )
    return ctx
