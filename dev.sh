#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"

if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PY="$ROOT/.venv/bin/python"
else
  echo "未找到 .venv，请在项目根目录执行: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

cleanup() {
  local p
  for p in $(jobs -p); do
    kill "$p" 2>/dev/null || true
  done
}

trap 'cleanup; exit 0' INT TERM

# 避免上次未退干净的 uvicorn 占满 8000 导致 Address already in use
if command -v lsof >/dev/null 2>&1; then
  for pid in $(lsof -ti :8000 2>/dev/null || true); do
    echo "[dev.sh] 端口 8000 已被占用，结束进程 $pid" >&2
    kill "$pid" 2>/dev/null || true
  done
  sleep 0.3
fi

"$PY" -m uvicorn main:app --reload --host 127.0.0.1 --port 8000 &
(cd "$ROOT/frontend" && npm run dev) &

wait
