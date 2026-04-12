#!/usr/bin/env bash
# 本机执行：同步云端代码、按需跑迁移、重启服务（多次 ssh，每次可能提示输入密码）
set -euo pipefail

SERVER="root@47.86.34.90"
REMOTE_DIR="/home/AIERP"

echo "=== 同步代码 ==="
ssh "$SERVER" "cd $REMOTE_DIR && git pull"

echo "=== 执行未跑的迁移（按需取消注释并改文件名）==="
# ssh "$SERVER" "cd $REMOTE_DIR && psql -U nicjiang -d aierp -f constitution/v35_xxx.sql"

echo "=== 重启服务 ==="
ssh "$SERVER" "systemctl restart aierp"

echo "=== 完成 ==="
