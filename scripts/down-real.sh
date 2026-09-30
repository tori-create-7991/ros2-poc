#!/usr/bin/env bash
# CRANE-X7 実機ドライバ (ros2real) を安全に止める。
# ドライバが止まるとサーボのトルクが抜けて腕が自重で落ちるため、
# 先に腕を安全な姿勢へ戻したか確認してから、ドライバ停止 → コンテナ削除の順で行う。
# ros2lab は止めない。`docker compose down` を直接使わないこと（ドライバが即座に落ちる）。
#   bash scripts/down-real.sh          # 確認プロンプトあり
#   bash scripts/down-real.sh --yes    # 確認を省略
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -z "$(docker ps --filter 'name=^ros2real$' --filter 'status=running' -q)" ]; then
  echo "ros2real は起動していない。" >&2
  exit 0
fi

if [ "${1:-}" != "--yes" ]; then
  echo "ドライバを止めるとトルクが抜け、腕が自重で落ちる。" >&2
  read -r -p "腕を安全な姿勢（低い位置・何も持っていない）へ戻した？ [yes/no] " answer
  if [ "$answer" != "yes" ]; then
    echo "中止した。" >&2
    exit 1
  fi
fi

# ドライバを SIGINT で終了させ、ros2_control に後始末させてからコンテナを消す
docker exec ros2real pkill -INT -f "ros2 launch crane_x7_control" || true
sleep "${DRIVER_STOP_WAIT:-3}"
docker compose --profile real rm -sf ros2real
