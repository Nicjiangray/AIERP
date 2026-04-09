# DEPRECATED: 已迁移至 llm/tools.py，请勿引用
# 已迁移至 llm/tools.py，待确认后删除
from __future__ import annotations

import copy
import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, AsyncIterator, Dict, List, Literal, Optional, Tuple, Union

import openai
import psycopg2.extras
from openai import AsyncOpenAI, OpenAI

from db_pool import db_conn_llm
from llm_client import (
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MODEL_NAME,
    LLM_TIMEOUT_SECONDS,
    _FALLBACK_BASE,
    _FALLBACK_KEY,
    _FALLBACK_MODEL,
    chat as llm_chat_plain,
    chat_stream as llm_chat_stream,
    llm_openai_extra_body,
)

_client = OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY)
_async_client = AsyncOpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY)

_fb_key_tools = _FALLBACK_KEY if _FALLBACK_KEY else "missing-fallback-key"
_fallback_client = OpenAI(base_url=_FALLBACK_BASE, api_key=_fb_key_tools)
_async_fallback_client = AsyncOpenAI(base_url=_FALLBACK_BASE, api_key=_fb_key_tools)

_RETRYABLE_OPENAI = (
    openai.APITimeoutError,
    openai.APIConnectionError,
    openai.RateLimitError,
    openai.APIError,
)

_MAX_TOOL_ROUNDS = 40


def _last_user_text_from_messages(msgs: List[Dict[str, Any]]) -> str:
    for m in reversed(msgs):
        if m.get("role") == "user":
            return str(m.get("content") or "")
    return ""


def _has_tool_turn_after_last_user(msgs: List[Dict[str, Any]]) -> bool:
    """最后一条 user 之后是否已有工具结果（role=tool），用于区分「首轮拒答」与「工具链结束后的纯文本总结」。"""
    last_i = -1
    for i, m in enumerate(msgs):
        if m.get("role") == "user":
            last_i = i
    if last_i < 0:
        return False
    for m in msgs[last_i + 1 :]:
        if m.get("role") == "tool":
            return True
    return False


def _maybe_assert_first_round_tool_use(
    msgs: List[Dict[str, Any]], response_message: Any
) -> None:
    if _has_tool_turn_after_last_user(msgs):
        return
    from main import _assert_tool_was_called

    _assert_tool_was_called(response_message, _last_user_text_from_messages(msgs))


# 待用户确认后执行的删除/删表（仅内存，进程重启失效）
_PENDING_DESTRUCTIVE: Dict[str, Dict[str, Any]] = {}

print(
    f"[aierp] 已加载 llm_tools.py: {Path(__file__).resolve()}",
    flush=True,
)
# 库访问：query_db / execute_db / 删表目标校验 均用 db_pool.db_conn_llm()（助手连接池），
# 与 main.get_llm_db_config() 同一套环境变量；HTTP 路由用 db_conn()，两套池分离。

_DB_TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "query_db",
            "description": (
                "对 PostgreSQL 执行单条只读 SQL（必须以 SELECT 开头），"
                "返回查询结果的 JSON（含 columns 与 rows）。"
                "查询 business 等业务表与 ai_assistant 助手工作区均可。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "单条 SELECT 语句，不要包含多条语句。",
                    },
                },
                "required": ["sql"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_db",
            "description": (
                "对 PostgreSQL 执行单条写操作 SQL，支持 INSERT、UPDATE、DELETE、CREATE、DROP。"
                "新建表、临时表必须放在 schema ai_assistant 下，勿在 business 下 CREATE TABLE。"
                "需要落库时必须调用本工具；禁止仅在对话里贴 SQL 代码块冒充已执行。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "单条写操作 SQL。",
                    },
                },
                "required": ["sql"],
            },
        },
    },
]


def _single_statement(sql: str) -> str:
    s = sql.strip().rstrip(";").strip()
    if ";" in s:
        raise ValueError("仅允许单条 SQL，不能包含分号串联多条语句。")
    return s


def _validate_select_sql(sql: str) -> str:
    s = _single_statement(sql)
    if not re.match(r"\s*SELECT\b", s, re.IGNORECASE | re.DOTALL):
        raise ValueError("query_db 仅允许以 SELECT 开头的只读查询。")
    return s


