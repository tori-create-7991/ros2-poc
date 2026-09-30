#!/usr/bin/env bash
# アームシミュ (ros2arm) を起動する。
# ros2real（実機ドライバ）が起動中だと /crane_x7_arm_controller/... が二重になり、
# シミュ向けの指令が実機に届く恐れがあるため、ros2real 起動中は拒否する。
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -n "$(docker ps --filter 'name=^ros2real$' --filter 'status=running' -q)" ]; then
  echo "ros2real が起動中。シミュ向けの指令が実機に届く恐れがあるため、先に 'bash scripts/down-real.sh' すること。" >&2
  exit 1
fi

docker compose --profile arm up -d --build
