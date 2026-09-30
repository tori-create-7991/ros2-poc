#!/usr/bin/env bash
# CRANE-X7 実機ドライバ (ros2real) を安全に止める。
# ドライバが止まるとサーボのトルクが抜けて腕が自重で落ちるため、
# 先に腕を安全な姿勢へ戻したか確認してから、ドライバ停止 → 終了確認 → コンテナ削除の順で行う。
# ros2lab は止めない。`docker compose down` を直接使わないこと（ドライバが即座に落ちる）。
#   bash scripts/down-real.sh          # 確認プロンプトあり
#   bash scripts/down-real.sh --yes    # 確認を省略
set -euo pipefail

cd "$(dirname "$0")/.."

# pgrep/pkill の `[r]` は自分自身（と docker exec のラッパー）にマッチさせないための書き方
DRIVER_LAUNCH_PATTERN='[r]os2 launch crane_x7_control'
DRIVER_NODE_PATTERN='[r]os2_control_node'

if [ -z "$(docker ps --filter 'name=^ros2real$' --filter 'status=running' -q)" ]; then
  echo "ros2real は起動していない。" >&2
  exit 0
fi

if [ "${1:-}" != "--yes" ]; then
  echo "ドライバを止めるとトルクが抜け、腕が自重で落ちる。" >&2
  answer=""
  read -r -p "腕を安全な姿勢（低い位置・何も持っていない）へ戻した？ [yes/no] " answer || answer=""
  if [ "$answer" != "yes" ]; then
    echo "中止した。" >&2
    exit 1
  fi
fi

# ドライバを SIGINT で終了させ、ros2_control に後始末させる（マッチ無しは rc=1 で正常）
stop_rc=0
docker exec ros2real pkill -INT -f "$DRIVER_LAUNCH_PATTERN" || stop_rc=$?
if [ "$stop_rc" -gt 1 ]; then
  echo "警告: ドライバへの SIGINT 送信に失敗した (rc=$stop_rc)。" >&2
fi

# ドライバの終了を待つ。終わらないままコンテナを消すと SIGKILL でトルクが突然抜ける
timeout="${DRIVER_STOP_WAIT:-15}"
waited=0
while [ -n "$(docker exec ros2real pgrep -f "$DRIVER_NODE_PATTERN" || true)" ]; do
  if [ "$waited" -ge "$timeout" ]; then
    echo "警告: ドライバが ${timeout} 秒経っても終了しない。このまま削除すると SIGKILL でトルクが突然抜ける。" >&2
    if [ "${1:-}" != "--yes" ]; then
      answer=""
      read -r -p "それでも削除する？ [yes/no] " answer || answer=""
      if [ "$answer" != "yes" ]; then
        echo "中止した。" >&2
        exit 1
      fi
    fi
    break
  fi
  sleep 1
  waited=$((waited + 1))
done

docker compose --profile real rm -sf ros2real
