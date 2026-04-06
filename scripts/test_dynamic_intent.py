#!/usr/bin/env python3
"""
动态意图识别冒烟：打印第三层会收到的表目录片段，并对若干中文句调用 detect_intent。
需本地 PostgreSQL + LLM；向量层可跳过（看日志）。
"""
from __future__ import annotations

import os
import sys

# 项目根
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.context_builder import build_table_directory  # noqa: E402
from engine.intent import detect_intent  # noqa: E402


CASES = [
    "查一下上海的客户有哪些",
    "帮我新建一个客户，名字叫测试公司",
    "把产品表里编号 P001 的单价改成 99",
    "联系人里姓张的有哪些",
    "你能做什么",
    "今天天气怎么样",
]


def main() -> None:
    snippet = build_table_directory()
    print("=== build_table_directory 前 1200 字符 ===\n")
    print(snippet[:1200] + ("…" if len(snippet) > 1200 else ""))
    print("\n=== detect_intent（含第三层时写入 sidecar）===\n")
    for text in CASES:
        side: dict = {}
        intent = detect_intent(text, use_expert=False, context=side)
        print(f"用户: {text!r}")
        print(f"  intent={intent!r}  sidecar={side!r}\n")
        if intent and not side.get("target_table"):
            raise SystemExit(f"加速层/侧车应有 target_table：{text!r} -> {side!r}")
        if not intent and side.get("target_table"):
            raise SystemExit(f"intent 为 None 时不应写 target_table：{text!r} -> {side!r}")


if __name__ == "__main__":
    main()
