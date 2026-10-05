#!/usr/bin/env bash
# 動作シナリオを 1 コマンドで実行し、判定して録画する（シミュ専用）。
#   ros2lab-a からアームへ関節指令（README と同じ ros2 topic pub）、グリッパは ros2arm から action を送る。
#   ros2arm で記録（カメラ・/joint_states・手先 TF・デスクトップ画面）→ 終了後に判定 → 1 本の mp4 に合成。
# 出力: workspace/runs/<日時>/（scenario.mp4, result.json, commands.log ほか）
# 終了コード: 0 = 全ステップ PASS / 1 = FAIL あり / 2 = 環境・実行時の問題 / 64 = 引数の誤り
set -euo pipefail

cd "$(dirname "$0")/.."

usage() {
  cat >&2 <<'EOF'
usage: bash scripts/run-scenario.sh [--scenario <名前|YAMLのパス>] [--repeat N] [--start-sim] [--timeout 秒]
  --scenario   default（既定）/ fail_demo / examples、または YAML ファイルのパス
  --repeat     シナリオ全体の繰り返し回数（YAML の repeat を上書き）
  --start-sim  シミュ（Gazebo + 仮想カメラ、三人称視点）が動いていなければ起動する
  --timeout    トピックが流れ始めるまで待つ秒数（既定 120、--start-sim 時 900）
EOF
  exit 64
}

SCENARIO=default
REPEAT=""
START_SIM=0
TIMEOUT=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --scenario) [ "$#" -ge 2 ] || usage; SCENARIO="$2"; shift 2 ;;
    --repeat) [ "$#" -ge 2 ] || usage; REPEAT="$2"; shift 2 ;;
    --start-sim) START_SIM=1; shift ;;
    --timeout) [ "$#" -ge 2 ] || usage; TIMEOUT="$2"; shift 2 ;;
    -h|--help) usage ;;
    *) echo "不明な引数: $1" >&2; usage ;;
  esac
done
case "$REPEAT" in ''|[1-9]|1[0-9]|20) ;; *) echo "--repeat は 1〜20" >&2; exit 64 ;; esac
if [ -z "$TIMEOUT" ]; then
  if [ "$START_SIM" = 1 ]; then TIMEOUT=900; else TIMEOUT=120; fi
fi
case "$TIMEOUT" in ''|*[!0-9]*) echo "--timeout は秒数（整数）" >&2; exit 64 ;; esac

if [ -f "$SCENARIO" ]; then
  SCENARIO_FILE="$SCENARIO"
elif [[ "$SCENARIO" =~ ^[A-Za-z0-9_-]+$ ]] && [ -f "ros2_poc_sim/config/scenarios/$SCENARIO.yaml" ]; then
  SCENARIO_FILE="ros2_poc_sim/config/scenarios/$SCENARIO.yaml"
else
  echo "シナリオが見つからない: $SCENARIO" >&2
  exit 64
fi

running() { [ -n "$(docker ps --filter "name=^$1\$" --filter 'status=running' -q)" ]; }

# 実機ドライバが動いていると、同名のコントローラに指令が届いてしまう
if running ros2real; then
  echo "ros2real（実機ドライバ）が起動中。このスクリプトはシミュ専用なので実行しない（'bash scripts/down-real.sh' で止めてから）。" >&2
  exit 2
fi
for c in ros2arm ros2lab-a; do
  if ! running "$c"; then
    echo "$c が起動していない。先に 'bash scripts/up-arm.sh' を実行する。" >&2
    exit 2
  fi
done

ROS_ENV='source /opt/ros/jazzy/setup.bash; source /opt/crane_ws/install/setup.bash; source /opt/ros2_poc_ws/install/setup.bash'
arm() { docker exec -u ubuntu -e DISPLAY=:1 ros2arm bash -c "$ROS_ENV; $1"; }
arm_bg() { docker exec -d -u ubuntu -e DISPLAY=:1 ros2arm bash -c "$ROS_ENV; $1"; }
lab() { docker compose exec -T ros2lab-a bash -lc "$1"; }

TS="$(date +%Y%m%d-%H%M%S)"
RUN_HOST="workspace/runs/$TS"
RUN="/workspace/runs/$TS"
# ros2arm の ubuntu が書けるよう、ディレクトリはコンテナ側で作る（/workspace は root と ubuntu が混在する）
arm "mkdir -p $RUN"
cp "$SCENARIO_FILE" "$RUN_HOST/scenario.yaml"
echo "出力先: $RUN_HOST"

topic_ok() {
  arm "timeout 15 ros2 topic echo --once --field header.stamp $1 > /dev/null 2>&1" < /dev/null
}
sim_ready() { topic_ok /joint_states && topic_ok /camera/color/image_raw; }

if ! sim_ready; then
  if [ "$START_SIM" = 1 ]; then
    echo "シミュを起動する（初回やホストが重いときは数分〜十数分かかる）。ログ: $RUN_HOST/sim.log"
    arm_bg "exec ros2 launch ros2_poc_sim arm_with_camera.launch.py placement:=fixed_front_wide > $RUN/sim.log 2>&1"
  fi
  echo "トピック（/joint_states, /camera/color/image_raw）を待つ（最大 ${TIMEOUT} 秒）"
  deadline=$(( $(date +%s) + TIMEOUT ))
  until sim_ready; do
    if [ "$(date +%s)" -ge "$deadline" ]; then
      echo "トピックが流れない。シミュを起動していなければ --start-sim を付ける（docs/sim-scenario-recording.md）。" >&2
      exit 2
    fi
    sleep 5
  done
