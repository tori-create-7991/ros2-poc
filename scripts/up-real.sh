#!/usr/bin/env bash
# CRANE-X7 実機ドライバ (ros2real) を起動する。
# - ros2arm（シミュ）と同時に起動すると /crane_x7_arm_controller/... が二重になり、
#   クライアントの指令が実機に届く恐れがあるため、ros2arm 起動中は拒否する。
# - デバイス (/dev/crane_x7) が無いと docker の不明瞭なエラーになるため、事前に確認する。
# 直接 `docker compose --profile real up` は使わず、必ずこのスクリプト経由で起動すること。
set -euo pipefail

cd "$(dirname "$0")/.."

DEVICE="${CRANE_X7_DEVICE:-/dev/crane_x7}"

if [ -n "$(docker ps --filter 'name=^ros2arm$' --filter 'status=running' -q)" ]; then
  echo "ros2arm が起動中。実機と同名コントローラが二重になるため、先に 'docker compose --profile arm down' すること。" >&2
  exit 1
fi

if [ ! -e "$DEVICE" ]; then
  echo "$DEVICE が無い。CRANE-X7 の USB 接続と host/99-crane-x7.rules の導入（README「実機で動かす」）を確認すること。" >&2
  exit 1
fi

docker compose --profile real up -d --build
