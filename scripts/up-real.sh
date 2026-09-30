#!/usr/bin/env bash
# CRANE-X7 実機ドライバ (ros2real) を起動する。
# ros2arm（シミュ）と同時に起動すると /crane_x7_arm_controller/... が二重になり、
# クライアントの指令が実機に届く恐れがあるため、ros2arm 起動中は拒否する。
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -n "$(docker ps --filter 'name=^ros2arm$' --filter 'status=running' -q)" ]; then
  echo "ros2arm が起動中。実機と同名コントローラが二重になるため、先に 'docker compose --profile arm down' すること。" >&2
  exit 1
fi

docker compose --profile real up -d --build