def _validate_write_sql(sql: str) -> str:
    s = _single_statement(sql)
    head = s.lstrip()
    if not head:
        raise ValueError("SQL 为空。")
    first = re.split(r"\s+", head, maxsplit=1)[0].upper()
    allowed = {"INSERT", "UPDATE", "DELETE", "CREATE", "DROP"}
    if first not in allowed:
        raise ValueError(
            f"execute_db 仅允许以 {', '.join(sorted(allowed))} 开头的语句，当前为：{first}"
        )
    return s


def _norm_ident(tok: str) -> str:
    t = (tok or "").strip()
    if len(t) >= 2 and t[0] == '"' and t[-1] == '"':
        return t[1:-1].replace('""', '"')
    return t.lower()


def _split_top_level_commas(s: str) -> List[str]:
    """按顶层逗号分割（忽略双引号字符串内与圆括号嵌套内的逗号）。"""
    parts: List[str] = []
    depth = 0
    i, start, n = 0, 0, len(s)
    while i < n:
        c = s[i]
        if c == '"':
            i += 1
            while i < n:
                if s[i] == '"':
                    if i + 1 < n and s[i + 1] == '"':
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth = max(0, depth - 1)
        elif c == "," and depth == 0:
            parts.append(s[start:i].strip())
            start = i + 1
        i += 1
    tail = s[start:].strip()
    if tail:
        parts.append(tail)
    return [p for p in parts if p]


def _parse_qualified_table(chunk: str) -> Tuple[str, str]:
    chunk = chunk.strip()
    chunk = re.sub(r"(?is)^ONLY\s+", "", chunk)
    chunk = re.sub(r"(?is)\s+(CASCADE|RESTRICT)\s*$", "", chunk).strip()
    if not chunk:
        return "public", ""
    m = re.fullmatch(
        r'(?s)(?P<s>"[^"]+"|[A-Za-z_][\w]*)\s*\.\s*(?P<t>"[^"]+"|[A-Za-z_][\w]*)',
        chunk,
    )
    if m:
        return _norm_ident(m.group("s")), _norm_ident(m.group("t"))
    return "public", _norm_ident(chunk)


def _extract_delete_targets(sql: str) -> List[Tuple[str, str]]:
    s = _single_statement(sql)
    m = re.search(
        r"(?is)\bDELETE\s+FROM\s+(?:ONLY\s+)?"
        r'(?:(?P<s>"[^"]+"|[A-Za-z_][\w]*)\s*\.\s*)?'
        r'(?P<t>"[^"]+"|[A-Za-z_][\w]*)',
        s,
    )
    if not m:
        return []
    sch_raw, tbl_raw = m.group("s"), m.group("t")
    sch = _norm_ident(sch_raw) if sch_raw else "public"
    tbl = _norm_ident(tbl_raw)
    return [(sch, tbl)] if tbl else []


def _extract_drop_targets(sql: str) -> List[Tuple[str, str]]:
    s = _single_statement(sql)
    if not re.match(r"(?is)\s*DROP\s+TABLE\b", s):
        return []
    m = re.search(r"(?is)DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?", s)
    if not m:
        return []
    rest = s[m.end() :].strip()
    rest = re.sub(r"(?is)\s+(CASCADE|RESTRICT)\s*$", "", rest)
    chunks = _split_top_level_commas(rest)
    out: List[Tuple[str, str]] = []
    for ch in chunks:
        sch, tbl = _parse_qualified_table(ch)
        if tbl:
            out.append((sch, tbl))
    return out


def _destructive_write_keyword(sql: str) -> Optional[str]:
    s = _single_statement(sql)
    head = s.lstrip()
    if not head:
        return None
    kw = re.split(r"\s+", head, maxsplit=1)[0].upper()
    if kw in ("DELETE", "DROP"):
        return kw
    return None


def _targets_for_destructive(sql: str) -> List[Tuple[str, str]]:
    kw = _destructive_write_keyword(sql)
    if kw == "DELETE":
        return _extract_delete_targets(sql)
    if kw == "DROP":
        return _extract_drop_targets(sql)
    return []