fi

REPEAT_ARG=""
[ -n "$REPEAT" ] && REPEAT_ARG="--repeat $REPEAT"
if ! arm "ros2 run ros2_poc_sim scenario_cli commands $RUN/scenario.yaml $REPEAT_ARG --steps-out $RUN/steps.json" \
    > "$RUN_HOST/commands.tsv" < /dev/null; then
  echo "シナリオの検証に失敗した（上のメッセージ参照）" >&2
  exit 64
fi

stop_recorders() {
  # [s] / [x] は pkill 自身を呼ぶ bash -c のコマンドラインに一致させないため
  arm "pkill -INT -f '[s]cenario_observer --out $RUN' ; pkill -INT -f '[x]11grab.*$RUN/desktop.mp4'" < /dev/null || true
  for _ in $(seq 1 30); do
    arm "pgrep -f '[s]cenario_observer --out $RUN|[x]11grab.*$RUN/desktop.mp4'" > /dev/null 2>&1 < /dev/null || return 0
    sleep 1
  done
  echo "記録プロセスが止まらない（30 秒）" >&2
}
trap stop_recorders EXIT

SIZE="$(arm "xdpyinfo -display :1 | awk '/dimensions:/{print \$2}'" < /dev/null)"
arm_bg "exec ros2 run ros2_poc_sim scenario_observer --out $RUN > $RUN/observer.log 2>&1"
arm_bg "date +%s.%N > $RUN/desktop_t0.txt; exec ffmpeg -y -v error -f x11grab -framerate 10 -video_size $SIZE -i :1 -c:v libx264 -preset ultrafast -pix_fmt yuv420p $RUN/desktop.mp4 2> $RUN/desktop_ffmpeg.log"
# 記録の立ち上がり待ち: 送信前のフレームが要るので、カメラのフレームが記録され始めるまで待つ
frames_recorded() { { wc -l < "$RUN_HOST/camera_frames.csv"; } 2>/dev/null || echo 0; }
for _ in $(seq 1 60); do
  [ "$(frames_recorded)" -ge 4 ] && break
  sleep 1
done
if [ "$(frames_recorded)" -lt 4 ]; then
  echo "カメラのフレームが記録されない（$RUN_HOST/observer.log を確認）" >&2
  exit 2
fi

: > "$RUN_HOST/events.jsonl"
: > "$RUN_HOST/commands.log"
total=$(wc -l < "$RUN_HOST/commands.tsv" | tr -d ' ')
i=0
while IFS=$'\t' read -r name target wait cmd <&3; do
  i=$((i + 1))
  echo "[$i/$total] $name ($target)"
  echo "$(date '+%H:%M:%S') [$target] \$ $cmd" | tee -a "$RUN_HOST/commands.log"
  # 送信前後の時刻はコンテナ内で取る（コンテナは Colima VM の時計を共有し、記録ノードと揃う）
  script="date +%s.%N; timeout 30 $cmd > /dev/null 2>&1; rc=\$?; date +%s.%N; exit \$rc"
  rc=0
  if [ "$target" = ros2lab-a ]; then
    out="$(lab "$script" < /dev/null)" || rc=$?
  else
    out="$(arm "$script" < /dev/null)" || rc=$?
  fi
  stamps="$(printf '%s\n' "$out" | grep -E '^[0-9]+\.[0-9]+$' || true)"
  t_start="$(printf '%s\n' "$stamps" | sed -n 1p)"
  t_sent="$(printf '%s\n' "$stamps" | sed -n 2p)"
  if [ -z "$t_start" ] || [ -z "$t_sent" ]; then
    echo "  送信時刻が取れない（rc=$rc）" >&2
    [ "$rc" = 0 ] && rc=2
    t_start="${t_start:-0}"; t_sent="${t_sent:-$t_start}"
  fi
  [ "$rc" = 0 ] || echo "  命令が失敗した（rc=$rc）" >&2
  printf '{"index": %d, "name": "%s", "target": "%s", "t_start": %s, "t_sent": %s, "rc": %d}\n' \
    "$((i - 1))" "$name" "$target" "$t_start" "$t_sent" "$rc" >> "$RUN_HOST/events.jsonl"
  # 静止するまで待つ（シミュは実時間より遅いので指令時間では足りない）。最低でも目安の秒数は待つ
  arm "ros2 run ros2_poc_sim scenario_cli wait $RUN $((i - 1))" < /dev/null || sleep "$wait"
done 3< "$RUN_HOST/commands.tsv"

stop_recorders
trap - EXIT

judge_rc=0
arm "ros2 run ros2_poc_sim scenario_cli judge $RUN" < /dev/null || judge_rc=$?
if [ "$judge_rc" -gt 1 ]; then
  echo "判定に失敗した（rc=$judge_rc）" >&2
  exit 2
fi
if arm "ros2 run ros2_poc_sim scenario_cli compose $RUN" < /dev/null; then
  echo "動画: $RUN_HOST/scenario.mp4"
else
  echo "合成に失敗した（$RUN_HOST/overlay/filtergraph.txt を確認）" >&2
fi
echo "判定: $RUN_HOST/result.json"
exit "$judge_rc"
