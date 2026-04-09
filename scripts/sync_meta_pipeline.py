#!/usr/bin/env python3
"""
元数据 → 中文名 → 领域模型提示词 一键流水线

顺序（与数据依赖一致）：
  1. CALL meta.sync_constitution() — 从 information_schema 重建 meta_tables / meta_fields
  2. sync_meta_cn — 为空 field_cn 批量填中文（LLM，可多批）
  3. sync_schema_shadow — 按 include_in_domain_model 裁剪后写入 meta.prompts（schema_shadow）

用法（仓库根目录）：
  .venv/bin/python scripts/sync_meta_pipeline.py
  .venv/bin/python scripts/sync_meta_pipeline.py --skip-meta-cn          # 已有人工 field_cn，只同步库结构+prompt
  .venv/bin/python scripts/sync_meta_pipeline.py --only-schema-shadow    # 仅重写 prompts（meta 已就绪）

单独脚本仍可直接运行：sync_constitution 仅能通过 psql/CALL，其余见各文件说明。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent

for p in (ROOT, SCRIPTS):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from tools.database import db_conn


def run_sync_constitution() -> None:
    with db_conn() as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("CALL meta.sync_constitution()")


def main() -> int:
    ap = argparse.ArgumentParser(description="元数据 constitution → field_cn → schema_shadow 流水线")
    ap.add_argument(
        "--skip-constitution",
        action="store_true",
        help="不执行 CALL meta.sync_constitution()（表结构未变、只补中文或只刷 prompt 时用）",
    )
    ap.add_argument(
        "--skip-meta-cn",
        action="store_true",
        help="不调用 LLM 填 field_cn（省时间；schema_shadow 仍用现有 field_cn/description）",
    )
    ap.add_argument(
        "--only-schema-shadow",
        action="store_true",
        help="只执行第 3 步写入 meta.prompts（等同 --skip-constitution --skip-meta-cn）",
    )
    ap.add_argument(
        "--continue-on-meta-cn-error",
        action="store_true",
        help="sync_meta_cn 失败时仍继续执行 sync_schema_shadow",
    )
    args = ap.parse_args()

    only_shadow = args.only_schema_shadow
    skip_const = args.skip_constitution or only_shadow
    skip_cn = args.skip_meta_cn or only_shadow

    if not skip_const:
        print("[pipeline] ① CALL meta.sync_constitution() …")
        run_sync_constitution()
        print("[pipeline] ① 完成")

    if not skip_cn:
        print("[pipeline] ② sync_meta_cn.py …")
        import sync_meta_cn

        rc = sync_meta_cn.main()
        if rc != 0:
            print("[pipeline] ② 失败 (exit %s)" % rc, file=sys.stderr)
            if not args.continue_on_meta_cn_error:
                return rc
        else:
            print("[pipeline] ② 完成")
    else:
        print("[pipeline] ② 已跳过 (--skip-meta-cn / --only-schema-shadow)")

    print("[pipeline] ③ sync_schema_shadow.py …")
    import sync_schema_shadow

    sync_schema_shadow.main()
    print("[pipeline] ③ 完成")
    print("[pipeline] 全部结束")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
