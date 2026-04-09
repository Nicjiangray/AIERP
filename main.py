"""AI·ERP 后端：数据库表 API + 与前端约定的 /ai/chat（直连 LLM）。"""

from __future__ import annotations

import asyncio
import io
import json
import logging

import bcrypt
import os
import re
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

import psycopg2.extras
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from psycopg2.extras import Json

from tools.database import close_all_pools, db_conn
from engine.memory_manager import clean_session, compress_longterm
from engine.prompt_builder import build_prompt
from engine.sql_engine import confirm_execute_sql, execute_sql, preview_sql
from llm.client import (
    chat,
    chat_expert_only,
    chat_stream,
    parse_file,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    _orch_h = logging.StreamHandler(sys.stderr)
    _orch_h.setLevel(logging.INFO)
    _orch_h.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
    logger.addHandler(_orch_h)
logger.propagate = False

MAX_TOOL_ROUNDS = 3
MAX_EXPERT_STEPS = 5


def _extract_json_from_text(text: str) -> Optional[Dict[str, Any]]:
    """从文本中提取第一个 JSON 对象（支持 ```json 代码块；正文用 JSONDecoder.raw_decode 处理嵌套）。"""
    if not text or not isinstance(text, str):
        return None
    raw = text.strip()
    for m in re.finditer(r"```(?:json)?\s*([\s\S]*?)```", raw, re.I):
        chunk = m.group(1).strip()
        try:
            obj = json.loads(chunk)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    dec = json.JSONDecoder()
    start = raw.find("{")
    while start >= 0:
        try:
            obj, _end = dec.raw_decode(raw, start)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
        start = raw.find("{", start + 1)
    return None


def _extract_tool_block(text: str) -> Optional[Dict[str, str]]:
    """从 LLM 回复中提取 [TOOL]...[/TOOL] 块，解析为 dict。"""
    m = re.search(r"\[TOOL\]\s*(.*?)\s*\[/TOOL\]", text, re.DOTALL | re.I)
    if not m:
        return None
    block: Dict[str, str] = {}
    for line in m.group(1).strip().splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, val = line.partition(":")
        block[key.strip()] = val.strip()
    if "action" not in block:
        return None
    logger.info(
        f"orchestrator: 检测到 [TOOL] 块: action={block.get('action')}, intent={block.get('intent')}"
    )
    return block


def _format_tool_result(tool_block: Dict[str, str], result: Dict[str, Any]) -> str:
    """把 tool_expert 最终结果格式化为 [TOOL_RESULT] 块注入给助手。"""
    action = tool_block.get("action", "")
    lines = ["[TOOL_RESULT]", f"action: {action}"]

    if not result.get("success"):
        lines.append("status: error")
        lines.append(f"error: {result.get('error', '执行失败')}")
        if result.get("need_info"):
            lines.append(f"need_info: {result['need_info']}")
        if result.get("candidates") is not None:
            c = result["candidates"]
            lines.append(
                f"candidates: {c if isinstance(c, str) else json.dumps(c, ensure_ascii=False, default=str)}"
            )
    elif result.get("sql") and result.get("stage") == "preview":
        lines.append("status: pending_confirm")
        lines.append(f"summary: {result.get('summary', '')}")
    else:
        lines.append("status: success")
        lines.append(f"summary: {result.get('summary', '')}")

    lines.append("[/TOOL_RESULT]")
    result_text = "\n".join(lines)
    logger.info(f"orchestrator: 回注给助手的 TOOL_RESULT:\n{result_text}")
    return result_text


def _is_write_preview_result(result: Dict[str, Any]) -> bool:
    """tool_expert 返回的 preview_sql 成功结果（待用户确认）。"""
    if not result.get("success"):
        return False
    if result.get("stage") != "preview":
        return False
    if not result.get("sql"):
        return False
    op = (result.get("operation") or "").lower()
    return op in ("insert", "update", "delete")


async def _call_tool_expert(tool_block: Dict[str, str]) -> Dict[str, Any]:
    """
    把助手的意图指令发给 tool_expert；expert 内部多步执行（query / write / done），
    闭环完成后返回最终结果（摘要、错误或写预览）。
    """
    expert_system = build_prompt("tool_expert")
    user_msg = "\n".join(f"{k}: {v}" for k, v in tool_block.items())
    logger.info(f"orchestrator: 调用 tool_expert, 发送内容:\n{user_msg}")
    expert_messages: List[Dict[str, Any]] = [
        {"role": "system", "content": expert_system},
        {"role": "user", "content": user_msg},
    ]

    def _run_expert() -> str:
        return chat(expert_messages)

    for step in range(MAX_EXPERT_STEPS):
        logger.info(f"tool_expert: 第 {step + 1}/{MAX_EXPERT_STEPS} 步")
        expert_reply = await asyncio.to_thread(_run_expert)
        logger.info(f"tool_expert: 回复:\n{expert_reply}")

        payload = _extract_json_from_text(expert_reply)
        if not payload:
            return {"success": False, "error": "工具专家未返回有效指令"}

        logger.info(f"orchestrator: expert payload: {payload}")

        if payload.get("done"):
            out = {
                "success": bool(payload.get("success", False)),
                "summary": str(payload.get("summary", "") or ""),
                "error": payload.get("error"),
                "need_info": payload.get("need_info"),
                "candidates": payload.get("candidates"),
            }
            logger.info(
                f"orchestrator: engine 执行结果: success={out.get('success')}, keys={list(out.keys())}"
            )
            return out

        sql = (payload.get("sql") or "").strip()
        cmd_type = (payload.get("type") or "").strip().lower()
        raw_params = payload.get("params")
        params_list: List[Any] = raw_params if isinstance(raw_params, list) else []

        if cmd_type == "query":
            if not sql:
                return {"success": False, "error": "query 指令缺少 sql"}

            def _do_query() -> Dict[str, Any]:
                with db_conn() as conn:
                    return execute_sql(sql, params_list, conn)

            exec_result = await asyncio.to_thread(_do_query)
            logger.info(
                f"tool_expert: query 结果: success={exec_result.get('success')}, count={exec_result.get('count')}"
            )
            logger.info(
                f"orchestrator: engine 执行结果: success={exec_result.get('success')}, keys={list(exec_result.keys())}"
            )
            result_text = json.dumps(exec_result, ensure_ascii=False, default=str)
            expert_messages.append({"role": "assistant", "content": expert_reply})
            expert_messages.append(
                {"role": "user", "content": f"执行结果：\n{result_text}"}
            )
            continue

        if cmd_type == "write":
            if not sql:
                return {"success": False, "error": "write 指令缺少 sql"}
            explanation = str(payload.get("explanation") or "")

            def _do_preview() -> Dict[str, Any]:
                with db_conn() as conn:
                    return preview_sql(sql, params_list, conn)

            exec_result = await asyncio.to_thread(_do_preview)
            logger.info(
                f"tool_expert: write preview 结果: success={exec_result.get('success')}"
            )
            logger.info(
                f"orchestrator: engine 执行结果: success={exec_result.get('success')}, keys={list(exec_result.keys())}"
            )
            if exec_result.get("success"):
                if explanation:
                    exec_result["summary"] = explanation
                return exec_result

            fail_text = json.dumps(exec_result, ensure_ascii=False, default=str)
            expert_messages.append({"role": "assistant", "content": expert_reply})
            expert_messages.append(
                {"role": "user", "content": f"执行结果：\n{fail_text}"}
            )
            continue

        return {"success": False, "error": f"未知指令类型: {cmd_type}"}

    return {"success": False, "error": "操作步骤过多，未能完成"}


def _insert_ai_chat_row(session_id: int, role: str, content: str) -> None:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO business.ai_chat (session_id, role, content)
                VALUES (%s, %s, %s)
                """,
                (session_id, role, content),
            )
        conn.commit()


def _env_bool(key: str, default: bool) -> bool:
    v = os.environ.get(key)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _cors_allow_origins_and_credentials() -> tuple[list[str], bool]:
    raw = (os.environ.get("CORS_ORIGINS") or "").strip()

    def _warn_star() -> None:
        if _env_bool("CORS_ALLOW_CREDENTIALS", False):
            logger.warning(
                "CORS_ALLOW_CREDENTIALS=true 与 CORS_ORIGINS=* 不兼容，已强制设为 false"
            )

    if raw == "*":
        _warn_star()
        return ["*"], False
    if not raw:
        origins = [
            "http://127.0.0.1:5173",
            "http://localhost:5173",
            "http://127.0.0.1:3000",
            "http://localhost:3000",
        ]
        return origins, _env_bool("CORS_ALLOW_CREDENTIALS", True)
    origins = [p.strip() for p in raw.split(",") if p.strip()]
    if not origins:
        return (
            ["http://127.0.0.1:5173", "http://localhost:5173"],
            _env_bool("CORS_ALLOW_CREDENTIALS", True),
        )
    if origins == ["*"]:
        _warn_star()
        return ["*"], False
    return origins, _env_bool("CORS_ALLOW_CREDENTIALS", True)


@asynccontextmanager
async def _app_lifespan(app: FastAPI):
    yield
    close_all_pools()


app = FastAPI(title="AI·ERP Backend", lifespan=_app_lifespan)
print(
    f"[aierp] 已加载 main.py: {Path(__file__).resolve()}",
    flush=True,
)

_cors_origins, _cors_cred = _cors_allow_origins_and_credentials()
print(
    f"[aierp] CORS allow_origins={_cors_origins!r} allow_credentials={_cors_cred}",
    flush=True,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_cred,
    allow_methods=["*"],
    allow_headers=["*"],
)

IDENT_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
ALLOWED_SCHEMAS = frozenset({"meta", "business", "ai_assistant"})
BLOCK_COLUMNS = frozenset({"password_hash", "embedding", "intent_embedding"})


def assert_ident(name: str, label: str = "identifier") -> None:
    if not name or not IDENT_RE.match(name):
        raise HTTPException(status_code=400, detail=f"Invalid {label}: {name}")


def fetch_column_meta(cur, schema: str, table: str) -> List[Dict[str, Any]]:
    cur.execute(
        """
        SELECT c.column_name AS name,
               c.data_type AS data_type,
               c.udt_name AS udt_name,
               c.is_nullable AS is_nullable,
               c.column_default AS column_default,
               c.is_generated AS is_generated,
               c.identity_generation AS identity_generation
        FROM information_schema.columns c
        WHERE c.table_schema = %s AND c.table_name = %s
        ORDER BY c.ordinal_position
        """,
        (schema, table),
    )
    return [dict(r) for r in cur.fetchall()]


def writable_columns(meta: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for r in meta:
        name = r["name"]
        if name in BLOCK_COLUMNS:
            continue
        if r.get("udt_name") == "vector":
            continue
        if (r.get("is_generated") or "NEVER") == "ALWAYS":
            continue
        out.append(r)
    return out


def coerce_cell(val: Any, udt_name: str, data_type: str, nullable: str) -> Any:
    if val is None:
        return None
    if val == "" and nullable == "YES":
        return None
    if isinstance(val, (dict, list)):
        return Json(val)
    return val


def table_has_id(meta: List[Dict[str, Any]]) -> bool:
    return any(r["name"] == "id" for r in meta)


LIKE_ESC = "!"


def filterable_column_names(meta: List[Dict[str, Any]]) -> List[str]:
    out = []
    for r in meta:
        n = r["name"]
        if n in BLOCK_COLUMNS:
            continue
        if r.get("udt_name") == "vector":
            continue
        out.append(n)
    return out


def _like_escape(s: str, esc: str = LIKE_ESC) -> str:
    if len(esc) != 1:
        raise ValueError("escape must be one character")
    return (
        s.replace(esc, esc + esc).replace("%", esc + "%").replace("_", esc + "_")
    )


def parse_filters_query(filters_raw: Optional[str]) -> Dict[str, str]:
    if not filters_raw or not str(filters_raw).strip():
        return {}
    try:
        o = json.loads(filters_raw)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="filters 须为合法 JSON 对象")
    if not isinstance(o, dict):
        raise HTTPException(status_code=400, detail="filters 须为 JSON 对象")
    out: Dict[str, str] = {}
    for k, v in o.items():
        if not isinstance(k, str):
            continue
        vs = str(v).strip() if v is not None else ""
        if vs:
            out[k] = vs
    return out


def build_fuzzy_where_clause(
    meta: List[Dict[str, Any]],
    filters: Dict[str, str],
    q_global: Optional[str],
) -> tuple[str, List[Any]]:
    allowed = set(filterable_column_names(meta))
    parts: List[str] = []
    params: List[Any] = []
    esc = LIKE_ESC

    for col, val in filters.items():
        if col not in allowed or not IDENT_RE.match(col):
            continue
        pat = "%" + _like_escape(val, esc) + "%"
        parts.append(f'"{col}"::text ILIKE %s ESCAPE \'{esc}\'')
        params.append(pat)

    qt = (q_global or "").strip()
    if qt:
        pat = "%" + _like_escape(qt, esc) + "%"
        ors: List[str] = []
        for col in sorted(allowed):
            if not IDENT_RE.match(col):
                continue
            ors.append(f'"{col}"::text ILIKE %s ESCAPE \'{esc}\'')
            params.append(pat)
        if ors:
            parts.append("(" + " OR ".join(ors) + ")")

    if not parts:
        return "", []
    return " AND ".join(parts), params


def _order_expr_for_column(meta_row: Dict[str, Any]) -> str:
    col = meta_row["name"]
    if meta_row.get("udt_name") == "vector":
        return f'"{col}"::text'
    return f'"{col}"'


def build_order_clause(
    meta: List[Dict[str, Any]],
    sort_by: Optional[str],
    sort_order_raw: Optional[str],
) -> str:
    by_name = {r["name"]: r for r in meta}
    names_order = [r["name"] for r in meta]
    order = (sort_order_raw or "asc").lower()
    if order not in ("asc", "desc"):
        order = "asc"
    col = sort_by
    if (
        not col
        or col not in by_name
        or not IDENT_RE.match(col)
    ):
        if "id" in by_name:
            col = "id"
            order = "desc"
        elif names_order:
            col = names_order[0]
            order = "asc"
        else:
            return "ORDER BY 1 ASC NULLS LAST"
    expr = _order_expr_for_column(by_name[col])
    return f"ORDER BY {expr} {order.upper()} NULLS LAST"


def normalize_sort_order_param(raw: Optional[str]) -> Optional[str]:
    if not raw or not str(raw).strip():
        return None
    x = str(raw).strip().lower()
    return x if x in ("asc", "desc") else None


def fetch_table_page(
    cur: Any,
    schema: str,
    table_name: str,
    meta: List[Dict[str, Any]],
    filters_raw: Optional[str],
    q: Optional[str],
    sort_by: Optional[str],
    sort_order: Optional[str],
    limit: int,
    offset: int,
    *,
    with_total: bool,
) -> tuple[List[Dict[str, Any]], Optional[int]]:
    fd = (
        parse_filters_query(filters_raw)
        if filters_raw and str(filters_raw).strip()
        else {}
    )
    where_sql, wparams = build_fuzzy_where_clause(meta, fd, q)
    order_clause = build_order_clause(meta, sort_by, sort_order)
    base_from = f'FROM "{schema}"."{table_name}"'
    frag = (" WHERE " + where_sql) if where_sql else ""

    total: Optional[int] = None
    if with_total:
        cur.execute(f"SELECT COUNT(*) AS _cnt {base_from}{frag}", tuple(wparams))
        total = int(cur.fetchone()["_cnt"])

    cur.execute(
        f"SELECT * {base_from}{frag} {order_clause} LIMIT %s OFFSET %s",
        (*wparams, limit, offset),
    )
    rows = [dict(r) for r in cur.fetchall()]
    return rows, total


# ── HTTP：表与导航 ───────────────────────────────────────────────────────────


@app.get("/")
def root():
    return {"status": "ok", "project": "AI·ERP"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/tables")
def list_tables():
    with db_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT table_schema AS schema, table_name AS table
                FROM information_schema.tables
                WHERE table_schema IN ('meta', 'business', 'ai_assistant')
                  AND table_type = 'BASE TABLE'
                ORDER BY table_schema, table_name
                """
            )
            rows = cur.fetchall()
        return [dict(r) for r in rows]