def _any_target_owned_by_assistant(
    sql: str, db_config: Dict[str, Any]
) -> bool:
    """DELETE / DROP 是否触及当前 db 用户（助手）拥有的对象。"""
    if _destructive_write_keyword(sql) is None:
        return False
    pairs = _targets_for_destructive(sql)
    if not pairs:
        return True
    assistant = (db_config.get("user") or "").strip()
    if not assistant:
        return False
    with db_conn_llm() as conn:
        with conn.cursor() as cur:
            for sch, tbl in pairs:
                cur.execute(
                    """
                    SELECT 1
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    JOIN pg_roles r ON r.oid = c.relowner
                    WHERE n.nspname = %s AND c.relname = %s AND r.rolname = %s
                      AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
                    """,
                    (sch, tbl, assistant),
                )
                if cur.fetchone():
                    return True
        return False


def _explain_destructive_for_user(
    conversation_messages: List[Dict[str, Any]],
) -> str:
    expl_system = (
        "助手即将对【助手本人在数据库中创建的数据或表】执行不可逆的删除或删表。\n"
        "请根据下列对话上下文，用 2～5 句中文说明：为什么要这样做、对用户有何影响。\n"
        "严格要求：不要出现任何 SQL 语句、表名、列名、schema 名、数据库名、"
        "英文字段名或其它技术标识；用业务语言描述即可。"
    )
    tail = conversation_messages[-16:]
    msgs = [{"role": "system", "content": expl_system}] + tail
    return (llm_chat_plain(msgs, None) or "").strip()


def _run_query_db(sql: str, _db_config: Dict[str, Any]) -> Dict[str, Any]:
    """_db_config 仅兼容 _tool_dispatch 的传参形式，已不用于选库或建连；连接固定为 db_conn_llm()。"""
    validated = _validate_select_sql(sql)
    with db_conn_llm() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(validated)
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description] if cur.description else []
            plain = [dict(r) for r in rows]
        return {"columns": cols, "rows": plain, "row_count": len(plain)}


def _run_execute_db(sql: str, _db_config: Dict[str, Any]) -> Dict[str, Any]:
    """_db_config 仅兼容 _tool_dispatch 传参，已不用于选库或建连；写连接固定为 db_conn_llm()。"""
    validated = _validate_write_sql(sql)
    with db_conn_llm() as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(validated)
            return {"rows_affected": cur.rowcount}


def _build_error_payload(e: Exception, sql: str) -> dict:
    msg = str(e).lower()
    if "does not exist" in msg:
        hint = "对象不存在，建议先用 query_db 查询 information_schema.tables 确认表名和schema后重试"
    elif "permission denied" in msg:
        hint = "权限不足，该操作超出当前账号权限范围，不要重试"
    elif "unique" in msg or "duplicate" in msg:
        hint = "唯一键冲突，建议先用 query_db 查询是否已存在相同记录"
    elif "foreign key" in msg or "violates foreign" in msg:
        hint = "外键约束失败，建议先用 query_db 确认关联主表记录存在后重试"
    elif "syntax error" in msg:
        hint = "SQL语法错误，检查关键字、引号和标点后重试"
    elif "null value" in msg or "not null" in msg:
        hint = "必填字段为空，检查所有 NOT NULL 字段是否已赋值"
    elif "out of range" in msg or "overflow" in msg:
        hint = "数值超出字段范围，检查数字类型是否匹配"
    else:
        hint = "请根据错误信息分析原因，必要时先用 query_db 查询相关表结构再重试"

    return {
        "error": str(e),
        "error_type": type(e).__name__,
        "sql_executed": sql,
        "hint": hint,
    }


def _tool_dispatch(name: str, arguments: str, db_config: Dict[str, Any]) -> str:
    try:
        args = json.loads(arguments or "{}")
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"工具参数不是合法 JSON：{e}"}, ensure_ascii=False)

    sql = args.get("sql")
    if not isinstance(sql, str) or not sql.strip():
        return json.dumps({"error": "缺少或非字符串参数 sql"}, ensure_ascii=False)

    try:
        if name == "query_db":
            payload = _run_query_db(sql, db_config)
        elif name == "execute_db":
            payload = _run_execute_db(sql, db_config)
        else:
            payload = {"error": f"未知工具：{name}"}
    except Exception as e:
        payload = _build_error_payload(e, sql)

    return json.dumps(payload, ensure_ascii=False, default=str)


def _assistant_tool_calls_to_dicts(msg: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for tc in msg.tool_calls or []:
        out.append(
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments or "{}",
                },
            }
        )
    return out


