"""
从 meta + workflows 构建意图识别 / collecting 用的纯文本上下文（同步，db_conn）。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Set

import psycopg2.extras

from tools.database import db_conn

logger = logging.getLogger(__name__)


def _workflows_rows() -> List[Dict[str, Any]]:
    with db_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT intent, display_name, description,
                       operation_type, target_tables
                FROM business.workflows
                WHERE COALESCE(is_active, 1) = 1
                ORDER BY intent
                """
            )
            return [dict(r) for r in cur.fetchall()]


def build_table_directory(workflows_subset: Optional[List[Dict[str, Any]]] = None) -> str:
    """
    表级目录 + 可用 workflow（operation_type / target_tables），供意图识别阶段一。
    """
    with db_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT m.table_name, m.table_cn, m.description, m.entity_type
                FROM meta.meta_tables m
                WHERE EXISTS (
                    SELECT 1
                    FROM information_schema.tables t
                    WHERE t.table_schema = 'business'
                      AND t.table_name = m.table_name
                      AND t.table_type = 'BASE TABLE'
                )
                ORDER BY m.table_name
                """
            )
            tables = cur.fetchall()

    parts: List[str] = ["## 系统中的业务表（business schema，元数据来自 meta.meta_tables）"]
    for t in tables:
        tn = (t.get("table_name") or "").strip()
        if not tn:
            continue
        tcn = (t.get("table_cn") or "").strip() or tn
        desc = (t.get("description") or "").strip()
        et = t.get("entity_type")
        extra = f"，entity_type={et}" if et else ""
        parts.append(f"- {tcn}（{tn}）{extra}：{desc}")

    wf_list = workflows_subset if workflows_subset is not None else _workflows_rows()
    parts.append("\n## 可用业务流程（intent）")
    for w in wf_list:
        ik = (w.get("intent") or "").strip()
        dn = (w.get("display_name") or "").strip()
        op = (w.get("operation_type") or "").strip() or "未分类"
        tt = w.get("target_tables")
        if isinstance(tt, list):
            targets = ", ".join(str(x) for x in tt if x)
        elif tt is None:
            targets = ""
        else:
            targets = str(tt)
        if not targets:
            targets = "（见元数据表目录）"
        des = (w.get("description") or "").strip()
        parts.append(
            f"- **{ik}**（{dn}）\n"
            f"  说明：{des}\n"
            f"  操作类型：{op}\n"
            f"  常见目标表：{targets}"
        )

    return "\n".join(parts)


def build_table_detail(table_name: str) -> str:
    """
    单表字段级详情，供 collecting 在已有 target_table 时注入 system prompt。
    """
    tn = (table_name or "").strip()
    if not tn:
        return ""

    with db_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT table_cn, description
                FROM meta.meta_tables
                WHERE table_name = %s
                """,
                (tn,),
            )
            ti = cur.fetchone()
            cur.execute(
                """
                SELECT field_name, field_cn, data_type,
                       COALESCE(is_required, false) AS is_required,
                       allowed_values
                FROM meta.meta_fields
                WHERE table_name = %s
                ORDER BY id
                """,
                (tn,),
            )
            fields = cur.fetchall()

    if not ti:
        return f"## 表 {tn}\n（未在 meta.meta_tables 中找到）\n"

    tcn = (ti.get("table_cn") or "").strip() or tn
    tdesc = (ti.get("description") or "").strip()
    parts = [f"## {tcn}（{tn}）\n{tdesc}\n", "### 字段清单"]

    for f in fields:
        fn = (f.get("field_name") or "").strip()
        if not fn:
            continue
        fcn = (f.get("field_cn") or "").strip() or fn
        dt = (f.get("data_type") or "").strip()
        req = f.get("is_required")
        av = f.get("allowed_values")
        line = f"- {fcn}（`{fn}`）：类型 {dt}"
        if req:
            line += "，必填"
        if av is not None and str(av).strip():
            try:
                if isinstance(av, (dict, list)):
                    line += f"，可选值：{json.dumps(av, ensure_ascii=False)}"
                else:
                    line += f"，可选值：{av}"
            except Exception:
                line += f"，可选值：{av!r}"
        parts.append(line)

    return "\n".join(parts)