def _schema_by_table_name(conn) -> dict:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT table_name, table_schema
            FROM information_schema.tables
            WHERE table_schema IN ('meta', 'business', 'ai_assistant') AND table_type = 'BASE TABLE'
            """
        )
        m = {}
        for r in cur.fetchall():
            m[r["table_name"]] = r["table_schema"]
        return m


@app.get("/nav/tables")
def list_nav_tables():
    with db_conn() as conn:
        schema_map = _schema_by_table_name(conn)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT table_name, table_cn, nav_section, nav_order, nav_parent_table
                FROM meta.meta_tables
                WHERE nav_section IS NOT NULL
                ORDER BY nav_section, nav_order, table_name
                """
            )
            rows = cur.fetchall()
        out = {"design": [], "prep": [], "run": []}
        for r in rows:
            sec = r.get("nav_section")
            if sec not in out:
                continue
            sch = schema_map.get(r["table_name"])
            if not sch:
                continue
            out[sec].append(
                {
                    "schema": sch,
                    "table_name": r["table_name"],
                    "table_cn": r["table_cn"],
                    "nav_order": r["nav_order"],
                    "nav_parent_table": r["nav_parent_table"],
                }
            )
        return out


@app.get("/table-columns/{table_name}")
def get_table_column_labels(table_name: str):
    with db_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT field_name, COALESCE(menu_label_cn, field_name) AS menu_label_cn,
                       COALESCE(menu_label_en, field_name) AS menu_label_en,
                       data_type
                FROM meta.meta_fields
                WHERE table_name = %s
                ORDER BY id
                """,
                (table_name,),
            )
            cols = cur.fetchall()
        return {"columns": [dict(c) for c in cols]}


@app.get("/table/{schema}/{table_name}")
def get_table(
    schema: str,
    table_name: str,
    limit: int = 500,
    offset: int = Query(0, ge=0, le=1_000_000),
    sort_by: Optional[str] = Query(None),
    sort_order: Optional[str] = Query(None),
    filters: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
):
    if schema not in ALLOWED_SCHEMAS:
        raise HTTPException(status_code=400, detail="Invalid schema")
    assert_ident(table_name, "table_name")
    lim = min(max(1, limit), 5000)
    off = max(0, min(offset, 1_000_000))
    so = normalize_sort_order_param(sort_order)
    with db_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            try:
                meta = fetch_column_meta(cur, schema, table_name)
                if not meta:
                    raise HTTPException(status_code=404, detail="Table not found")
                rows, total = fetch_table_page(
                    cur,
                    schema,
                    table_name,
                    meta,
                    filters,
                    q,
                    sort_by,
                    so,
                    lim,
                    off,
                    with_total=True,
                )
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=400, detail=str(e)) from e
            return {"rows": rows, "total": total}


def _export_cell_value(val: Any, col_meta: Optional[Dict[str, Any]]) -> Any:
    if val is None:
        return None
    udt = (col_meta or {}).get("udt_name")
    if udt == "vector":
        return "(向量，已省略)"
    if isinstance(val, uuid.UUID):
        return str(val)
    if isinstance(val, (bytes, memoryview)):
        return "(二进制)"
    if isinstance(val, (dict, list)):
        return json.dumps(val, ensure_ascii=False)
    if isinstance(val, Decimal):
        return str(val)
    if isinstance(val, datetime):
        if val.tzinfo is not None:
            return val.astimezone(timezone.utc).replace(tzinfo=None)
        return val
    if isinstance(val, date):
        return val
    if isinstance(val, time):
        if val.tzinfo is not None:
            return val.replace(tzinfo=None)
        return val
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float, str)):
        return val
    return str(val)


def _resolve_export_columns(
    meta: List[Dict[str, Any]], cols_param: Optional[str]
) -> List[str]:
    all_names = [r["name"] for r in meta]
    name_set = set(all_names)
    if not cols_param or not cols_param.strip():
        return all_names
    out: List[str] = []
    seen = set()
    for raw in cols_param.split(","):
        c = raw.strip()
        if not c or c in seen:
            continue
        if not IDENT_RE.match(c) or c not in name_set:
            continue
        seen.add(c)
        out.append(c)
    return out if out else all_names


@app.get("/export/xlsx/{schema}/{table_name}")
def export_table_xlsx(
    schema: str,
    table_name: str,
    limit: int = 5000,
    cols: Optional[str] = None,
    filters: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    sort_by: Optional[str] = Query(None),
    sort_order: Optional[str] = Query(None),
):
    try:
        from openpyxl import Workbook
    except ImportError as e:
        raise HTTPException(
            status_code=503,
            detail="缺少 openpyxl，无法导出。请 pip install openpyxl",
        ) from e

    if schema not in ALLOWED_SCHEMAS:
        raise HTTPException(status_code=400, detail="Invalid schema")
    assert_ident(table_name, "table_name")
    lim = min(max(1, limit), 10000)
    so = normalize_sort_order_param(sort_order)

    with db_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            meta = fetch_column_meta(cur, schema, table_name)
            if not meta:
                raise HTTPException(status_code=404, detail="Table not found")

            col_order = _resolve_export_columns(meta, cols)
            name_to_meta = {r["name"]: r for r in meta}

            cur.execute(
                """
                SELECT field_name, COALESCE(menu_label_cn, field_name) AS menu_label_cn
                FROM meta.meta_fields
                WHERE table_name = %s
                """,
                (table_name,),
            )
            label_map = {r["field_name"]: r["menu_label_cn"] for r in cur.fetchall()}
            headers = [label_map.get(c, c) for c in col_order]

            try:
                db_rows_dicts, _ = fetch_table_page(
                    cur,
                    schema,
                    table_name,
                    meta,
                    filters,
                    q,
                    sort_by,
                    so,
                    lim,
                    0,
                    with_total=False,
                )
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=400, detail=str(e)) from e

    wb = Workbook()
    ws = wb.active
    ws.title = "data"[:31]
    ws.append(headers)
    for d in db_rows_dicts:
        ws.append(
            [_export_cell_value(d.get(c), name_to_meta.get(c)) for c in col_order]
        )

    buf = io.BytesIO()
    wb.save(buf)
    body = buf.getvalue()
    fname = f"{table_name}.xlsx"
    return Response(
        content=body,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.post("/table/{schema}/{table_name}")
def table_insert(schema: str, table_name: str, body: Dict[str, Any] = Body(...)):
    if schema not in ALLOWED_SCHEMAS:
        raise HTTPException(status_code=400, detail="Invalid schema")
    assert_ident(schema, "schema")
    assert_ident(table_name, "table_name")
    with db_conn() as conn:
        with conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                meta = fetch_column_meta(cur, schema, table_name)
                if not meta:
                    raise HTTPException(status_code=404, detail="Table not found")
                allowed = {r["name"]: r for r in writable_columns(meta)}
                cols = []
                vals = []
                for k, v in body.items():
                    if k not in allowed:
                        continue
                    r = allowed[k]
                    if k == "id" and (v is None or v == ""):
                        continue
                    cols.append(k)
                    vals.append(
                        coerce_cell(v, r["udt_name"], r["data_type"], r["is_nullable"])
                    )
                if not cols:
                    raise HTTPException(status_code=400, detail="No writable columns in body")
                qcols = ",".join(f'"{c}"' for c in cols)
                ph = ",".join(["%s"] * len(cols))
                sql = f'INSERT INTO "{schema}"."{table_name}" ({qcols}) VALUES ({ph}) RETURNING *'
                try:
                    cur.execute(sql, vals)
                    row = cur.fetchone()
                except Exception as e:
                    raise HTTPException(status_code=400, detail=str(e)) from e
        return {"row": jsonable_encoder(dict(row))}


@app.patch("/table/{schema}/{table_name}/{row_id}")
def table_update(schema: str, table_name: str, row_id: int, body: Dict[str, Any] = Body(...)):
    if schema not in ALLOWED_SCHEMAS:
        raise HTTPException(status_code=400, detail="Invalid schema")
    assert_ident(schema, "schema")
    assert_ident(table_name, "table_name")
    with db_conn() as conn:
        with conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                meta = fetch_column_meta(cur, schema, table_name)
                if not meta or not table_has_id(meta):
                    raise HTTPException(status_code=400, detail="Table must have integer id column")
                allowed = {r["name"]: r for r in writable_columns(meta)}
                sets = []
                vals = []
                for k, v in body.items():
                    if k == "id":
                        continue
                    if k not in allowed:
                        continue
                    r = allowed[k]
                    sets.append(f'"{k}" = %s')
                    vals.append(
                        coerce_cell(v, r["udt_name"], r["data_type"], r["is_nullable"])
                    )
                if not sets:
                    raise HTTPException(status_code=400, detail="No updatable fields")
                vals.append(row_id)
                sql = f'UPDATE "{schema}"."{table_name}" SET {", ".join(sets)} WHERE id = %s RETURNING *'
                try:
                    cur.execute(sql, vals)
                    row = cur.fetchone()
                    if not row:
                        raise HTTPException(status_code=404, detail="Row not found")
                except HTTPException:
                    raise
                except Exception as e:
                    raise HTTPException(status_code=400, detail=str(e)) from e
        return {"row": jsonable_encoder(dict(row))}


@app.delete("/table/{schema}/{table_name}/{row_id}")
def table_delete(schema: str, table_name: str, row_id: int):
    if schema not in ALLOWED_SCHEMAS:
        raise HTTPException(status_code=400, detail="Invalid schema")
    assert_ident(schema, "schema")
    assert_ident(table_name, "table_name")
    with db_conn() as conn:
        with conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                meta = fetch_column_meta(cur, schema, table_name)
                if not meta or not table_has_id(meta):
                    raise HTTPException(status_code=400, detail="Table must have integer id column")
                try:
                    cur.execute(
                        f'DELETE FROM "{schema}"."{table_name}" WHERE id = %s RETURNING *',
                        (row_id,),
                    )
                    row = cur.fetchone()
                    if not row:
                        raise HTTPException(status_code=404, detail="Row not found")
                except HTTPException:
                    raise
                except Exception as e:
                    raise HTTPException(status_code=400, detail=str(e)) from e
        return {"row": jsonable_encoder(dict(row))}


# ── 认证（简单登录，无 JWT）──────────────────────────────────────────────────


class LoginRequest(BaseModel):
    login_name: str
    password: str


def _row_to_user_dict(row: tuple) -> Dict[str, Any]:
    """employees 查询行 → 前端 user 对象（id 为 employee id）。"""
    eid, emp_code, name, department, role_tags = row
    return {
        "id": int(eid),
        "emp_code": emp_code or "",
        "name": name or "",
        "department": department or "",
        "role_tags": role_tags if role_tags is not None else "",
    }


def _fetch_employee_user(cur, employee_id: int) -> Optional[Dict[str, Any]]:
    cur.execute(
        """
        SELECT id, emp_code, name, department, role_tags
        FROM business.employees
        WHERE id = %s
        """,
        (employee_id,),
    )
    row = cur.fetchone()
    if not row:
        return None
    return _row_to_user_dict(row)


@app.post("/auth/login")
async def auth_login(req: LoginRequest):
    """
    简单登录：验证 login_name + password，返回 user（employee 维度）。
    不做 JWT；前端保存 user_id 后随请求携带。
    """
    name = (req.login_name or "").strip()
    if not name or not req.password:
        return {"success": False, "error": "用户名或密码错误"}

    try:
        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, employee_id, password_hash, is_active
                    FROM business.user_accounts
                    WHERE LOWER(login_name) = LOWER(%s)
                    """,
                    (name,),
                )
                row = cur.fetchone()
                if not row or not row[2]:
                    return {"success": False, "error": "用户名或密码错误"}
                if not row[3]:
                    return {"success": False, "error": "用户名或密码错误"}

                ph = row[2]
                if isinstance(ph, memoryview):
                    ph = ph.tobytes().decode("utf-8", errors="replace")
                else:
                    ph = str(ph)

                try:
                    ok = bcrypt.checkpw(
                        req.password.encode("utf-8"),
                        ph.encode("utf-8"),
                    )
                except ValueError:
                    ok = False
                if not ok:
                    return {"success": False, "error": "用户名或密码错误"}

                ua_id = int(row[0])
                employee_id = int(row[1])

                user = _fetch_employee_user(cur, employee_id)
                if not user:
                    return {"success": False, "error": "用户名或密码错误"}

                cur.execute(
                    """
                    UPDATE business.user_accounts
                    SET last_login_at = NOW(), updated_at = NOW()
                    WHERE id = %s
                    """,
                    (ua_id,),
                )
                try:
                    cur.execute(
                        """
                        UPDATE business.employees
                        SET last_login_at = NOW()
                        WHERE id = %s
                        """,
                        (employee_id,),
                    )
                except Exception:
                    pass

            conn.commit()
    except Exception as e:
        logger.exception("auth_login: %s", e)
        return {"success": False, "error": "登录失败，请稍后重试"}

    return {"success": True, "user": user}


