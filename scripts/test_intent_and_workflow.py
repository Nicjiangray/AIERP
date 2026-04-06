"""
端到端测试：意图识别 → workflow 执行
用法：python scripts/test_intent_and_workflow.py
"""
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from engine.intent import detect_intent
from engine.workflow_runner import execute_workflow_for_chat

# ── 测试 1：意图识别 ──────────────────────────────────────────────
print("=" * 50)
print("测试 1：意图识别")
print("=" * 50)

test_cases = [
    ("我要创建一份合同", "create_contract"),
    ("帮我新增一个合同", "create_contract"),
    ("今天天气怎么样", None),
    ("查一下库存", None),
]

for text, expected in test_cases:
    result = detect_intent(text)
    status = "✅" if result == expected else "❌"
    print(f"{status} 输入: {text!r} → 识别: {result!r} （期望: {expected!r}）")

# ── 测试 2：workflow 执行（模拟业务员身份）────────────────────────
print()
print("=" * 50)
print("测试 2：create_contract workflow 执行")
print("=" * 50)

context = {
    "user_id": 1,          # 根据你的 employees 表调整
    "role": "salesperson", # 业务员身份
    "user_input": "我要创建一份合同",
    # 如果 workflow 步骤需要产品/供应商 ID，在这里补充：
    # "product_id": 1,
    # "supplier_id": 1,
}

result = execute_workflow_for_chat("create_contract", context)

print("返回内容：")
print(result["content"])
print()
print("完整 workflow_context：")
ctx = result.get("workflow_context", {})
print(json.dumps(ctx, ensure_ascii=False, default=str, indent=2))
