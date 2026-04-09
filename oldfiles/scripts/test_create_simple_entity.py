#!/usr/bin/env python3
"""
create_simple_entity / is_business_message / detect_intent 联调脚本（真实数据库与 LLM）。

项目根目录执行：
  .venv/bin/python scripts/test_create_simple_entity.py
"""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import psycopg2.extras

from engine.collecting import run_collecting
from engine.intent import detect_intent, is_business_message
from tools.database import db_conn

test_user_id = 2
test_session_id = 1


def _sep(title: str) -> None:
    print()
    print("=" * 72)
    print(f"  {title}")
    print("=" * 72)


def ensure_session(session_id: int, user_id: int) -> None:
    """保证存在指定 id 的 session（测试用）。"""
    with db_conn() as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM business.sessions WHERE id = %s",
                (session_id,),
            )
            if cur.fetchone():
                cur.execute(
                    """
                    UPDATE business.sessions
                    SET user_id = %s, last_active_at = now(), is_active = true
                    WHERE id = %s
                    """,
                    (user_id, session_id),
                )
                print(f"[prep] 已更新 session id={session_id} user_id={user_id}")
                return
            cur.execute(
                """
                INSERT INTO business.sessions (id, user_id)
                VALUES (%s, %s)
                """,
                (session_id, user_id),
            )
            print(f"[prep] 已插入 session id={session_id} user_id={user_id}")


def slot_counts_for_frame(frame_id: Optional[int]) -> tuple[int, int, int]:
    """返回 (total, pending, supplied)；frame_id 无效时 (0,0,0)。"""
    if frame_id is None:
        return 0, 0, 0
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    COUNT(*)::int,
                    COUNT(*) FILTER (WHERE status = 'pending')::int,
                    COUNT(*) FILTER (WHERE status = 'supplied')::int
                FROM business.slots
                WHERE frame_id = %s
                """,
                (int(frame_id),),
            )
            row = cur.fetchone()
            if not row:
                return 0, 0, 0
            return int(row[0]), int(row[1]), int(row[2])


def print_run_result(label: str, result: dict) -> None:
    fid = result.get("frame_id")
    total, pending, supplied = slot_counts_for_frame(fid)
    print(f"\n--- {label} ---")
    print(f"status:     {result.get('status')!r}")
    print(f"reply:      {result.get('reply')!r}")
    print(f"frame_id:   {fid!r}")
    print(
        f"slots:      total={total}, pending={pending}, supplied={supplied}"
    )


def scenario_1() -> None:
    _sep("场景一：全新开始，用户提供了部分信息")
    ctx = {
        "user_id": test_user_id,
        "session_id": test_session_id,
        "matched_intent": "create_simple_entity",
        "user_input": "我要新建一个客户，公司名是测试科技有限公司",
    }
    try:
        result = run_collecting(ctx)
        print_run_result("run_collecting 结果", result)
        print("\n预期: status='collecting'，reply 追问下一必填字段")
    except Exception as e:
        print(f"场景一异常: {e}")
        traceback.print_exc()


def scenario_2() -> None:
    _sep("场景二：继续补充信息（第二轮输入）")
    ctx = {
        "user_id": test_user_id,
        "session_id": test_session_id,
        "matched_intent": "create_simple_entity",
        "user_input": "客户编号是 TEST-001，付款方式是月结30天",
    }
    try:
        result = run_collecting(ctx)
        print_run_result("run_collecting 结果", result)
        print("\n预期: 复用场景一 frame，继续填充 slots")
    except Exception as e:
        print(f"场景二异常: {e}")
        traceback.print_exc()


def scenario_3_collecting_final() -> None:
    _sep("场景三：补全最后两个字段，触发写库")
    ctx = {
        "user_id": test_user_id,
        "session_id": test_session_id,
        "matched_intent": "create_simple_entity",
        "user_input": "开票类型是增值税专用发票，税号是 91110000123456789X",
    }
    try:
        result = run_collecting(ctx)
        fid = result.get("frame_id")
        print(f"\n--- run_collecting 结果 ---")
        print(f"status:   {result.get('status')!r}")
        print(f"reply:    {result.get('reply')!r}")
        print(f"frame_id: {fid!r}")
    except Exception as e:
        print(f"场景三（写库）异常: {e}")
        traceback.print_exc()
        return

    _sep("验证：frame 状态（等价于 SELECT id, subject, status FROM business.frames WHERE id = <本轮 frame_id>）")
    if fid is None:
        print("  (无 frame_id，跳过)")
    else:
        try:
            with db_conn() as conn:
                with conn.cursor(
                    cursor_factory=psycopg2.extras.RealDictCursor
                ) as cur:
                    cur.execute(
                        """
                        SELECT id, subject, status
                        FROM business.frames
                        WHERE id = %s
                        """,
                        (int(fid),),
                    )
                    row = cur.fetchone()
            if row:
                print(
                    f"  id={row['id']}, subject={row['subject']!r}, status={row['status']!r}"
                )
            else:
                print(f"  (未找到 frame id={fid!r})")
        except Exception as e:
            print(f"  查询 frames 异常: {e}")
            traceback.print_exc()

    _sep("验证：customers 最近 3 条")
    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT id, name, customer_code, payment_terms, invoice_type, tax_id
                    FROM business.customers
                    ORDER BY id DESC
                    LIMIT 3
                    """
                )
                rows = cur.fetchall()
        if not rows:
            print("  (无数据)")
        else:
            cols = list(rows[0].keys())
            print("  " + " | ".join(cols))
            print("  " + "-" * 72)
            for r in rows:
                print("  " + " | ".join(str(r[c]) for c in cols))
    except Exception as e:
        print(f"  查询 customers 异常: {e}")
        traceback.print_exc()