@app.get("/auth/me")
async def auth_me(user_id: int = Query(..., description="employees.id")):
    """根据 user_id（员工 id）恢复登录态。"""
    try:
        with db_conn() as conn:
            with conn.cursor() as cur:
                user = _fetch_employee_user(cur, user_id)
    except Exception as e:
        logger.exception("auth_me: %s", e)
        raise HTTPException(status_code=500, detail="查询失败") from e

    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    return {"user": user}


# ── AI Chat（直连 LLM，与前端 AiPanel 协议对齐）──────────────────────────────


class ChatMessage(BaseModel):
    role: str
    content: str


class WriteConfirmRequest(BaseModel):
    """确认执行 write：传入 preview 返回的 payload；兼容旧字段 frame_id（已停用）。"""

    payload: Optional[Dict[str, Any]] = None
    frame_id: Optional[int] = None
    confirmed: Optional[bool] = None


class ChatRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    messages: List[ChatMessage]
    images: Optional[List[str]] = None
    files: Optional[List[str]] = None
    confirm: Optional[bool] = False
    pending_token: Optional[str] = Field(default=None, alias="pendingToken")
    stream: Optional[bool] = False
    invite_expert: Optional[bool] = Field(default=False, alias="inviteExpert")
    session_id: Optional[int] = None
    user_id: Optional[int] = None


