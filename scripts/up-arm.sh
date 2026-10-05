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

# このスクリプトは SROS2 を知らない（素の docker compose で環境 a の ros2arm を作る）。ros2lab-a が環境 b / c のまま使うと、
# ros2arm だけ環境 a で作り直されて混在し、DDS で通信できない。SROS2 の環境では up-env.sh で ros2lab-a/b と一緒に起動する。
# 空（docker によっては <no value>）はラベル導入前のコンテナなので a として扱う
lab_env="$(docker inspect -f '{{index .Config.Labels "ros2poc.env"}}' ros2lab-a 2>/dev/null || true)"
if [ -n "$lab_env" ] && [ "$lab_env" != a ] && [ "$lab_env" != '<no value>' ]; then
  echo "ros2lab-a が SROS2 環境 $lab_env で起動している。このスクリプトは環境 a 用なので、ros2arm も環境 $lab_env で起動する: 'bash scripts/up-env.sh $lab_env --arm'" >&2
  exit 1
fi

docker compose --profile arm up -d --build
