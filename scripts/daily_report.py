"""
每日测试摘要：汇总 ai_incidents 和 ai_feedback，输出文本报告。
用法：python scripts/daily_report.py [--days 1]
"""

from __future__ import annotations

import argparse

from tools.database import db_conn


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=1, help="回溯天数")
    args = parser.parse_args()
    days = max(1, args.days)

    print(f"\n{'=' * 60}")
    print(f"  aierp 测试日报（最近 {days} 天）")
    print(f"{'=' * 60}\n")

    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(DISTINCT session_id) FROM business.ai_chat
                WHERE created_at >= NOW() - (%s::integer * INTERVAL '1 day')
                """,
                (days,),
            )
            total = cur.fetchone()[0]
            print(f"总对话会话数：{total}\n")

            cur.execute(
                """
                SELECT stage, COUNT(*) AS cnt
                FROM business.ai_incidents
                WHERE created_at >= NOW() - (%s::integer * INTERVAL '1 day')
                GROUP BY stage ORDER BY cnt DESC
                """,
                (days,),
            )
            incidents = cur.fetchall()

            print("── 系统错误 ──")
            if not incidents:
                print("  无错误记录 ✓")
            for stage, cnt in incidents:
                print(f"  {stage}: {cnt} 次")
                cur.execute(
                    """
                    SELECT LEFT(error_message, 150), LEFT(generated_sql, 100), created_at::text
                    FROM business.ai_incidents
                    WHERE stage = %s AND created_at >= NOW() - (%s::integer * INTERVAL '1 day')
                    ORDER BY created_at DESC LIMIT 3
                    """,
                    (stage, days),
                )
                for err, sql, ts in cur.fetchall():
                    print(f"    [{ts[:16]}] {err}")
                    if sql:
                        print(f"      SQL: {sql}")

            print()

            cur.execute(
                """
                SELECT feedback_type, COUNT(*) AS cnt
                FROM business.ai_feedback
                WHERE created_at >= NOW() - (%s::integer * INTERVAL '1 day')
                GROUP BY feedback_type ORDER BY cnt DESC
                """,
                (days,),
            )
            feedbacks = cur.fetchall()

            print("── 用户反馈 ──")
            if not feedbacks:
                print("  无反馈 ✓")
            for ftype, cnt in feedbacks:
                print(f"  {ftype}: {cnt} 条")
                cur.execute(
                    """
                    SELECT e.name, LEFT(f.content, 150), f.created_at::text
                    FROM business.ai_feedback f
                    LEFT JOIN business.employees e ON e.id = f.user_id
                    WHERE f.feedback_type = %s
                      AND f.created_at >= NOW() - (%s::integer * INTERVAL '1 day')
                    ORDER BY f.created_at DESC LIMIT 5
                    """,
                    (ftype, days),
                )
                for name, content, ts in cur.fetchall():
                    print(f"    [{ts[:16]}] {name or '未知'}: {content}")

    print(f"\n{'=' * 60}\n")


if __name__ == "__main__":
    main()
