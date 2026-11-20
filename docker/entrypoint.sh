#!/bin/sh
# 以 root 启动时按 PUID/PGID 降权运行，避免在 NAS 上产出 root 属主的文件。
set -e

DATA_DIR="${DATA_DIR:-/data}"
mkdir -p "$DATA_DIR"

if [ "$(id -u)" = "0" ]; then
  PUID="${PUID:-1000}"
  PGID="${PGID:-1000}"
  # 仅当属主不一致时才递归 chown：大型媒体库每次启动全盘扫描会很慢
  if [ "$(stat -c %u "$DATA_DIR" 2>/dev/null || echo 0)" != "$PUID" ]; then
    chown -R "$PUID:$PGID" "$DATA_DIR" 2>/dev/null || true
  fi
  exec gosu "$PUID:$PGID" python -m forager
fi

exec python -m forager
