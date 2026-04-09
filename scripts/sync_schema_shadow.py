#!/usr/bin/env python3
"""
从 meta.meta_tables / meta.meta_fields 生成影子表结构 JSON，写入 meta.prompts（prompt_code=schema_shadow）。

范围（重要）：
    只包含 meta.meta_tables.include_in_domain_model = true 的表（LLM 领域模型开关）。
    新建表由 `CALL meta.sync_constitution()` 按 schema 预填：business 默认 true，meta 默认 false；
    可在库内改某行为 false（如会话/帧表）而无需维护排除列表。
    须先执行：constitution/add_meta_tables_include_in_domain_model.sql

用法（项目根目录）：
    python scripts/sync_schema_shadow.py

一键三步（constitution → sync_meta_cn → 本脚本）：见 scripts/sync_meta_pipeline.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import psycopg2.extras

from tools.database import db_conn

EXCLUDE_FIELD_NAMES = frozenset(
    {"created_at", "updated_at", "deleted_at", "is_deleted", "created_by", "updated_by"}
)

PROMPT_CODE = "schema_shadow"


def _simplify_type(pg_type: Optional[str]) -> str:
    if not pg_type:
        return "unknown"
    t = pg_type.strip().lower()
    if "timestamp" in t:
        return "datetime"
    if t in ("date",):
        return "date"
    if t in ("integer", "bigint", "smallint", "serial", "bigserial", "int"):
        return "int"
    if t in ("boolean", "bool"):
        return "bool"
    if "character varying" in t or t in ("text", "character", "varchar", "string"):
        return "string"
    if t in ("numeric", "decimal", "real", "double precision", "float", "number"):
        return "number"
    if t in ("json", "jsonb"):
        return "json"
    if t == "uuid":
        return "uuid"
    if t == "array" or t.endswith("[]"):
        return "array"
    return t[:64]


def _parse_related_tables(raw: Any) -> List[Dict[str, Any]]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    if isinstance(raw, dict):
        if not raw:
            return []
        # 兼容误存为对象的情况
        if "items" in raw and isinstance(raw["items"], list):
            return [x for x in raw["items"] if isinstance(x, dict)]
        return []
    return []


def _load_table_schema_map(cur) -> Dict[str, str]:
    """table_name -> 实际 PostgreSQL schema（business / meta 等）。"""
    cur.execute(
        """
        SELECT table_schema, table_name
        FROM information_schema.tables
        WHERE table_type = 'BASE TABLE'
          AND table_schema NOT IN ('pg_catalog', 'information_schema')
        """
    )
    out: Dict[str, str] = {}
    for row in cur.fetchall():
        schema, name = row["table_schema"], row["table_name"]
        if name in out:
            # 同名表优先保留 business
            if out[name] != "business" and schema == "business":
                out[name] = schema
        else:
            out[name] = schema
    return out


def _field_fk_string(
    related: List[Dict[str, Any]], field_name: str
) -> Optional[str]:
    for rel in related:
        if rel.get("fk") != field_name:
            continue
        typ = (rel.get("type") or "").lower()
        ref_table = rel.get("table")
        if typ == "belongs_to" and ref_table:
            return f"{ref_table}.id"
        if typ == "soft_reference":
            note = rel.get("note")
            if isinstance(note, str) and note.strip():
                return note.strip()
            if ref_table:
                return str(ref_table)
        # has_many 的 fk 通常指子表上的列名，不挂在父表字段上；跳过
    return None


def _clip_relations_to_allowed(
    parent: Optional[str],
    children: List[str],
    fk_field: Optional[str],
    allowed: Set[str],
) -> Tuple[Optional[str], List[str], Optional[str]]:
    """仅保留指向/来自「当前导出集合」内的父子关系，避免指向未纳入领域模型的表。"""
    ch = [c for c in children if c in allowed]
    pa = parent if (parent and parent in allowed) else None
    fk = fk_field if pa else None
    return pa, ch, fk


def _derive_parent_child(
    table_name: str,
    nav_parent: Optional[str],
    related: List[Dict[str, Any]],
    all_rows: Sequence[Dict[str, Any]],
) -> Tuple[Optional[str], List[str], Optional[str]]:
    """
    返回 (parent_table_name | None, children_table_names, fk_field_to_parent | None)。
    """
    children: List[str] = []
    for rel in related:
        if (rel.get("type") or "").lower() == "has_many" and rel.get("table"):
            children.append(rel["table"])

    for r in all_rows:
        if r.get("nav_parent_table") == table_name:
            tn = r.get("table_name")
            if tn and tn not in children:
                children.append(tn)

    parent: Optional[str] = None
    fk_field: Optional[str] = None

    if nav_parent:
        parent = nav_parent
        for rel in related:
            if (rel.get("type") or "").lower() != "belongs_to":
                continue
            if rel.get("table") == nav_parent:
                fk_field = rel.get("fk")
                break
    else:
        for rel in related:
            if (rel.get("type") or "").lower() == "belongs_to" and rel.get("table"):
                parent = rel.get("table")
                fk_field = rel.get("fk")
                break

    return parent, sorted(set(children)), fk_field


def _ensure_include_in_domain_model_column(cur) -> None:
    cur.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'meta'
              AND table_name = 'meta_tables'
              AND column_name = 'include_in_domain_model'
        ) AS ok
        """
    )
    row = cur.fetchone()
    if not row or not row["ok"]:
        raise SystemExit(
            "meta.meta_tables 缺少 include_in_domain_model 列，请先执行："
            "psql -d aierp -f constitution/add_meta_tables_include_in_domain_model.sql"
        )


