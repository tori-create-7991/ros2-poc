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

# 起動中に up し直すと、イメージや設定の変更でコンテナが作り直され、ドライバが落ちてトルクが抜ける
if [ -n "$(docker ps --filter 'name=^ros2real$' --filter 'status=running' -q)" ]; then
  echo "ros2real は既に起動中。作り直すとドライバが落ちてトルクが抜けるため、先に 'bash scripts/down-real.sh' すること。" >&2
  exit 1
fi

if [ ! -e "$DEVICE" ]; then
  echo "$DEVICE が無い。CRANE-X7 の USB 接続と host/99-crane-x7.rules の導入（README「実機で動かす」）を確認すること。" >&2
  exit 1
fi

# latency_timer が 1 でないと 200Hz の制御が崩れる。sysfs が読めるときだけ警告する（非致命）。
LATENCY_FILE="/sys/bus/usb-serial/devices/$(basename "$(readlink -f "$DEVICE")")/latency_timer"
if [ -r "$LATENCY_FILE" ] && [ "$(cat "$LATENCY_FILE")" != "1" ]; then
  echo "警告: $LATENCY_FILE が 1 ではない。host/99-crane-x7.rules が効いていない可能性がある。" >&2
fi

docker compose --profile real up -d --build