def _batch_needs_destructive_confirm(msg: Any, db_config: Dict[str, Any]) -> bool:
    for tc in msg.tool_calls or []:
        if tc.function.name != "execute_db":
            continue
        try:
            args = json.loads(tc.function.arguments or "{}")
        except json.JSONDecodeError:
            continue
        sql = args.get("sql")
        if not isinstance(sql, str) or not sql.strip():
            continue
        if _destructive_write_keyword(sql) and _any_target_owned_by_assistant(sql, db_config):
            return True
    return False


def _openai_tool_loop_round(
    msgs: List[Dict[str, Any]], db_config: Dict[str, Any]
) -> Union[str, Tuple[Literal["pending"], str, str], Tuple[Literal["continue"], List[Dict[str, Any]]]]:
    """
    单次 LLM 调用；若无 tool_calls 返回最终文本；
    若需确认返回 ("pending", explanation, token)；
    若有 tool_calls 且已执行完本轮工具结果，返回 ("continue", new_messages_to_append)。
    new_messages_to_append = [assistant_msg, tool_msg, ...]
    """
    local_kwargs: Dict[str, Any] = {
        "model": LLM_MODEL_NAME,
        "messages": msgs,
        "tools": _DB_TOOLS,
        "tool_choice": "auto",
        "timeout": LLM_TIMEOUT_SECONDS,
    }
    eb = llm_openai_extra_body()
    if eb is not None:
        local_kwargs["extra_body"] = eb

    resp = None
    last_err: Optional[BaseException] = None
    for _ in range(3):
        try:
            resp = _client.chat.completions.create(**local_kwargs)
            break
        except _RETRYABLE_OPENAI as e:
            last_err = e
            continue

    if resp is None:
        if not _FALLBACK_KEY:
            raise RuntimeError(
                "本地 LLM 已连续失败 3 次，且未配置 FALLBACK_API_KEY，无法使用云端 fallback。"
            ) from last_err
        print("[llm_tools] 本地模型失败，切换 DeepSeek fallback", flush=True)
        fb_kwargs: Dict[str, Any] = {
            "model": _FALLBACK_MODEL,
            "messages": msgs,
            "tools": _DB_TOOLS,
            "tool_choice": "auto",
            "timeout": LLM_TIMEOUT_SECONDS,
        }
        try:
            resp = _fallback_client.chat.completions.create(**fb_kwargs)
        except openai.APITimeoutError as e:
            raise RuntimeError(f"LLM fallback 请求超时：{e}") from e
        except openai.APIConnectionError as e:
            raise RuntimeError(
                f"LLM fallback 连接失败：无法访问 {_FALLBACK_BASE}。请检查网络与 FALLBACK_API_KEY。"
            ) from e
        except openai.RateLimitError as e:
            raise RuntimeError(f"LLM fallback 被限流：{e}") from e
        except openai.APIError as e:
            raise RuntimeError(f"LLM fallback 请求失败：{e}") from e

    msg = resp.choices[0].message

    if not msg.tool_calls:
        _maybe_assert_first_round_tool_use(msgs, msg)
        return (msg.content or "").strip()

    assistant_msg: Dict[str, Any] = {
        "role": "assistant",
        "content": msg.content if msg.content else None,
        "tool_calls": _assistant_tool_calls_to_dicts(msg),
    }

    if _batch_needs_destructive_confirm(msg, db_config):
        explanation = _explain_destructive_for_user(msgs)
        token = str(uuid.uuid4())
        _PENDING_DESTRUCTIVE[token] = {
            "msgs": copy.deepcopy(msgs),
            "assistant_msg": copy.deepcopy(assistant_msg),
            "db_config": copy.deepcopy(db_config),
        }
        print(
            "[llm_tools] 已写入 destructive pending token=%r，当前挂起 keys=%s"
            % (token, list(_PENDING_DESTRUCTIVE.keys())),
            flush=True,
        )
        return ("pending", explanation, token)

    tool_blocks: List[Dict[str, Any]] = [assistant_msg]
    for tc in msg.tool_calls:
        name = tc.function.name
        args = tc.function.arguments or "{}"
        content = _tool_dispatch(name, args, db_config)
        tool_blocks.append(
            {
                "role": "tool",
                "tool_call_id": tc.id,
                "content": content,
            }
        )
    return ("continue", tool_blocks)


