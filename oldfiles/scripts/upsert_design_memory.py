#!/usr/bin/env python3
"""
将一条「设计过程记忆」写入 business.design_memories，并用 Ollama 中文 embedding 生成向量。

约定：只收录前端 / 后端 / 数据库结构 相关变更（由 --layers 声明）。

示例：
  python scripts/upsert_design_memory.py \\
    --title "拆分登录表" \\
    --content "将登录从 employees 拆出 business.user_accounts..." \\
    --layers database,backend \\
    --source-ref "constitution/create_auth_tables.sql"

依赖：pip install ollama psycopg2-binary
环境：Ollama 本地服务已启动；模型默认 shaw/dmeta-embedding-zh（与 ollama ls 一致可改 --embed-model）
"""

from __future__ import annotations

import argparse
import sys

import ollama
import psycopg2

CONN = dict(host="localhost", port=5432, dbname="aierp", user="nicjiang")
EXPECTED_DIM = 768
ALLOWED_LAYERS = frozenset({"frontend", "backend", "database"})


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Insert design memory + Ollama embedding")
    p.add_argument("--title", required=True, help="短标题")
    p.add_argument(
        "--content",
        required=True,
        help="供向量检索的正文（建议中文，含背景/改动/原因/影响）",
    )
    p.add_argument(
        "--layers",
        required=True,
        help="逗号分隔：frontend, backend, database 的子集，至少一项",
    )
    p.add_argument(
        "--source-ref",
        default="",
        help="文件路径、PR、commit 等可追溯信息",
    )
    p.add_argument(
        "--tags",
        default="",
        help="可选逗号分隔标签",
    )
    p.add_argument(
        "--embed-model",
        default="shaw/dmeta-embedding-zh",
        help="Ollama embedding 模型名",
    )
    p.add_argument(
        "--created-by-emp-code",
        default="",
        help="可选：录入人 business.employees.emp_code",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    raw_layers = [x.strip().lower() for x in args.layers.split(",") if x.strip()]
    layers = []
    for x in raw_layers:
        if x not in ALLOWED_LAYERS:
            print(f"非法 layer: {x}，允许: {sorted(ALLOWED_LAYERS)}", file=sys.stderr)
            sys.exit(1)
        layers.append(x)
    if not layers:
        print("至少指定一个 --layers", file=sys.stderr)
        sys.exit(1)

    tags = [t.strip() for t in args.tags.split(",") if t.strip()] or None

    resp = ollama.embeddings(model=args.embed_model, prompt=args.content)
    vec = resp.get("embedding")
    if not vec or not isinstance(vec, list):
        print("Ollama 返回无有效 embedding", file=sys.stderr)
        sys.exit(1)
    if len(vec) != EXPECTED_DIM:
        print(
            f"向量维度 {len(vec)} 与库表 vector({EXPECTED_DIM}) 不一致，请换模型或改表。",
            file=sys.stderr,
        )
        sys.exit(1)

    vec_literal = "[" + ",".join(str(float(v)) for v in vec) + "]"

    conn = psycopg2.connect(**CONN)
    cur = conn.cursor()
    emp_id = None
    if args.created_by_emp_code:
        cur.execute(
            "SELECT id FROM business.employees WHERE emp_code = %s LIMIT 1",
            (args.created_by_emp_code,),
        )
        row = cur.fetchone()
        if not row:
            print(f"未找到 emp_code={args.created_by_emp_code}", file=sys.stderr)
            sys.exit(1)
        emp_id = row[0]

    source_ref = args.source_ref.strip() or None

    cur.execute(
        """
        INSERT INTO business.design_memories (
            title, content, change_layers, source_ref, tags, embedding, created_by
        )
        VALUES (
            %s, %s, %s::text[], %s, %s::text[], %s::vector, %s
        )
        RETURNING id
        """,
        (
            args.title,
            args.content,
            layers,
            source_ref,
            tags,
            vec_literal,
            emp_id,
        ),
    )
    new_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    print(f"design_memories.id = {new_id}", file=sys.stderr)


if __name__ == "__main__":
    main()