def _ensure_prompts_table(cur) -> None:
    cur.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = 'meta' AND table_name = 'prompts'
        ) AS ok
        """
    )
    row = cur.fetchone()
    if not row or not row["ok"]:
        raise SystemExit(
            "meta.prompts 表不存在，请先执行：psql -d aierp -f constitution/create_meta_prompts.sql"
        )


def main() -> None:
    with db_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            _ensure_include_in_domain_model_column(cur)
            _ensure_prompts_table(cur)

            schema_by_table = _load_table_schema_map(cur)

            cur.execute(
                """
                SELECT id, table_name, table_cn, description, nav_parent_table, related_tables
                FROM meta.meta_tables
                WHERE deleted_at IS NULL
                  AND include_in_domain_model = true
                ORDER BY table_name
                """
            )
            table_rows: List[Dict[str, Any]] = [dict(r) for r in cur.fetchall()]

            cur.execute(
                """
                SELECT table_name, field_name, field_cn, data_type, is_required, description
                FROM meta.meta_fields
                WHERE deleted_at IS NULL
                ORDER BY table_name, id
                """
            )
            field_rows = [dict(r) for r in cur.fetchall()]

    allowed: Set[str] = {r["table_name"] for r in table_rows}

    fields_by_table: Dict[str, List[Dict[str, Any]]] = {}
    for fr in field_rows:
        tn = fr["table_name"]
        if tn not in allowed:
            continue
        fields_by_table.setdefault(tn, []).append(fr)

    output: List[Dict[str, Any]] = []
    field_count = 0

    for tr in table_rows:
        tname = tr["table_name"]
        related = _parse_related_tables(tr.get("related_tables"))
        nav_parent = tr.get("nav_parent_table")

        parent, children, fk_field = _derive_parent_child(
            tname, nav_parent, related, table_rows
        )
        parent, children, fk_field = _clip_relations_to_allowed(
            parent, children, fk_field, allowed
        )

        comment = (tr.get("description") or "").strip() or (
            (tr.get("table_cn") or "").strip() or tname
        )

        fields_out: List[Dict[str, Any]] = []
        for f in fields_by_table.get(tname, []):
            fn = f["field_name"]
            if fn in EXCLUDE_FIELD_NAMES:
                continue
            field_count += 1
            cn = (f.get("field_cn") or "").strip()
            desc = (f.get("description") or "").strip()
            fcomment = cn or desc or fn
            fd: Dict[str, Any] = {
                "name": fn,
                "type": _simplify_type(f.get("data_type")),
                "comment": fcomment,
            }
            if bool(f.get("is_required")):
                fd["required"] = True
            fk_val = _field_fk_string(related, fn)
            if fk_val is not None:
                fd["fk"] = fk_val
            fields_out.append(fd)

        row: Dict[str, Any] = {
            "table": tname,
            "schema": schema_by_table.get(tname, "unknown"),
            "comment": comment,
            "fields": fields_out,
        }
        if children:
            row["children"] = children
        if parent is not None:
            row["parent"] = parent
        if fk_field is not None:
            row["fk_field"] = fk_field
        output.append(row)

    payload = json.dumps(output, ensure_ascii=False, indent=2)
    n_tables = len(output)
    content_len = len(payload)
    token_est = max(1, content_len // 4)

    record = {
        "prompt_code": PROMPT_CODE,
        "category": "schema",
        "title": "数据库表结构（影子表）",
        "summary": "所有业务表的结构定义，包含表名、字段、类型、注释、外键关系",
        "sort_order": 2,
        "inject_mode": "always",
        "source": "db_sync",
        "llm_editable": False,
        "is_active": True,
        "updated_by": "system",
        "content": payload,
    }

    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO meta.prompts (
                    prompt_code, category, title, summary, content,
                    sort_order, inject_mode, source, llm_editable, is_active,
                    version, updated_by
                ) VALUES (
                    %(prompt_code)s, %(category)s, %(title)s, %(summary)s, %(content)s,
                    %(sort_order)s, %(inject_mode)s, %(source)s, %(llm_editable)s, %(is_active)s,
                    1, %(updated_by)s
                )
                ON CONFLICT (prompt_code) DO UPDATE SET
                    category = EXCLUDED.category,
                    title = EXCLUDED.title,
                    summary = EXCLUDED.summary,
                    content = EXCLUDED.content,
                    sort_order = EXCLUDED.sort_order,
                    inject_mode = EXCLUDED.inject_mode,
                    source = EXCLUDED.source,
                    llm_editable = EXCLUDED.llm_editable,
                    is_active = EXCLUDED.is_active,
                    version = meta.prompts.version + 1,
                    updated_at = NOW(),
                    updated_by = EXCLUDED.updated_by
                """,
                record,
            )
        conn.commit()

    print(
        f"同步完成：include_in_domain_model 共 {n_tables} 张表，{field_count} 个字段（已排除审计字段名），"
        f"content 长度 {content_len} 字符，约 {token_est} tokens（粗略按 4 字符/token）。"
    )


if __name__ == "__main__":
    main()
