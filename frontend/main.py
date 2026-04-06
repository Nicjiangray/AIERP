# DEPRECATED: 独立测试用FastAPI，非生产入口，请勿引用
from contextlib import closing

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List
import os
import psycopg2
import psycopg2.extras
import os
import httpx

app = FastAPI(title="AI·ERP Backend")


def _cors():
    raw = (os.environ.get("CORS_ORIGINS") or "").strip()
    if raw == "*":
        return ["*"], False
    if not raw:
        return (
            [
                "http://127.0.0.1:5173",
                "http://localhost:5173",
            ],
            True,
        )
    o = [p.strip() for p in raw.split(",") if p.strip()]
    if o == ["*"]:
        return ["*"], False
    return o, os.environ.get("CORS_ALLOW_CREDENTIALS", "true").lower() in (
        "1",
        "true",
        "yes",
    )


_co, _cc = _cors()

# ── CORS（与仓库根目录 main.py 约定一致：* 时不带 credentials）────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=_co,
    allow_credentials=_cc,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── DB ──────────────────────────────────────────────────────────────────────
def get_conn():
    return psycopg2.connect(
        dbname="aierp",
        user="nicjiang",
        host="localhost",
        port=5432
    )

# ── Routes ──────────────────────────────────────────────────────────────────
@app.get("/")
def root():
    return {"status": "ok", "project": "AI·ERP"}

@app.get("/tables")
def list_tables():
    with closing(get_conn()) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT table_schema AS schema, table_name AS table
                FROM information_schema.tables
                WHERE table_schema IN ('meta', 'business')
                  AND table_type = 'BASE TABLE'
                ORDER BY table_schema, table_name
            """)
            rows = cur.fetchall()
    return [dict(r) for r in rows]

@app.get("/table/{schema}/{table_name}")
def get_table(schema: str, table_name: str, limit: int = 200):
    allowed_schemas = {"meta", "business"}
    if schema not in allowed_schemas:
        raise HTTPException(status_code=400, detail="Invalid schema")
    with closing(get_conn()) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f'SELECT * FROM "{schema}"."{table_name}" LIMIT %s', (limit,))
            rows = cur.fetchall()
    return {"rows": [dict(r) for r in rows]}

# ── AI Chat ─────────────────────────────────────────────────────────────────
class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    messages: List[ChatMessage]

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

@app.post("/ai/chat")
async def ai_chat(req: ChatRequest):
    if not ANTHROPIC_API_KEY:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not set")

    # 构建系统提示：注入数据库上下文
    system = """你是 AI·ERP 系统的智能助手。你熟悉该系统的数据库结构：
- meta schema：仅保留元数据目录表 meta_tables、meta_fields
- business schema：业务与主数据（含规则、人事、主数据及业务表、视图等）

请用简洁、专业的中文回答问题。涉及数据或代码时，使用 Markdown 格式。"""

    messages = [{"role": m.role, "content": m.content} for m in req.messages]

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 1024,
                "system": system,
                "messages": messages,
            }
        )

    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="Anthropic API error")

    data = resp.json()
    content = data["content"][0]["text"]
    return {"content": content}
