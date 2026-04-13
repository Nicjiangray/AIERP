#!/usr/bin/env python3
"""
扫描 constitution/v*.sql，按版本号排序，跳过 meta.migrations 中已执行的文件，依次执行。
全部成功后运行 scripts/sync_meta_pipeline.py。

用法（仓库根目录）：
  python scripts/migrate.py
  python scripts/migrate.py --host 47.86.34.90 --password "$PG_API_PASSWORD"
  python scripts/migrate.py --env-file .env
  python scripts/migrate.py --dry-run

手工迁库后若 meta.migrations 为空：先运行 scripts/seed_migrations.py 补录已执行文件名，再使用本脚本。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import sqlparse

from migration_common import (
    CONSTITUTION,
    ROOT,
    add_standard_db_arguments,
    connect_db,
    ensure_migrations_table,
    list_migration_files,
    load_env_file_early,
    migration_sort_key,
    os_environ_for_db,
)

SYNC_PIPELINE = ROOT / "scripts" / "sync_meta_pipeline.py"


def _split_sql(content: str) -> list[str]:
    parts = sqlparse.split(content)
    out: list[str] = []
    for p in parts:
        s = (p or "").strip()
        if not s:
            continue
        out.append(s)
    return out


def main() -> int:
    load_env_file_early(sys.argv[1:])

    ap = argparse.ArgumentParser(description="运行 constitution/v*.sql 迁移并同步元数据流水线")
    add_standard_db_arguments(ap)
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="连接数据库并列出已执行/待执行迁移，不执行 SQL、不跑 sync_meta_pipeline",
    )
    args = ap.parse_args()

    all_files = list_migration_files()
    if not all_files:
        print(f"No migration files under {CONSTITUTION}", file=sys.stderr)
        return 1

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

    with conn.cursor() as cur:
        cur.execute("SELECT version FROM meta.migrations")
        applied = {row[0] for row in cur.fetchall()}
    conn.commit()

    pending = [p for p in all_files if p.name not in applied]
    applied_sorted = sorted(
        applied,
        key=lambda n: migration_sort_key(n) if n.endswith(".sql") else (0, 0, n),
    )

    print(
        "Already applied: "
        + (", ".join(applied_sorted) if applied_sorted else "(none)")
    )
    print(
        "Pending: "
        + (", ".join(p.name for p in pending) if pending else "(none)")
    )

    if not applied and len(pending) > 3:
        print(
            "Note: meta.migrations is empty but many files are pending. "
            "If this DB was migrated manually before, run: python scripts/seed_migrations.py"
        )

    if args.dry_run:
        conn.close()
        print("(dry-run: no migrations executed, sync_meta_pipeline not run)")
        return 0

    if not pending:
        print("All migrations applied.")
    else:
        for path in pending:
            print(f"Applying {path.name} ... ", end="", flush=True)
            raw = path.read_text(encoding="utf-8")
            statements = _split_sql(raw)
            if not statements:
                print("SKIP (empty)")
                continue
            try:
                with conn.cursor() as cur:
                    for stmt in statements:
                        cur.execute(stmt)
                    cur.execute(
                        "INSERT INTO meta.migrations (version) VALUES (%s)",
                        (path.name,),
                    )
                conn.commit()
                print("OK")
            except Exception as e:
                conn.rollback()
                print(f"FAILED\n{e}", file=sys.stderr)
                return 1

        print("All migrations applied.")

    conn.close()

    print("Running sync_meta_pipeline.py ...")
    try:
        subprocess.run(
            [sys.executable, str(SYNC_PIPELINE)],
            cwd=str(ROOT),
            check=True,
            env=os_environ_for_db(
                args.host,
                args.port,
                args.user,
                args.password,
                args.db,
            ),
        )
    except subprocess.CalledProcessError as e:
        print(f"sync_meta_pipeline.py failed with exit code {e.returncode}", file=sys.stderr)
        return e.returncode
    except FileNotFoundError:
        print(f"Python or script not found: {SYNC_PIPELINE}", file=sys.stderr)
        return 1

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
