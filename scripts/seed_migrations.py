#!/usr/bin/env python3
"""
将 constitution/ 下当前所有 v*.sql 文件名补录到 meta.migrations（ON CONFLICT DO NOTHING），
用于手工迁库后对齐记录，之后 migrate.py 只跑新增文件。

用法（仓库根目录）：
  .venv/bin/python scripts/seed_migrations.py
  .venv/bin/python scripts/seed_migrations.py --host 47.86.34.90 --password "$PG_API_PASSWORD"
  .venv/bin/python scripts/seed_migrations.py --env-file .env
"""

from __future__ import annotations

import argparse
import sys

from migration_common import (
    CONSTITUTION,
    add_standard_db_arguments,
    connect_db,
    ensure_migrations_table,
    list_migration_files,
    load_env_file_early,
)


def main() -> int:
    load_env_file_early(sys.argv[1:])

    ap = argparse.ArgumentParser(
        description="补录已执行迁移：插入 constitution 下全部 v*.sql 文件名到 meta.migrations",
    )
    add_standard_db_arguments(ap)
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="只列出将插入的文件名，不连库、不写入",
    )
    args = ap.parse_args()

    files = list_migration_files()
    if not files:
        print(f"No v*.sql under {CONSTITUTION}", file=sys.stderr)
        return 1

    if args.dry_run:
        print(f"Would seed {len(files)} migration record(s):")
        for p in files:
            print(f"  {p.name}")
        return 0

    print(f"Connecting to {args.host}:{args.port}/{args.db}...")
    try:
        conn = connect_db(args.host, args.port, args.user, args.password, args.db)
    except Exception as e:
        print(f"Connection failed: {e}", file=sys.stderr)
        return 1

    try:
        ensure_migrations_table(conn)
    except Exception as e:
        print(f"Failed to ensure meta.migrations: {e}", file=sys.stderr)
        conn.close()
        return 1

    print("meta.migrations table ready.")

    inserted = 0
    skipped = 0
    try:
        with conn.cursor() as cur:
            for p in files:
                cur.execute(
                    """
                    INSERT INTO meta.migrations (version) VALUES (%s)
                    ON CONFLICT (version) DO NOTHING
                    """,
                    (p.name,),
                )
                n = cur.rowcount or 0
                if n:
                    inserted += n
                else:
                    skipped += 1
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Seed failed: {e}", file=sys.stderr)
        conn.close()
        return 1

    conn.close()
    print(
        f"Done. Inserted {inserted} new row(s); "
        f"{skipped} already present (skipped); "
        f"{len(files)} file(s) on disk."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
