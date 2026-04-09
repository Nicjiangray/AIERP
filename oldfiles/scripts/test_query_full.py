"""
端到端：意图识别 → query_master_data workflow → data.main / data.sub
用法（项目根目录）：.venv/bin/python scripts/test_query_full.py
"""
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from engine.intent import detect_intent
from engine.workflow_runner import execute_workflow_for_chat

tests = [
    "查一下我们的客户列表",
    "看看产品有哪些",
    "查一下合同",
]

for text in tests:
    print(f"\n{'=' * 50}")
    print(f"输入：{text}")

    context = {
        "user_text": text,
        "user_id": 2,
    }

    intent = detect_intent(text)
    print(f"意图：{intent}")

    if intent:
        context["intent"] = intent
        result = execute_workflow_for_chat(intent, context)
        print(f"reply：{result.get('reply', '')[:100]}")
        main_data = result.get("data", {}).get("main", [])
        sub_data = result.get("data", {}).get("sub", [])
        print(f"data.main 行数：{len(main_data)}")
        print(f"data.sub 行数：{len(sub_data)}")
        if main_data:
            print(f"data.main 第一行：{json.dumps(main_data[0], ensure_ascii=False, default=str)}")
    else:
        print("未匹配意图")