def _merge_stream_chunks_to_assistant_message(chunks: List[Any]) -> Any:
    """将流式 chunk 合并为与同步 API message 兼容的 SimpleNamespace（供 _assistant_tool_calls_to_dicts 等使用）。"""
    content = ""
    tool_calls_by_idx: Dict[int, Dict[str, Any]] = {}
    for chunk in chunks:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if not delta:
            continue
        if delta.content:
            content += delta.content
        if delta.tool_calls:
            for tc in delta.tool_calls:
                i = tc.index
                if i not in tool_calls_by_idx:
                    tool_calls_by_idx[i] = {
                        "id": "",
                        "function": {"name": "", "arguments": ""},
                    }
                if tc.id:
                    tool_calls_by_idx[i]["id"] = tc.id
                if tc.function:
                    if tc.function.name:
                        tool_calls_by_idx[i]["function"]["name"] += tc.function.name or ""
                    if tc.function.arguments:
                        tool_calls_by_idx[i]["function"]["arguments"] += (
                            tc.function.arguments or ""
                        )
    tool_calls_ns: List[Any] = []
    for idx in sorted(tool_calls_by_idx.keys()):
        d = tool_calls_by_idx[idx]
        tool_calls_ns.append(
            SimpleNamespace(
                id=d["id"],
                function=SimpleNamespace(
                    name=d["function"]["name"],
                    arguments=d["function"]["arguments"],
                ),
            )
        )
    return SimpleNamespace(
        content=content if content else None,
        tool_calls=tool_calls_ns if tool_calls_ns else None,
    )


@dataclass
class DbStreamPendingMarker:
    """流式管线中先于说明正文 yield，供 main 发 meta（pendingToken）。"""

    token: str


async def _explain_destructive_stream(
    conversation_messages: List[Dict[str, Any]],
) -> AsyncIterator[str]:
    expl_system = (
        "助手即将对【助手本人在数据库中创建的数据或表】执行不可逆的删除或删表。\n"
        "请根据下列对话上下文，用 2～5 句中文说明：为什么要这样做、对用户有何影响。\n"
        "严格要求：不要出现任何 SQL 语句、表名、列名、schema 名、数据库名、"
        "英文字段名或其它技术标识；用业务语言描述即可。"
    )
    tail = conversation_messages[-16:]
    msgs = [{"role": "system", "content": expl_system}] + tail
    async for piece in llm_chat_stream(msgs, None):
        yield piece


