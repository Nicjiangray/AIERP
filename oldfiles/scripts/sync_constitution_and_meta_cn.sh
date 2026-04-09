#!/usr/bin/env bash
# 一次执行：重建 meta 目录（meta_fields / meta_tables）+ 本地 LLM 批量填 field_cn
# 用法（在仓库根目录）：./scripts/sync_constitution_and_meta_cn.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PGUSER="${PGUSER:-nicjiang}"
PGDATABASE="${PGDATABASE:-aierp}"
echo "[sync] CALL meta.sync_constitution() …"
psql -U "$PGUSER" -d "$PGDATABASE" -c "CALL meta.sync_constitution();"
echo "[sync] scripts/sync_meta_cn.py（本地 LLM 填 field_cn）…"
.venv/bin/python scripts/sync_meta_cn.py
echo "[sync] 完成。"