def _sse_line(obj: Dict[str, Any]) -> bytes:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode("utf-8")


@app.post("/ai/confirm")
async def ai_confirm(body: WriteConfirmRequest):
    """执行经 preview_sql 审核过的写操作 SQL（payload 含 sql、params）。"""

    if body.payload is not None:
        pass
    elif body.frame_id is not None:
        return {
            "success": False,
            "error": "当前版本请使用对话内「写入预览」确认流程，不再支持 frame_id 确认。",
        }
    else:
        raise HTTPException(status_code=400, detail="缺少 payload")

    sql = body.payload.get("sql") if isinstance(body.payload, dict) else None
    params = body.payload.get("params", []) if isinstance(body.payload, dict) else []
    if not sql or not isinstance(sql, str):
        raise HTTPException(status_code=400, detail="缺少 sql")
    if not isinstance(params, list):
        raise HTTPException(status_code=400, detail="params 必须为数组")

    def _run() -> Dict[str, Any]:
        with db_conn() as conn:
            try:
                return confirm_execute_sql(sql, params, conn)
            except Exception as e:
                logger.exception("ai/confirm confirm_execute_sql: %s", e)
                return {"success": False, "error": f"执行异常：{e}"}

    return await asyncio.to_thread(_run)


@app.post("/ai/memory/clean-session")
async def api_clean_session(body: Dict[str, Any] = Body(...)):
    """手动触发：清洗指定 session 的对话，提取要点写入 ai_memory。"""
    raw = body.get("session_id")
    if raw is None:
        raise HTTPException(status_code=400, detail="需要 session_id")
    try:
        session_id = int(raw)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="session_id 必须为整数")
    result = await asyncio.to_thread(clean_session, session_id)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "清洗失败"))
    return result


