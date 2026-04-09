#!/usr/bin/env python3
"""
打印三层意图识别轨迹（用于验证）。
项目根：.venv/bin/python scripts/test_intent_layers.py
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from engine.intent import (
    _get_embedding,
    _layer1_substring_match,
    _layer2_embedding_scores,
    _load_workflows,
    detect_intent,
)

TESTS = [
    ("查一下客户列表", "第一层或第二层"),
    ("看看我们有哪些买家", "第二层语义"),
    ("有没有全棉的产品", "第二层语义"),
    ("我要签一份合同", "第一层或第二层"),
    ("今天天气怎么样", "未匹配"),
]


def trace(text: str) -> None:
    print(f"\n输入：{text!r}")
    wfs = _load_workflows()
    l1 = _layer1_substring_match(text, wfs)
    if l1:
        print(f"  层级：第一层（子串）→ intent={l1!r}")
        print(f"  detect_intent 最终：{detect_intent(text)!r}")
        return
    uv = _get_embedding(text)
    if uv is None:
        print("  层级：向量失败 → 第三层（全量 LLM）")
        print(f"  detect_intent 最终：{detect_intent(text)!r}")
        return
    scores = _layer2_embedding_scores(text, wfs, uv)
    if not scores:
        print("  层级：无分数")
        print(f"  detect_intent 最终：{detect_intent(text)!r}")
        return
    best_i, best_s = scores[0]
    print(f"  第二层分数：{scores[:5]}...")
    if best_s > 0.85:
        print(f"  层级：第二层（高相似）→ {best_i!r} sim={best_s:.3f}")
    elif best_s >= 0.60:
        print(f"  层级：第二层中档 → 进第三层，最高分 {best_i!r} sim={best_s:.3f}")
    else:
        print(f"  层级：第二层过低 sim={best_s:.3f} → 不匹配（不调 LLM）")
    print(f"  detect_intent 最终：{detect_intent(text)!r}")


def main() -> None:
    for text, note in TESTS:
        print("=" * 60)
        print(f"说明（预期）：{note}")
        trace(text)


if __name__ == "__main__":
    main()