async def chat_with_db_outcome_stream(
    messages: List[Dict[str, Any]],
    db_config: Dict[str, Any],
    *,
    resume_token: Optional[str] = None,
) -> AsyncIterator[Union[str, DbStreamPendingMarker]]:
    """
    与 chat_with_db_outcome 相同分支逻辑，但助手正文与复述说明按模型 token 流式 yield。
    遇 destructive pending 时先 yield DbStreamPendingMarker(token)，再 yield 说明字符串片段。
    """
    msgs: List[Dict[str, Any]] = list(messages)

    if resume_token:
        pend = _PENDING_DESTRUCTIVE.pop(resume_token, None)
        if not pend:
            yield "确认失败：令牌无效或已过期，请重新发起请求。"
            return
        msgs = pend["msgs"]
        assistant_msg = pend["assistant_msg"]
        db_cfg = pend["db_config"]
        msgs.append(assistant_msg)
        for tc in assistant_msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            name = fn.get("name", "")
            args = fn.get("arguments", "{}")
            content = _tool_dispatch(str(name), str(args), db_cfg)
            msgs.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": content,
                }
            )
        db_config = db_cfg

    try:
        for _ in range(_MAX_TOOL_ROUNDS):
            local_kwargs: Dict[str, Any] = {
                "model": LLM_MODEL_NAME,
                "messages": msgs,
                "tools": _DB_TOOLS,
                "tool_choice": "auto",
                "timeout": LLM_TIMEOUT_SECONDS,
                "stream": True,
            }
            eb = llm_openai_extra_body()
            if eb is not None:
                local_kwargs["extra_body"] = eb

            stream = None
            last_stream_err: Optional[BaseException] = None
            for _attempt in range(3):
                try:
                    stream = await _async_client.chat.completions.create(**local_kwargs)
                    break
                except _RETRYABLE_OPENAI as e:
                    last_stream_err = e
                    continue

            if stream is None:
                if not _FALLBACK_KEY:
                    raise RuntimeError(
                        "本地 LLM 已连续失败 3 次，且未配置 FALLBACK_API_KEY，无法使用云端 fallback。"
                    ) from last_stream_err
                print("[llm_tools] 本地模型失败，切换 DeepSeek fallback", flush=True)
                fb_kwargs: Dict[str, Any] = {
                    "model": _FALLBACK_MODEL,
                    "messages": msgs,
                    "tools": _DB_TOOLS,
                    "tool_choice": "auto",
                    "timeout": LLM_TIMEOUT_SECONDS,
                    "stream": True,
                }
                try:
                    stream = await _async_fallback_client.chat.completions.create(**fb_kwargs)
                except openai.APITimeoutError as e:
                    raise RuntimeError(f"LLM fallback 请求超时：{e}") from e
                except openai.APIConnectionError as e:
                    raise RuntimeError(
                        f"LLM fallback 连接失败：无法访问 {_FALLBACK_BASE}。请检查网络与 FALLBACK_API_KEY。"
                    ) from e
                except openai.RateLimitError as e:
                    raise RuntimeError(f"LLM fallback 被限流：{e}") from e
                except openai.APIError as e:
                    raise RuntimeError(f"LLM fallback 请求失败：{e}") from e

            chunks: List[Any] = []
            streamed_content = False
            async for chunk in stream:
                chunks.append(chunk)
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if not delta:
                    continue
                if delta.content:
                    streamed_content = True
                    yield delta.content
                # tool_calls 在流结束后合并

            msg = _merge_stream_chunks_to_assistant_message(chunks)
            if msg.tool_calls and streamed_content:
                print(
                    "[llm_tools] 提示：本轮在 tool_calls 前已流式输出正文（部分模型会混排说明与工具）",
                    flush=True,
                )

            if not msg.tool_calls:
                _maybe_assert_first_round_tool_use(msgs, msg)
                return

            assistant_msg: Dict[str, Any] = {
                "role": "assistant",
                "content": msg.content if msg.content else None,
                "tool_calls": _assistant_tool_calls_to_dicts(msg),
            }

            if _batch_needs_destructive_confirm(msg, db_config):
                token = str(uuid.uuid4())
                _PENDING_DESTRUCTIVE[token] = {
                    "msgs": copy.deepcopy(msgs),
                    "assistant_msg": copy.deepcopy(assistant_msg),
                    "db_config": copy.deepcopy(db_config),
                }
                print(
                    "[llm_tools] 流式 pending token=%r | keys=%s"
                    % (token, list(_PENDING_DESTRUCTIVE.keys())),
                    flush=True,
                )
                yield DbStreamPendingMarker(token=token)
                async for piece in _explain_destructive_stream(msgs):
                    yield piece
                return

            tool_blocks: List[Dict[str, Any]] = [assistant_msg]
            for tc in msg.tool_calls:
                name = tc.function.name
                args = tc.function.arguments or "{}"
                content = _tool_dispatch(name, args, db_config)
                tool_blocks.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": content,
                    }
                )
            msgs.extend(tool_blocks)

        yield f"工具调用超过最大轮数（{_MAX_TOOL_ROUNDS}），已中止。"
    except openai.APITimeoutError as e:
        raise RuntimeError(f"LLM 请求超时：{e}") from e
    except openai.APIConnectionError as e:
        raise RuntimeError(
            f"LLM 连接失败：无法访问 {LLM_BASE_URL}。"
            "请检查网络、LLM_BASE_URL、API Key 及服务是否可用（本机 Ollama 需已启动）。"
        ) from e
    except openai.RateLimitError as e:
        raise RuntimeError(f"LLM 被限流，请稍后重试：{e}") from e
    except openai.APIError as e:
        raise RuntimeError(f"LLM 请求失败：{e}") from e


async def resume_pending_db_chat_stream(token: str) -> AsyncIterator[Union[str, DbStreamPendingMarker]]:
    """与 resume_pending_db_chat 相同语义，流式输出正文。"""
    print(
        "[resume_pending_db_chat_stream] token=%r | keys=%s"
        % (token, list(_PENDING_DESTRUCTIVE.keys())),
        flush=True,
    )
    async for item in chat_with_db_outcome_stream([], {}, resume_token=token):
        yield item


