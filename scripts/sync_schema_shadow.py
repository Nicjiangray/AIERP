#!/usr/bin/env python3
"""
从 meta.meta_tables / meta.meta_fields 生成紧凑纯文本「影子表结构」，写入 meta.prompts（prompt_code=schema_shadow）。

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

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import psycopg2.extras

from tools.database import db_conn

# 审计类字段：各表不再逐行列出（与旧版一致，减少噪音）
EXCLUDE_FIELD_NAMES = frozenset(
    {"created_at", "updated_at", "deleted_at", "is_deleted", "created_by", "updated_by"}
)

# 公共字段：仅在文件头声明一次，各表字段列表中省略
COMMON_FIELDS = frozenset(
    {"id", "status", "notes", "created_at", "updated_at", "deleted_at"}
)

# 字段名已自明时不附加中文备注（第4条）
SELF_EXPLANATORY_NAMES = frozenset(
    {
        "id",
        "name",
        "status",
        "notes",
        "phone",
        "email",
        "address",
        "city",
        "province",
        "country",
        "district",
        "color",
        "unit",
        "quantity",
        "currency",
        "spec",
    }
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
        if "items" in raw and isinstance(raw["items"], list):
            return [x for x in raw["items"] if isinstance(x, dict)]
        return []
    return []


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
    return None


def _compact_fk(fk_val: str) -> str:
    """customers.id → →customers"""
    s = fk_val.strip()
    if s.endswith(".id"):
        return "→" + s[:-3]
    return "→" + s


def _should_omit_field_comment(field_name: str, has_fk_arrow: bool) -> bool:
    fn = field_name.lower()
    if fn in SELF_EXPLANATORY_NAMES:
        return True
    if fn.endswith("_id") and has_fk_arrow:
        return True
    if fn.endswith("_name"):
        return True
    if fn.endswith("_date"):
        return True
    return False


def _clip_relations_to_allowed(
    parent: Optional[str],
    children: List[str],
    fk_field: Optional[str],
    allowed: Set[str],
) -> Tuple[Optional[str], List[str], Optional[str]]:
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


def _build_compact_schema_text(
    table_rows: List[Dict[str, Any]],
    fields_by_table: Dict[str, List[Dict[str, Any]]],
) -> Tuple[str, int, int]:
    """返回 (纯文本, 表数, 列数统计（不含公共/审计省略）)。"""
    lines: List[str] = []
    lines.append("== 业务表结构 ==")
    lines.append("")
    lines.append(
        "公共字段（所有表默认包含，下方各表不再重复）："
        "id(int 主键), status(string), notes(string), created_at, updated_at, deleted_at"
    )
    lines.append("")

    field_count = 0
    n_tables = len(table_rows)

    for tr in table_rows:
        tname = tr["table_name"]
        related = _parse_related_tables(tr.get("related_tables"))
        nav_parent = tr.get("nav_parent_table")

        parent, children, fk_field = _derive_parent_child(
            tname, nav_parent, related, table_rows
        )
        allowed: Set[str] = {r["table_name"] for r in table_rows}
        parent, children, fk_field = _clip_relations_to_allowed(
            parent, children, fk_field, allowed
        )

        table_cn = (tr.get("table_cn") or "").strip()
        desc = (tr.get("description") or "").strip()
        title_cn = table_cn or desc or tname

        parts: List[str] = [f"{tname} {title_cn}"]
        if children:
            parts.append("children: " + ", ".join(children))
        if parent:
            ppart = f"parent: {parent}"
            if fk_field:
                ppart += f" via {fk_field}"
            parts.append(ppart)

        lines.append("---")
        lines.append(" | ".join(parts))

        for f in fields_by_table.get(tname, []):
            fn = f["field_name"]
            if fn in EXCLUDE_FIELD_NAMES or fn in COMMON_FIELDS:
                continue
            field_count += 1

            typ = _simplify_type(f.get("data_type"))
            req = "!" if bool(f.get("is_required")) else ""

            raw_fk = _field_fk_string(related, fn)
            fk_part = ""
            has_arrow = False
            if raw_fk is not None:
                fk_part = " " + _compact_fk(raw_fk)
                has_arrow = True

            cn = (f.get("field_cn") or "").strip()
            fdesc = (f.get("description") or "").strip()
            hint = cn or fdesc

            comment_part = ""
            if hint and not _should_omit_field_comment(fn, has_arrow):
                comment_part = " " + hint

            lines.append(f"  {fn}{req} {typ}{fk_part}{comment_part}")

    text = "\n".join(lines) + "\n"
    return text, n_tables, field_count


def main() -> None:
    with db_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            _ensure_include_in_domain_model_column(cur)
            _ensure_prompts_table(cur)

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

    payload, n_tables, field_count = _build_compact_schema_text(
        table_rows, fields_by_table
    )
    content_len = len(payload)
    token_est = max(1, content_len // 4)

    record = {
        "prompt_code": PROMPT_CODE,
        "category": "schema",
        "title": "数据库表结构（影子表·紧凑文本）",
        "summary": "领域表紧凑单行表示：表头含主从关系，字段含类型/必填/外键；公共字段仅在文首声明一次",
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
        f"同步完成（紧凑文本）：include_in_domain_model 共 {n_tables} 张表，"
        f"{field_count} 个字段（已排除公共字段与审计字段名），"
        f"content 长度 {content_len} 字符，约 {token_est} tokens（粗略按 4 字符/token）。"
    )


if __name__ == "__main__":
    main()
