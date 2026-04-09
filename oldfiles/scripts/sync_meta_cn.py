#!/usr/bin/env python3
"""
为 meta.meta_fields 中 field_cn 为空的记录批量生成中文名（LLM）。

用法（在仓库根目录，且已配置 LLM 与 PG）：
  .venv/bin/python scripts/sync_meta_cn.py

前置：已执行 CALL meta.sync_constitution();
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from llm.client import chat_with_fallback
from tools.database import db_conn

BATCH_SIZE = 50
_FIELD_CN_MAX_LEN = 50

_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
# LLM 偶发在 JSON 键名里插入空白/tab，如 "table\t_name"、"field   _name"
_BROKEN_TABLE_NAME_KEY = re.compile(r'"table\s+_name"\s*:', re.MULTILINE)
_BROKEN_FIELD_NAME_KEY = re.compile(r'"field\s+_name"\s*:', re.MULTILINE)
# JSON 不允许字符串值外出现裸控制符；模型偶发在键名或逗号附近插入 \x00-\x1f
_BAD_JSON_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _sanitize_llm_json_array(raw: str, *, aggressive: bool = False) -> str:
    s = (raw or "").strip()
    s = _JSON_FENCE.sub("", s).strip()
    s = _BROKEN_TABLE_NAME_KEY.sub('"table_name":', s)
    s = _BROKEN_FIELD_NAME_KEY.sub('"field_name":', s)
    if aggressive:
        s = _BAD_JSON_CTRL.sub("", s)
    return s


def _parse_json_array(text: str) -> list:
    raw = _JSON_FENCE.sub("", (text or "").strip()).strip()
    raw = _sanitize_llm_json_array(raw, aggressive=False)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e1:
        raw2 = _sanitize_llm_json_array(raw, aggressive=True)
        try:
            data = json.loads(raw2)
        except json.JSONDecodeError as e2:
            raise e2 from e1
    if isinstance(data, dict) and "items" in data:
        data = data["items"]
    if not isinstance(data, list):
        raise ValueError("LLM 返回不是 JSON 数组")
    return data


def _fetch_batch(cur) -> list[tuple[str, str, str]]:
    cur.execute(
        """
        SELECT table_name, field_name, COALESCE(description, '') AS description
        FROM meta.meta_fields
        WHERE COALESCE(TRIM(field_cn), '') = ''
        ORDER BY table_name, field_name
        LIMIT %s
        """,
        (BATCH_SIZE,),
    )
    return [(r[0], r[1], r[2] or "") for r in cur.fetchall()]


def _build_prompt(rows: list[tuple[str, str, str]]) -> str:
    lines = [
        "给以下数据库字段生成简短中文名（2-4个字）",
        "只输出JSON数组，格式：",
        '[{"table_name":"xxx","field_name":"xxx","field_cn":"xxx"},...]',
        "",
        "字段清单：",
        "table_name | field_name | description",
    ]
    for table_name, field_name, desc in rows:
        safe_desc = desc.replace("\n", " ").strip()[:500]
        lines.append(f"{table_name} | {field_name} | {safe_desc}")
    return "\n".join(lines)


def main() -> int:
    total_updated = 0
    batch_no = 0

    while True:
        with db_conn() as conn:
            with conn.cursor() as cur:
                rows = _fetch_batch(cur)
        if not rows:
            break

        batch_no += 1
        user_content = _build_prompt(rows)
        messages = [
            {
                "role": "system",
                "content": (
                    "你只输出合法 JSON 数组，不要其它文字或 Markdown。"
                    "键名必须严格为 table_name、field_name、field_cn（三个英文标识符，中间不要空格、不要拆成 field _name）。"
                    "不要在 JSON 中插入未转义的控制字符。"
                ),
            },
            {"role": "user", "content": user_content},
        ]
        raw_text = ""
        try:
            raw_text = chat_with_fallback(messages)
            items = _parse_json_array(raw_text)
        except Exception as e:
            print(f"[sync_meta_cn] 第 {batch_no} 批 LLM/解析失败: {e}", file=sys.stderr)
            if raw_text:
                print(raw_text[:2000], file=sys.stderr)
            return 1

        by_key: dict[tuple[str, str], str] = {}
        for it in items:
            if not isinstance(it, dict):
                continue
            tn = (it.get("table_name") or "").strip()
            fn = (it.get("field_name") or "").strip()
            cn = (it.get("field_cn") or "").strip()
            if not tn or not fn:
                continue
            if not cn:
                cn = fn
            cn = cn[:_FIELD_CN_MAX_LEN]
            by_key[(tn, fn)] = cn

        batch_updated = 0
        with db_conn() as conn:
            with conn.cursor() as cur:
                for table_name, field_name, _ in rows:
                    key = (table_name, field_name)
                    cn = by_key.get(key)
                    if not cn:
                        cn = field_name[:_FIELD_CN_MAX_LEN]
                    cur.execute(
                        """
                        UPDATE meta.meta_fields
                        SET field_cn = %s
                        WHERE table_name = %s AND field_name = %s
                          AND COALESCE(TRIM(field_cn), '') = ''
                        """,
                        (cn, table_name, field_name),
                    )
                    batch_updated += cur.rowcount or 0
            conn.commit()
        total_updated += batch_updated
        print(f"[sync_meta_cn] 第 {batch_no} 批：更新 {batch_updated} 条 field_cn")

    print("==============================")
    print(f"meta_fields field_cn：自动生成 {total_updated} 条")
    print("==============================")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
