#!/usr/bin/env python3
"""One-off: fill meta.meta_fields.menu_label_cn / menu_label_en from field_cn, description, field_name."""

import re
import sys

import psycopg2
from psycopg2.extras import RealDictCursor

CONN = dict(dbname="aierp", user="nicjiang", host="localhost", port=5432)

# 常见技术字段：无中文 field_cn、description 也难提取时用
COMMON_CN = {
    "id": "主键",
    "created_at": "创建时间",
    "updated_at": "更新时间",
    "notes": "备注",
    "embedding": "向量",
    "status": "状态",
    "title": "标题",
    "name": "名称",
    "product_name": "产品名称",
    "email": "邮箱",
    "phone": "电话",
    "address": "地址",
    "currency": "币种",
    "description": "描述",
    "category": "分类",
    "content": "内容",
    "type": "类型",
    "code": "编号",
}

COMMON_EN = {
    "id": "ID",
    "created_at": "Created At",
    "updated_at": "Updated At",
}


def has_cjk(s: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", s or ""))


def first_cjk_phrase(desc: str, max_len: int = 32) -> str | None:
    if not desc:
        return None
    seg = desc.strip()
    for sep in ("，", ",", "。", "；", ";"):
        if sep in seg:
            seg = seg.split(sep, 1)[0].strip()
            break
    if not has_cjk(seg):
        return None
    return seg[:max_len]


def humanize_en(field_name: str) -> str:
    fn = (field_name or "").strip()
    if fn in COMMON_EN:
        return COMMON_EN[fn]
    parts = [p for p in fn.split("_") if p]
    if not parts:
        return fn
    return " ".join(w[:1].upper() + w[1:].lower() if w else "" for w in parts)


def menu_cn(field_cn: str, field_name: str, description: str | None) -> str:
    fc = (field_cn or "").strip()
    desc = (description or "").strip()

    if has_cjk(fc):
        return fc

    phrase = first_cjk_phrase(desc)
    if phrase:
        return phrase

    if field_name in COMMON_CN:
        return COMMON_CN[field_name]

    # 仍无中文：用 field_cn 或 field_name 兜底（避免空）
    if fc:
        return fc
    return field_name or ""


def menu_en(field_name: str) -> str:
    fn = (field_name or "").strip()
    return humanize_en(fn)


def main() -> None:
    conn = psycopg2.connect(**CONN, cursor_factory=RealDictCursor)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, field_name, field_cn, description FROM meta.meta_fields ORDER BY id"
    )
    rows = cur.fetchall()
    for r in rows:
        cn = menu_cn(r["field_cn"], r["field_name"], r["description"])
        en = menu_en(r["field_name"])
        cur.execute(
            "UPDATE meta.meta_fields SET menu_label_cn = %s, menu_label_en = %s WHERE id = %s",
            (cn, en, r["id"]),
        )
    conn.commit()
    cur.close()
    conn.close()
    print(f"updated {len(rows)} rows", file=sys.stderr)


if __name__ == "__main__":
    main()