@app.post("/ai/memory/compress-longterm")
async def api_compress_longterm(body: Dict[str, Any] = Body(...)):
    """手动触发：压缩指定用户的长期记忆。"""
    raw = body.get("user_id")
    if raw is None:
        raise HTTPException(status_code=400, detail="需要 user_id")
    try:
        user_id = int(raw)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="user_id 必须为整数")
    result = await asyncio.to_thread(compress_longterm, user_id)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "压缩失败"))
    return result


def _chunk_text_for_sse(text: str, size: int = 48) -> List[str]:
    if not text:
        return [""]
    return [text[i : i + size] for i in range(0, len(text), size)]


def _last_user_message_text(messages: List[ChatMessage]) -> str:
    for m in reversed(messages):
        if m.role == "user":
            return m.content or ""
    return ""


@app.post("/ai/chat")
async def ai_chat(req: ChatRequest):
    if req.pending_token:
        raise HTTPException(
            status_code=400,
            detail="当前版本不支持 pendingToken 恢复，请发起新对话。",
        )
    if req.confirm:
        raise HTTPException(
            status_code=400,
            detail="当前版本不支持在此确认数据库操作；请使用旧后端或关闭「确认」类流程。",
        )

    session_id: Optional[int] = req.session_id
    user_id: Optional[int] = req.user_id

    if user_id is not None and session_id is None:
        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO business.ai_sessions (user_id, status)
                    VALUES (%s, 'in_progress')
                    RETURNING id
                    """,
                    (user_id,),
                )
                row = cur.fetchone()
                session_id = int(row[0]) if row else None
            conn.commit()

    history_messages = [{"role": m.role, "content": m.content} for m in req.messages]
    file_summaries: List[str] = []

    if req.files:
        for file_payload in req.files:
            parsed = await asyncio.to_thread(parse_file, file_payload)
            if parsed.get("summary"):
                file_summaries.append(str(parsed["summary"]))
            p_type = parsed.get("type")
            if p_type == "text":
                extra = (parsed.get("content") or "").strip()
                if extra:
                    for m in reversed(history_messages):
                        if m.get("role") == "user":
                            m["content"] = (m.get("content") or "") + "\n\n【附件】\n" + extra
                            break

    system_prompt = build_prompt("assistant", user_id=user_id)
    api_messages: List[Dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
    ] + history_messages

    use_expert = bool(req.invite_expert)
    user_msg_for_log = _last_user_message_text(req.messages)

    # ── SSE 流式（前端默认 stream=true）──────────────────────────────
    if req.stream:

        async def sse_gen():
            try:
                yield _sse_line({"type": "start", "session_id": session_id})
                meta_payload = {
                    "type": "meta",
                    "intent": "chat",
                    "requiresConfirm": False,
                    "pendingToken": None,
                    "fileSummaries": file_summaries,
                    "data": {"main": [], "sub": []},
                }
                yield _sse_line(meta_payload)

                work_msgs: List[Dict[str, Any]] = list(api_messages)

                if user_id is not None and session_id is not None:
                    _insert_ai_chat_row(session_id, "user", user_msg_for_log)

                for _round in range(MAX_TOOL_ROUNDS):
                    logger.info(
                        f"orchestrator: 进入第 {_round + 1}/{MAX_TOOL_ROUNDS} 轮"
                    )
                    full_reply = ""
                    if use_expert:
                        full_reply = await asyncio.to_thread(chat_expert_only, work_msgs)
                        for part in _chunk_text_for_sse(full_reply):
                            yield _sse_line({"type": "delta", "text": part})
                    else:
                        async for piece in chat_stream(work_msgs):
                            if piece:
                                full_reply += piece
                                yield _sse_line({"type": "delta", "text": piece})

                    if user_id is not None and session_id is not None:
                        _insert_ai_chat_row(session_id, "assistant", full_reply)

                    tool_block = _extract_tool_block(full_reply)
                    if not tool_block:
                        break

                    result = await _call_tool_expert(tool_block)

                    if _is_write_preview_result(result):
                        result_text = _format_tool_result(tool_block, result)
                        if user_id is not None and session_id is not None:
                            _insert_ai_chat_row(session_id, "tool", result_text)
                        yield _sse_line(
                            {
                                "type": "write_preview",
                                "stage": result.get("stage"),
                                "summary": result.get("summary"),
                                "payload": result.get("payload")
                                or {
                                    "sql": result.get("sql"),
                                    "params": result.get("params") or [],
                                },
                                "operation": result.get("operation"),
                                "table": result.get("table"),
                            }
                        )
                        break

                    result_text = _format_tool_result(tool_block, result)
                    if user_id is not None and session_id is not None:
                        _insert_ai_chat_row(session_id, "tool", result_text)

                    work_msgs.append({"role": "assistant", "content": full_reply})
                    work_msgs.append({"role": "user", "content": result_text})

                yield _sse_line(
                    {
                        "type": "done",
                        "data": {"main": [], "sub": []},
                    }
                )
            except Exception as e:
                logger.exception("ai/chat SSE: %s", e)
                yield _sse_line({"type": "error", "detail": str(e)})
                yield _sse_line({"type": "done", "data": {"main": [], "sub": []}})

        return StreamingResponse(
            sse_gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # ── 非流式 JSON ─────────────────────────────────────────────────
    work_msgs = list(api_messages)
    pr: Optional[Dict[str, Any]] = None
    reply = ""

    try:
        if user_id is not None and session_id is not None:
            _insert_ai_chat_row(session_id, "user", user_msg_for_log)

        for _round in range(MAX_TOOL_ROUNDS):
            logger.info(f"orchestrator: 进入第 {_round + 1}/{MAX_TOOL_ROUNDS} 轮")
            if use_expert:
                full_reply = await asyncio.to_thread(chat_expert_only, work_msgs)
            else:
                full_reply = await asyncio.to_thread(chat, work_msgs)

            if user_id is not None and session_id is not None:
                _insert_ai_chat_row(session_id, "assistant", full_reply)

            reply = full_reply
            tool_block = _extract_tool_block(full_reply)
            if not tool_block:
                break

            result = await _call_tool_expert(tool_block)

            if _is_write_preview_result(result):
                result_text = _format_tool_result(tool_block, result)
                if user_id is not None and session_id is not None:
                    _insert_ai_chat_row(session_id, "tool", result_text)
                pr = result
                break

            result_text = _format_tool_result(tool_block, result)
            if user_id is not None and session_id is not None:
                _insert_ai_chat_row(session_id, "tool", result_text)

            work_msgs.append({"role": "assistant", "content": full_reply})
            work_msgs.append({"role": "user", "content": result_text})
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    out: Dict[str, Any] = {
        "reply": reply,
        "content": reply,
        "data": {"main": [], "sub": []},
        "intent": "chat",
        "requiresConfirm": False,
        "pendingToken": None,
        "fileSummaries": file_summaries,
        "confirm_required": False,
    }
    if session_id is not None:
        out["session_id"] = session_id
    if pr and pr.get("success"):
        out["stage"] = "preview"
        out["summary"] = pr.get("summary")
        out["payload"] = pr.get("payload") or {
            "sql": pr.get("sql"),
            "params": pr.get("params") or [],
        }
        out["operation"] = pr.get("operation")
        out["table"] = pr.get("table")
    return out
