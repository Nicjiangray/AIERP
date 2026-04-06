#!/usr/bin/env python3
"""
将 business.workflows.example_queries 中每条示例句向量化，写入 query_embeddings。

依赖：本机 Ollama，模型名由环境变量 OLLAMA_EMBED_MODEL 指定（默认 shaw/dmeta-embedding-zh）。

用法（项目根目录）：
  .venv/bin/python scripts/sync_intent_embeddings.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, List

import ollama as ollama_client

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.database import db_conn

EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "shaw/dmeta-embedding-zh")


def _embed_one(text: str) -> List[float] | None:
    try:
        r = ollama_client.embed(model=EMBED_MODEL, input=text)
        if r.embeddings and len(r.embeddings) > 0:
            return [float(x) for x in r.embeddings[0]]
    except Exception as e:
        print(f"[sync_intent_embeddings] ollama.embed 失败: {e}", flush=True)
    try:
        r = ollama_client.embeddings(model=EMBED_MODEL, prompt=text)
        if r.embedding:
            return [float(x) for x in r.embedding]
    except Exception as e:
        print(f"[sync_intent_embeddings] ollama.embeddings 失败: {e}", flush=True)
        return None
    return None


def _parse_example_queries(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    if isinstance(raw, str):
        try:
            j = json.loads(raw)
            if isinstance(j, list):
                return [str(x).strip() for x in j if str(x).strip()]
        except json.JSONDecodeError:
            return []
    return []


def main() -> None:
    sql_apply = _ROOT / "constitution" / "add_workflow_intent_embeddings.sql"
    if sql_apply.is_file():
        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql_apply.read_text(encoding="utf-8"))
            conn.commit()
        print("已执行 constitution/add_workflow_intent_embeddings.sql", flush=True)

    sql_seed = _ROOT / "scripts" / "seed_workflow_example_queries.sql"
    if sql_seed.is_file():
        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql_seed.read_text(encoding="utf-8"))
            conn.commit()
        print("已执行 scripts/seed_workflow_example_queries.sql", flush=True)

    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT intent, example_queries
                FROM business.workflows
                WHERE COALESCE(is_active, 1) = 1
                  AND example_queries IS NOT NULL
                  AND example_queries::text NOT IN ('null', '[]', '')
                ORDER BY intent
                """
            )
            rows = cur.fetchall()

    for intent, ex_raw in rows:
        examples = _parse_example_queries(ex_raw)
        if not examples:
            print(f"[{intent}] 无示例句，跳过", flush=True)
            continue
        vectors: List[List[float]] = []
        for line in examples:
            vec = _embed_one(line)
            if vec is None:
                print(f"[{intent}] 向量化失败，句子: {line!r}，中止该 intent", flush=True)
                sys.exit(1)
            vectors.append(vec)
            print(f"  [{intent}] ok  dim={len(vec)}  {line[:40]}...", flush=True)

        payload = json.dumps(vectors, ensure_ascii=False)
        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE business.workflows
                    SET query_embeddings = %s::jsonb
                    WHERE intent = %s
                    """,
                    (payload, intent),
                )
            conn.commit()
        print(f"[{intent}] 已写入 {len(vectors)} 条向量", flush=True)


if __name__ == "__main__":
    main()