def scenario_4() -> None:
    _sep("场景四：is_business_message 过滤测试")
    cases = [
        ("你好", False),
        ("谢谢", False),
        ("我要新建客户", True),
        ("帮我查一下产品", True),
        ("今天天气不错", False),
        ("hi", False),
    ]
    for text, expected in cases:
        got = is_business_message(text)
        mark = "✓" if got == expected else "✗(与注释预期不一致)"
        print(f"  {mark}  {text!r} -> {got}  (注释预期 {expected})")


def scenario_5() -> None:
    _sep("场景五：detect_intent 识别测试（依赖 LLM / Ollama / 向量，可能较慢）")
    cases = [
        "帮我新建一个客户",
        "添加一条产品记录",
        "查一下华润的客户信息",
    ]
    for text in cases:
        try:
            intent = detect_intent(text, use_expert=False)
            print(f"  {text!r} -> {intent!r}")
        except Exception as e:
            print(f"  {text!r} -> 异常: {e}")
            traceback.print_exc()


def print_db_summary() -> None:
    _sep("数据库验证：最近 5 条 frames + slots 汇总（user_id=2）")
    sql = """
SELECT f.id, f.subject, f.status, f.workflow_intent,
       COUNT(s.id) as total_slots,
       COALESCE(SUM(CASE WHEN s.status='pending' THEN 1 ELSE 0 END), 0) as pending,
       COALESCE(SUM(CASE WHEN s.status='supplied' THEN 1 ELSE 0 END), 0) as supplied
FROM business.frames f
LEFT JOIN business.slots s ON s.frame_id = f.id
WHERE f.user_id = 2
GROUP BY f.id, f.subject, f.status, f.workflow_intent, f.created_at
ORDER BY f.created_at DESC
LIMIT 5
"""
    try:
        with db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql)
                rows = cur.fetchall()
        if not rows:
            print("  (无数据)")
            return
        cols = list(rows[0].keys())
        print("  " + " | ".join(cols))
        print("  " + "-" * 68)
        for r in rows:
            print("  " + " | ".join(str(r[c]) for c in cols))
    except Exception as e:
        print(f"查询异常: {e}")
        traceback.print_exc()


def main() -> None:
    os.chdir(_ROOT)
    print("前置：确保 session 存在")
    try:
        ensure_session(test_session_id, test_user_id)
    except Exception as e:
        print(f"ensure_session 异常: {e}")
        traceback.print_exc()

    scenario_1()
    scenario_2()
    scenario_3_collecting_final()
    scenario_4()
    scenario_5()
    print_db_summary()
    print()
    print("（测试完成，未清理数据，可人工查库）")


if __name__ == "__main__":
    main()