def chat_with_db(messages: List[Dict[str, Any]], db_config: Dict[str, Any]) -> str:
    outcome = chat_with_db_outcome(messages, db_config, resume_token=None)
    if outcome.kind == "complete":
        return outcome.text or ""
    if outcome.kind == "pending":
        raise RuntimeError(
            "本次对话需要用户在界面确认后才能执行删除/删表。"
            f" pending_token={outcome.token}"
        )
    return ""


@dataclass
class DbChatOutcome:
    kind: Literal["complete", "pending"]
    text: Optional[str] = None
    explanation: Optional[str] = None
    token: Optional[str] = None


def chat_with_db_outcome(
    messages: List[Dict[str, Any]],
    db_config: Dict[str, Any],
    *,
    resume_token: Optional[str] = None,
) -> DbChatOutcome:
    """
    与 chat_with_db 相同，但若遇助手删除自建数据/表，返回 pending 而非抛错。
    resume_token 由用户确认后传入，用于继续执行被暂缓的工具批次。
    """
    msgs: List[Dict[str, Any]] = list(messages)

    if resume_token:
        pend = _PENDING_DESTRUCTIVE.pop(resume_token, None)
        if not pend:
            return DbChatOutcome(
                kind="complete",
                text="确认失败：令牌无效或已过期，请重新发起请求。",
            )
        msgs = pend["msgs"]
        assistant_msg = pend["assistant_msg"]
        db_cfg = pend["db_config"]
        msgs.append(assistant_msg)
        for tc in assistant_msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            name = fn.get("name", "")
            args = fn.get("arguments") or "{}"
            content = _tool_dispatch(str(name), str(args), db_cfg)
            msgs.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": content,
                }
            )
        db_config = db_cfg

    try:
        for _ in range(_MAX_TOOL_ROUNDS):
            step = _openai_tool_loop_round(msgs, db_config)
            if isinstance(step, str):
                return DbChatOutcome(kind="complete", text=step)
            if step[0] == "pending":
                _, explanation, token = step
                print(
                    "[llm_tools] chat_with_db_outcome 返回 pending，token=%r" % (token,),
                    flush=True,
                )
                return DbChatOutcome(
                    kind="pending", explanation=explanation, token=token
                )
            _, blocks = step
            msgs.extend(blocks)

        return DbChatOutcome(
            kind="complete",
            text=f"工具调用超过最大轮数（{_MAX_TOOL_ROUNDS}），已中止。",
        )
    except openai.APITimeoutError as e:
        raise RuntimeError(f"LLM 请求超时：{e}") from e
    except openai.APIConnectionError as e:
        raise RuntimeError(
            f"LLM 连接失败：无法访问 {LLM_BASE_URL}。"
            "请检查网络、LLM_BASE_URL、API Key 及服务是否可用（本机 Ollama 需已启动）。"
        ) from e
    except openai.RateLimitError as e:
        raise RuntimeError(f"LLM 被限流，请稍后重试：{e}") from e
    except openai.APIError as e:
        raise RuntimeError(f"LLM 请求失败：{e}") from e


def resume_pending_db_chat(token: str) -> str:
    """用户确认后继续工具循环；上下文仅来自服务端挂起的 pending 记录。"""
    print(
        "[resume_pending_db_chat] token=%r | _PENDING_DESTRUCTIVE.keys()=%s"
        % (token, list(_PENDING_DESTRUCTIVE.keys())),
        flush=True,
    )
    out = chat_with_db_outcome([], {}, resume_token=token)
    if out.kind == "pending":
        return out.explanation or "仍需您在界面确认后才能继续。"
    return out.text or ""


if __name__ == "__main__":
    import os

    cfg = {
        "host": os.environ.get("PGHOST", "localhost"),
        "database": os.environ.get("PGDATABASE", "aierp"),
        "user": os.environ.get("PGUSER", "llm_assistant"),
        "password": os.environ.get("PGPASSWORD", "llm_safe_2024"),
    }
    user_messages: List[Dict[str, Any]] = [
        {"role": "user", "content": "请列出当前数据库中的所有表。"},
    ]
    print(chat_with_db(user_messages, cfg))
