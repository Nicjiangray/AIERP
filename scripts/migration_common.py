"""
migrate.py / seed_migrations.py 共用：路径、连接参数、constitution/v*.sql 列表、meta.migrations 建表。
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parents[1]
CONSTITUTION = ROOT / "constitution"

CREATE_MIGRATIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS meta.migrations (
    version VARCHAR(100) PRIMARY KEY,
    applied_at TIMESTAMPTZ DEFAULT now()
);
"""

_VERSION_PREFIX_RE = re.compile(r"^v(\d+)(?:_(\d+))?")


def load_env_file_early(argv: list[str]) -> None:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--env-file", type=str, default=None)
    ns, _ = p.parse_known_args(argv)
    if ns.env_file:
        try:
            from dotenv import load_dotenv
        except ImportError:
            print(
                "migration scripts: 需要 python-dotenv 才能使用 --env-file",
                file=sys.stderr,
            )
            sys.exit(1)
        load_dotenv(ns.env_file)


def migration_sort_key(filename: str) -> tuple:
    m = _VERSION_PREFIX_RE.match(filename)
    if not m:
        return (999_999, 0, filename)
    major = int(m.group(1))
    minor = int(m.group(2)) if m.group(2) else 0
    return (major, minor, filename)


def list_migration_files() -> list[Path]:
    return sorted(
        CONSTITUTION.glob("v*.sql"),
        key=lambda p: migration_sort_key(p.name),
    )


def add_standard_db_arguments(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--host", default=os.environ.get("PGHOST", "localhost"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("PGPORT", "5432")))
    ap.add_argument("--user", default=os.environ.get("PGUSER", "nicjiang"))
    ap.add_argument(
        "--password",
        default=os.environ.get("PG_API_PASSWORD", ""),
        help="数据库密码（默认读环境变量 PG_API_PASSWORD）",
    )
    ap.add_argument("--db", default=os.environ.get("PGDATABASE", "aierp"))
    ap.add_argument(
        "--env-file",
        type=str,
        default=None,
        help="可选：加载 .env（PGHOST / PG_API_PASSWORD 等）；在解析其它参数前预加载",
    )


def connect_db(host: str, port: int, user: str, password: str, dbname: str):
    return psycopg2.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        dbname=dbname,
    )


def ensure_migrations_table(conn) -> None:
    conn.autocommit = False
    with conn.cursor() as cur:
        cur.execute(CREATE_MIGRATIONS_TABLE_SQL)
    conn.commit()


def os_environ_for_db(
    host: str,
    port: int,
    user: str,
    password: str,
    dbname: str,
) -> dict:
    """
    供 migrate.py 调用 sync_meta_pipeline 子进程时使用，与 tools.database._api_pg_kwargs 键一致，
    使 pipeline 与迁移连同一实例（避免 --host 指云端时子进程仍连 localhost）。
    """
    env = os.environ.copy()
    env["PGHOST"] = host
    env["PGPORT"] = str(port)
    env["PGUSER"] = user
    env["PGDATABASE"] = dbname
    env["PG_API_PASSWORD"] = password or ""
    return env
