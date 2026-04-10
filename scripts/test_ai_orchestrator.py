#!/usr/bin/env python3
"""
编排器与 JSON 提取的离线测试（不依赖 LLM）。

运行：python3 scripts/test_ai_orchestrator.py

环境变量 AIERP_BASE_URL 可选：若设置（如 http://127.0.0.1:8000）且服务已启动，
可对 /ai/chat 做一次冒烟（需有效 LLM + 数据库）。
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request

# 保证可 import 项目根
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from main import MAX_ORCHESTRATOR_ROUNDS, MAX_TOOL_ROUNDS, _extract_json_from_text  # noqa: E402


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def test_nested_json_raw_decode() -> None:
    nested = {
        "action": "write",
        "operation": "create",
        "table": "contracts",
        "data": {
            "lines": [{"sku": "x", "meta": {"a": 1}}],
            "note": "brace { in string ok",
        },
    }
    raw = f"说明文字\n```json\n{json.dumps(nested, ensure_ascii=False)}\n```\n尾部"
    got = _extract_json_from_text(raw)
    _assert(got == nested, f"code fence: {got}")

    noisy = (
        'prefix {"action": "query", "table": "t", "filters": [{"field": "f", "op": "eq", "value": "v"}]} suffix'
    )
    got2 = _extract_json_from_text(noisy)
    _assert(got2 and got2.get("action") == "query", f"inline: {got2}")


def test_max_tool_rounds_constant() -> None:
    _assert(MAX_TOOL_ROUNDS == MAX_ORCHESTRATOR_ROUNDS, "MAX_TOOL_ROUNDS 应与 MAX_ORCHESTRATOR_ROUNDS 一致")
    _assert(MAX_ORCHESTRATOR_ROUNDS == 30, "MAX_ORCHESTRATOR_ROUNDS 应为 30")
    # 与 main 中外层 orchestrator 循环一致
    seen = 0
    for _ in range(MAX_ORCHESTRATOR_ROUNDS):
        seen += 1
    _assert(seen == 30, "循环上限")


def smoke_http_if_configured() -> None:
    base = (os.environ.get("AIERP_BASE_URL") or "").strip().rstrip("/")
    if not base:
        print("跳过 HTTP 冒烟：未设置 AIERP_BASE_URL")
        return
    body = json.dumps(
        {
            "messages": [{"role": "user", "content": "ping"}],
            "stream": False,
            "user_id": None,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/ai/chat",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    _assert("reply" in data, f"响应缺 reply: {data.keys()}")
    print("HTTP smoke OK:", base)


def main() -> None:
    test_nested_json_raw_decode()
    test_max_tool_rounds_constant()
    print("离线测试通过：_extract_json_from_text（嵌套） + MAX_ORCHESTRATOR_ROUNDS")
    smoke_http_if_configured()


if __name__ == "__main__":
    main()
