#!/usr/bin/env bash
# 動作シナリオを 1 コマンドで実行し、判定して録画する（シミュ専用）。
#   ros2lab-a からアームへ関節指令（README と同じ ros2 topic pub）、グリッパは ros2arm から action を送る。
#   ros2arm で記録（カメラ・/joint_states・手先 TF・デスクトップ画面）→ 終了後に判定 → 1 本の mp4 に合成。
# 出力: workspace/runs/<日時>/（scenario.mp4, result.json, commands.log ほか）
# 終了コード: 0 = 全ステップ PASS / 1 = FAIL あり / 2 = 環境・実行時の問題（指令が送れなかった場合を含む）/
#            64 = 引数の誤り / 130 = Ctrl-C
set -euo pipefail

CALLER_DIR="$PWD"   # --scenario の相対パスは呼び出し元から解決する
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

if [[ "$SCENARIO" == */* || "$SCENARIO" == *.yaml || "$SCENARIO" == *.yml ]]; then
  # パス指定: 呼び出し元のディレクトリから解決する（リポジトリ内の同名ファイルを黙って選ばない）
  case "$SCENARIO" in /*) SCENARIO_FILE="$SCENARIO" ;; *) SCENARIO_FILE="$CALLER_DIR/$SCENARIO" ;; esac
  if [ ! -f "$SCENARIO_FILE" ]; then
    echo "シナリオが見つからない: $SCENARIO" >&2
    exit 64
  fi
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
# ros2arm は SROS2 化していない（常に環境 a）。ros2lab-a が環境 b / c だと DDS で通信できず、
# コントローラ待ちで時間切れになって Discovery の問題と誤って案内してしまう。
# 空（docker によっては <no value>）はラベル導入前のコンテナなので a として扱う
lab_env="$(docker inspect -f '{{index .Config.Labels "ros2poc.env"}}' ros2lab-a 2>/dev/null || true)"
if [ -n "$lab_env" ] && [ "$lab_env" != a ] && [ "$lab_env" != '<no value>' ]; then
  echo "ros2lab-a が SROS2 環境 $lab_env で起動している。ros2arm は環境 a でしか使えないので 'bash scripts/up-env.sh a' で戻す。" >&2
  exit 2
fi

ROS_ENV='source /opt/ros/jazzy/setup.bash; source /opt/crane_ws/install/setup.bash; source /opt/ros2_poc_ws/install/setup.bash'
arm() { docker exec -u ubuntu -e DISPLAY=:1 ros2arm bash -c "$ROS_ENV; $1"; }
arm_bg() { docker exec -d -u ubuntu -e DISPLAY=:1 ros2arm bash -c "$ROS_ENV; $1"; }
# compose のプロジェクト名（= ディレクトリ名）に依存しないよう、コンテナ名で直接入る
lab() { docker exec ros2lab-a bash -lc "$1"; }
# scenario_cli の送信先（論理名）とコンテナの対応はここだけが持つ
send() {
  case "$1" in
    lab) lab "$2" ;;
    sim) arm "$2" ;;
    *) echo "不明な送信先: $1" >&2; return 2 ;;
  esac
}
fail_env() { echo "$1" >&2; exit 2; }

# 録画・合成に要る外部コマンド・フォント（ベースイメージ由来）を先に確かめる
arm "ros2 run ros2_poc_sim scenario_cli doctor" < /dev/null \
  || fail_env "ros2arm に録画・合成の前提が揃っていない（上のメッセージ参照。'bash scripts/up-arm.sh' でイメージを作り直す）"

TS="$(date +%Y%m%d-%H%M%S)"
# ホスト側の出力先は ros2arm の /workspace のマウント元（up-arm.sh を実行した checkout の ./workspace）。
# このスクリプトのある checkout とは限らない（git worktree など）。テストでは RUN_SCENARIO_WORKSPACE で差し替える
WORKSPACE_HOST="${RUN_SCENARIO_WORKSPACE:-$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/workspace"}}{{.Source}}{{end}}{{end}}' ros2arm 2>/dev/null || true)}"
if [ -z "$WORKSPACE_HOST" ] || [ ! -d "$WORKSPACE_HOST" ]; then
  fail_env "ros2arm の /workspace のマウント元が分からない（${WORKSPACE_HOST:-空}）。'bash scripts/up-arm.sh' で作り直す"
fi
RUN_HOST="$WORKSPACE_HOST/runs/$TS"
RUN="/workspace/runs/$TS"
# 記録プロセス（observer・カメラ用 ffmpeg・デスクトップ録画）。[s] などは pkill / pgrep 自身を呼ぶ
# bash -c のコマンドラインに一致させないため。ANY_* は他の実行（前回の残り）も含めて探すとき用
RECORDERS="[s]cenario_observer --out $RUN|[r]awvideo.*$RUN/camera\.mp4|[x]11grab.*$RUN/desktop\.mp4"
# 最初の停止指示（INT）はカメラ用 ffmpeg に直接送らない。observer が入力を閉じれば ffmpeg は残りを
# 書き出して終わる。ffmpeg に INT を送ると読み残したフレーム（最後のステップの判定用）が落ちる
RECORDERS_INT="[s]cenario_observer --out $RUN|[x]11grab.*$RUN/desktop\.mp4"
ANY_RECORDERS='[s]cenario_observer --out /workspace/runs/|[r]awvideo.*/workspace/runs/[0-9-]*/camera\.mp4|[x]11grab.*/workspace/runs/[0-9-]*/desktop\.mp4'

# 同時実行の拒否（同じアームに 2 本の指令が混ざる）。ロックは ros2arm の /tmp に置く。
# コンテナを作り直す（up-arm.sh）と消えるが、docker restart や Colima の再起動では残る
LOCK=/tmp/run-scenario.lock
OWNER="$TS $(hostname -s 2>/dev/null || echo host) $$"
if ! arm "mkdir $LOCK && echo '$OWNER' > $LOCK/owner" < /dev/null 2>/dev/null; then
  owner="$(arm "cat $LOCK/owner" < /dev/null 2>/dev/null || true)"
  read -r started owner_host owner_pid <<<"${owner:-}" || true
  # 記録中、または同じホストで持ち主のプロセスが生きている（シミュ起動待ちなど記録前の段階）なら実行中
  if arm "pgrep -f '$ANY_RECORDERS'" > /dev/null 2>&1 < /dev/null \
     || { [ "${owner_host:-}" = "$(hostname -s 2>/dev/null || echo host)" ] && [ -n "${owner_pid:-}" ] \
          && kill -0 "$owner_pid" 2>/dev/null; }; then
    fail_env "別の run-scenario が実行中（開始 ${started:-不明}）。実行中でないと確かめられたら外す: docker exec ros2arm rm -r $LOCK"
  fi
  fail_env "前回の run-scenario のロックが残っている（開始 ${started:-不明}、記録プロセスは無い）。実行中でなければ外す: docker exec ros2arm rm -r $LOCK"
fi
# shellcheck disable=SC2317,SC2329  # trap から呼ぶ
release_lock() { arm "rm -r $LOCK" < /dev/null > /dev/null 2>&1 || true; }
trap release_lock EXIT
# 前回の記録プロセスが残っていたら止めてもらう（残るとシミュがさらに遅くなる）
if arm "pgrep -f '$ANY_RECORDERS'" > /dev/null 2>&1 < /dev/null; then
  fail_env "前回の記録プロセスが残っている。止めてから再実行する: docker exec ros2arm pkill -INT -f '$ANY_RECORDERS'"
fi

RECORDING=0     # 記録プロセスを起動したか
STOPPED=0       # 記録プロセスが止まったことを確かめたか
STOP_GAVE_UP=0  # KILL まで送っても止まらなかったか（後片付けで同じ待ちを繰り返さない）
stop_recorders() {
  # observer は ffmpeg の書き出しを最大 60 秒待つので、それより長く待ってから TERM → KILL に上げる
  [ "$RECORDING" = 1 ] && [ "$STOPPED" = 0 ] || return 0
  [ "$STOP_GAVE_UP" = 0 ] || return 1
  echo "記録を止めている（最大 2 分ほどかかる）"
  local sig
  for sig in INT TERM KILL; do
    local pattern="$RECORDERS"
    if [ "$sig" = INT ]; then pattern="$RECORDERS_INT"; fi
    arm "pkill -$sig -f '$pattern'" < /dev/null > /dev/null 2>&1 || true
    for _ in $(seq 1 "$([ "$sig" = INT ] && echo 90 || echo 10)"); do
      if ! arm "pgrep -f '$RECORDERS'" > /dev/null 2>&1 < /dev/null; then
        STOPPED=1
        return 0
      fi
      sleep 1
    done
    echo "記録プロセスが SIG$sig で止まらない" >&2
  done
  STOP_GAVE_UP=1
  return 1
}
# shellcheck disable=SC2317,SC2329  # trap から呼ぶ（shellcheck のバージョンでコードが違う）
cleanup() {
  trap '' INT   # 後片付けの途中で Ctrl-C されてもロックを外すところまで進める
  # docker exec は（TTY なしでは）シグナルを転送しないので、コンテナ内の wait は明示的に止める
  [ "$RECORDING" = 0 ] || arm "pkill -f '[s]cenario_cli wait $RUN '" < /dev/null > /dev/null 2>&1 || true
  stop_recorders || true
  release_lock
}
trap cleanup EXIT

# ros2arm の ubuntu が書けるよう、ディレクトリはコンテナ側で作る（/workspace は root と ubuntu が混在する）。
# Linux ホストではホストのユーザーが ubuntu(uid 1000) と別になりうるので、ホスト側からも書けるよう 777 にする
arm "mkdir -p $RUN && chmod 777 $RUN" < /dev/null || fail_env "出力先を作れない: $RUN_HOST"
cp "$SCENARIO_FILE" "$RUN_HOST/scenario.yaml" || fail_env "シナリオをコピーできない"
echo "出力先: $RUN_HOST"

topic_ok() {
  arm "timeout 15 ros2 topic echo --once --field header.stamp $1 > /dev/null 2>&1" < /dev/null
}
sim_ready() { topic_ok /joint_states && topic_ok /camera/color/image_raw; }

if ! sim_ready; then
  if [ "$START_SIM" = 1 ]; then
    # 別のシミュ（カメラ無しの公式 launch など）や起動途中のシミュに重ねて起動すると、Gazebo と
    # コントローラが二重になり、指令が両方に届いて判定が無意味になる
    if arm "pgrep -f '[a]rm_with_camera\.launch\.py'" > /dev/null 2>&1 < /dev/null; then
      # カメラ付きのシミュが起動途中（前回の --start-sim が待ちの上限で終わった後など）: 重ねずに待つ
      echo "カメラ付きのシミュが起動途中なので、起動はせずに待つ"
    elif arm "pgrep -f '[r]os2 launch|[g]z sim'" > /dev/null 2>&1 < /dev/null; then
      fail_env "ros2arm でカメラ無しのシミュ（README のデモなど）が動いている。止めてから --start-sim で起動し直す: docker exec ros2arm pkill -INT -f '[r]os2 launch'"
    else
      echo "シミュを起動する（初回やホストが重いときは数分〜十数分かかる）。ログ: $WORKSPACE_HOST/runs/sim-$TS.log"
      arm_bg "exec ros2 launch ros2_poc_sim arm_with_camera.launch.py placement:=fixed_front_wide > /workspace/runs/sim-$TS.log 2>&1"
    fi
  fi
  echo "トピック（/joint_states, /camera/color/image_raw）を待つ（最大 ${TIMEOUT} 秒）"
  deadline=$(( $(date +%s) + TIMEOUT ))
  until sim_ready; do
    if [ "$(date +%s)" -ge "$deadline" ]; then
      fail_env "トピックが流れない。カメラ付きのシミュ（arm_with_camera.launch.py）が動いていなければ、他のシミュを止めて --start-sim を付ける（docs/sim-scenario-recording.md）。"
    fi
    sleep 5
  done
fi

lab true < /dev/null > /dev/null || fail_env "ros2lab-a でコマンドを実行できない"
# ros2lab-a からアームのコントローラが見えるまで待つ。`ros2 topic pub -w 1` は「誰か 1 つ」の購読者で
# 送ってしまうので、Discovery が遅れている（同じネットワークの別コンテナが多いと起きる）と指令が届かない
# 購読者がちょうど 1 つであることも確かめる（シミュの二重起動や実機ドライバと混ざっていないこと）
controller_count() {
  lab "timeout 25 ros2 topic info -v --no-daemon /crane_x7_arm_controller/joint_trajectory 2>/dev/null \
       | grep -c 'Node name: crane_x7_arm_controller'" < /dev/null 2>/dev/null || true
}
controller_seen() {
  local n
  # ros2lab のログインシェルは最初にバナー行（[ros2lab] DOMAIN=...）を出すので、数値の行だけを取る
  n="$(controller_count | grep -E '^[0-9]+$' | tail -n 1 || true)"
  [ "${n:-0}" -gt 1 ] && fail_env "crane_x7_arm_controller が ${n} 個見える（シミュの二重起動か実機ドライバと混在）。シミュを止めて起動し直す"
  [ "${n:-0}" -eq 1 ]
}
deadline=$(( $(date +%s) + TIMEOUT ))
until controller_seen; do
  if [ "$(date +%s)" -ge "$deadline" ]; then
    fail_env "ros2lab-a から crane_x7_arm_controller が見えない（Discovery の問題）。数十秒待ってから再実行する。"
  fi
  echo "ros2lab-a から crane_x7_arm_controller が見えるのを待つ"
  sleep 5
done

REPEAT_ARG=""
[ -n "$REPEAT" ] && REPEAT_ARG="--repeat $REPEAT"
rc=0
arm "ros2 run ros2_poc_sim scenario_cli commands $RUN/scenario.yaml $REPEAT_ARG --steps-out $RUN/steps.json" \
  > "$RUN_HOST/commands.tsv" < /dev/null || rc=$?
if [ "$rc" = 64 ]; then
  echo "シナリオの検証に失敗した（上のメッセージ参照）" >&2
  exit 64
fi
[ "$rc" = 0 ] || fail_env "シナリオを展開できない（rc=$rc）"
# 送るコマンドは scenario.py が作る 2 種類の形に限る（YAML の値は数値に変換済み。生成側の検証が漏れても
# ここで止める）。bash -c の文字列に入るので、シェルの特殊文字が混ざったら実行しない
# 送信先・トピック・型まで固定し、メッセージ部分は二重引用符の中の数値・名前・記号だけを許す
# （引用の中なのでグロブやブレース展開も起きない）
CMD_RE_LAB='^ros2 topic pub -w 1 --times 3 -r 2 /crane_x7_arm_controller/joint_trajectory trajectory_msgs/msg/JointTrajectory "[][A-Za-z0-9_ .:{},-]+"$'
CMD_RE_SIM='^ros2 action send_goal /crane_x7_gripper_controller/gripper_cmd control_msgs/action/ParallelGripperCommand "[][A-Za-z0-9_ .:{},-]+"$'
while IFS=$'\t' read -r name target wait cmd; do
  [[ "$name" =~ ^[A-Za-z0-9_#-]{1,48}$ ]] || fail_env "想定外のステップ名なので実行しない: $name"
  [[ "$wait" =~ ^[0-9]+(\.[0-9]+)?$ ]] || fail_env "想定外の待ち秒なので実行しない: $wait"
  case "$target" in
    lab) re="$CMD_RE_LAB" ;;
    sim) re="$CMD_RE_SIM" ;;
    *) fail_env "不明な送信先なので実行しない: $target" ;;
  esac
  (export LC_ALL=C; [[ "$cmd" =~ $re ]]) || fail_env "想定外の形のコマンドなので実行しない: $cmd"
done < "$RUN_HOST/commands.tsv"
MAX_SEC="$(arm "ros2 run ros2_poc_sim scenario_cli budget $RUN" < /dev/null)" || fail_env "記録時間の上限を計算できない"
[[ "$MAX_SEC" =~ ^[0-9]+$ ]] || fail_env "記録時間の上限が数値でない: $MAX_SEC"

SIZE="$(arm "xdpyinfo -display :1 | awk '/dimensions:/{print \$2}'" < /dev/null)"
if [[ ! "$SIZE" =~ ^[0-9]+x[0-9]+$ ]]; then
  fail_env "デスクトップ（DISPLAY=:1）の大きさが取れない（${SIZE:-空}）。noVNC のデスクトップが起動しているか確認する。"
fi
# 記録プロセスは停止の指示が届かなくても MAX_SEC 秒で止まる（-t / --max-duration）
RECORDING=1
arm_bg "exec ros2 run ros2_poc_sim scenario_observer --out $RUN --max-duration $MAX_SEC > $RUN/observer.log 2>&1"
arm_bg "date +%s.%N > $RUN/desktop_t0.txt; exec ffmpeg -y -v error -f x11grab -framerate 10 -video_size $SIZE -t $MAX_SEC -i :1 -c:v libx264 -preset ultrafast -pix_fmt yuv420p $RUN/desktop.mp4 2> $RUN/desktop_ffmpeg.log"
# 記録の立ち上がり待ち: 送信前のフレームが要るので、カメラのフレームが記録され始めるまで待つ
frames_recorded() { { wc -l < "$RUN_HOST/camera_frames.csv"; } 2>/dev/null || echo 0; }
for _ in $(seq 1 60); do
  [ "$(frames_recorded)" -ge 4 ] && break
  sleep 1
done
[ "$(frames_recorded)" -ge 4 ] || fail_env "カメラのフレームが記録されない（$RUN_HOST/observer.log を確認）"

SEND_FAILED=0
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
  out="$(send "$target" "$script" < /dev/null)" || rc=$?
  stamps="$(printf '%s\n' "$out" | grep -E '^[0-9]+\.[0-9]+$' || true)"
  t_start="$(printf '%s\n' "$stamps" | sed -n 1p)"
  t_sent="$(printf '%s\n' "$stamps" | sed -n 2p)"
  if [ -z "$t_start" ] || [ -z "$t_sent" ]; then
    echo "  送信時刻が取れない（rc=$rc）" >&2
    [ "$rc" = 0 ] && rc=2
    t_start="${t_start:-0}"; t_sent="${t_sent:-$t_start}"
  fi
  if [ "$rc" != 0 ]; then
    echo "  命令が失敗した（rc=$rc）" >&2
    SEND_FAILED=$((SEND_FAILED + 1))
  fi
  # name は scenario.py で英数字・_・-（と repeat の #k）に限られるので JSON の文字列にそのまま入れてよい
  printf '{"index": %d, "name": "%s", "target": "%s", "t_start": %s, "t_sent": %s, "rc": %d}\n' \
    "$((i - 1))" "$name" "$target" "$t_start" "$t_sent" "$rc" >> "$RUN_HOST/events.jsonl"
  # 静止するまで待つ（シミュは実時間より遅いので指令時間では足りない）。wait が使えなければ目安の秒数
  arm "ros2 run ros2_poc_sim scenario_cli wait $RUN $((i - 1))" < /dev/null || sleep "$wait"
done 3< "$RUN_HOST/commands.tsv"

stop_recorders || fail_env "記録プロセスを止められないので判定しない（camera.mp4 が未完の可能性）"

judge_rc=0
arm "ros2 run ros2_poc_sim scenario_cli judge $RUN" < /dev/null || judge_rc=$?
if [ "$judge_rc" -gt 1 ] || [ ! -f "$RUN_HOST/result.json" ]; then
  fail_env "判定に失敗した（rc=$judge_rc）"
fi
# 合成の失敗は判定結果（終了コード）を変えない。result.json が判定の正
if arm "ros2 run ros2_poc_sim scenario_cli compose $RUN" < /dev/null; then
  echo "動画: $RUN_HOST/scenario.mp4"
else
  echo "合成に失敗した（$RUN_HOST/overlay/filtergraph.txt を確認）。判定は result.json を見る" >&2
fi
echo "判定: $RUN_HOST/result.json"
if [ "$SEND_FAILED" -gt 0 ]; then
  # 判定（result.json）は残すが、指令が届いていないので動作の FAIL ではなく実行時の問題として返す
  fail_env "指令を送れなかったステップが ${SEND_FAILED} 個ある（result.json の send_failed）"
fi
exit "$judge_rc"
