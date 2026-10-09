#!/usr/bin/env bash
# run-scenario.sh の引数検査・起動前ガード・終了コードの経路を docker スタブで検証する（実際の docker は呼ばない）。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# docker スタブ。呼び出しを STUB_LOG に記録し、コンテナ内の動作をまねる:
#   docker ps --filter name=^X$  STUB_RUNNING（空白区切り）に X があれば ID を返す
#   docker exec ...              STUB_EXEC_FAIL（正規表現）に一致すれば STUB_EXEC_RC（既定 2）で失敗。
#                                pgrep は既定で「プロセス無し」。STUB_LEFTOVER=1 なら起動前から有り、
#                                STUB_STUCK=1 なら記録の起動後ずっと有り（止まらない）
#   test -d /tmp/run-vla.lock    STUB_VLA_LOCK=1 のとき「ある」（ros2server が起動中のときだけ見に行く）
#   docker inspect（ros2poc.env）  ros2lab-a は STUB_LAB_ENV、ros2arm は STUB_ARM_ENV を返す（未設定なら a、空文字なら空 = ラベル導入前）
#   /workspace/runs/<ts>         $STUB_WS/runs/<ts> に対応させ、記録・判定の成果物を置く
cat > "$TMP/docker" <<'STUB'
#!/usr/bin/env bash
echo "$*" >> "$STUB_LOG"
if [ "${1:-}" = "ps" ]; then
  for arg in "$@"; do
    case "$arg" in
      name=^*\$) name="${arg#name=^}"; name="${name%\$}"
        for running in ${STUB_RUNNING:-}; do
          [ "$running" = "$name" ] && echo "abc123"
        done ;;
    esac
  done
  exit 0
fi
if [ "${1:-}" = "info" ]; then
  [ -n "${STUB_DOCKER_INFO:-}" ] && echo "$STUB_DOCKER_INFO"
  exit 0
fi
if [ "${1:-}" = "inspect" ]; then
  if [[ "$*" == *ros2poc.env* ]]; then
    case "$*" in
      *" ros2arm") echo "${STUB_ARM_ENV-a}" ;;
      *) echo "${STUB_LAB_ENV-a}" ;;
    esac
  fi
  exit 0
fi
[ "${1:-}" = "exec" ] || exit 0
args="$*"
# ros2lab のログインシェル（bash -lc）は実物と同じくバナーを先に出す
[[ "$args" == "exec ros2lab-a bash -lc "* ]] && echo "[ros2lab] DOMAIN=42 discovery=SUBNET"
if [ -n "${STUB_EXEC_FAIL:-}" ] && [[ "$args" =~ $STUB_EXEC_FAIL ]]; then
  exit "${STUB_EXEC_RC:-2}"
fi
run_dir() { [[ "$args" =~ /workspace/runs/([0-9-]+) ]] && echo "$STUB_WS/runs/${BASH_REMATCH[1]}"; }
case "$args" in
  *"pgrep -f '[a]rm_with_camera"*) [ "${STUB_SIM_RUNNING:-0}" = camera ] && exit 0; exit 1 ;;
  *"pgrep -f '[g]z sim gui"*) [ "${STUB_GZ_GUI:-0}" = 1 ] && exit 0; exit 1 ;;
  *"pgrep -f '[r]os2 launch"*) [ "${STUB_SIM_RUNNING:-0}" = other ] && exit 0; exit 1 ;;
  *"grep -c 'Node name"*) echo "${STUB_CONTROLLERS:-1}" ;;
  *"mkdir /tmp/run-scenario.lock"*)
    # 取得の途中（コンテナ側では済んでいる）にシグナルが来る状況を作る。待っている印を置いて、signal まで待つ
    if [ -n "${STUB_HANG_ARM:-}" ]; then touch "$STUB_WS/waiting.arm"; sleep "$STUB_HANG_ARM"; fi
    exit 0 ;;
  *"test -d /tmp/run-vla.lock"*) [ "${STUB_VLA_LOCK:-0}" = 1 ] && exit 0; exit 1 ;;
  *"cat /tmp/run-scenario.lock/owner"*) echo "${STUB_OWNER:-}" ;;
  *pgrep*)
    if [ "${STUB_LEFTOVER:-0}" = 1 ]; then exit 0; fi
    if [ "${STUB_STUCK:-0}" = 1 ] && [ -f "$STUB_WS/started" ]; then exit 0; fi
    exit 1 ;;
  *"mkdir -p /workspace/runs/"*) mkdir -p "$(run_dir)" ;;
  *"scenario_cli commands"*)
    default='a\tlab\t3.0\tros2 topic pub -w 1 --times 3 -r 2 /crane_x7_arm_controller/joint_trajectory trajectory_msgs/msg/JointTrajectory "{joint_names: [j], points: [{positions: [0.5], time_from_start: {sec: 3, nanosec: 0}}]}"\n'
    printf '%b' "${STUB_TSV:-$default}" ;;
  *"scenario_cli budget"*) echo 600 ;;
  *xdpyinfo*) echo 1920x1080 ;;
  *"scenario_observer --out"*) touch "$STUB_WS/started"; printf 'n,t\n0,1\n1,2\n2,3\n3,4\n' > "$(run_dir)/camera_frames.csv" ;;
  *"date +%s.%N; timeout 30"*) printf '100.0\n101.0\n' ;;
  *"scenario_cli judge"*) rc="${STUB_JUDGE_RC:-0}"; [ "$rc" -le 1 ] && echo '{}' > "$(run_dir)/result.json"; exit "$rc" ;;
esac
exit 0
STUB
chmod +x "$TMP/docker"

fail() { echo "FAIL: $*" >&2; exit 1; }

# 引数: STUB_RUNNING [スクリプト引数...]。stderr を ERR、終了コードを RC、docker 呼び出しを LOG に入れる
run_case() {
  local running="$1"
  shift
  STUB_LOG="$TMP/log.$RANDOM"
  STUB_WS="$TMP/ws.$RANDOM"
  mkdir -p "$STUB_WS/runs"
  : > "$STUB_LOG"
  export STUB_LOG STUB_WS
  set +e
  ERR="$(cd "${RUN_CWD:-$ROOT}" && PATH="$TMP:$PATH" STUB_RUNNING="$running" RUN_SCENARIO_WORKSPACE="$STUB_WS" \
    bash "$ROOT/scripts/run-scenario.sh" "$@" 2>&1 >/dev/null)"
  RC=$?
  set -e
  LOG="$(cat "$STUB_LOG")"
}

no_exec() { if grep -qE "^(exec|compose)" <<<"$LOG"; then fail "$1: コンテナに命令が送られた: $LOG"; fi; }
sent() { grep -q "date +%s.%N; timeout 30" <<<"$LOG"; }
lock_released() { grep -q "rm -r /tmp/run-scenario.lock" <<<"$LOG"; }

ALL="ros2arm ros2lab-a"

# --- 引数の誤り → 64（docker は呼ばない）
for args in "--bogus" "--repeat 0" "--repeat 21" "--repeat x" "--timeout 1.5" "--scenario no_such" \
            "--scenario ../etc/passwd" "--scenario" "--help"; do
  # shellcheck disable=SC2086
  run_case "$ALL" $args
  [ "$RC" -eq 64 ] || fail "引数 '$args' は exit 64 のはずが $RC: $ERR"
  [ -z "$LOG" ] || fail "引数 '$args' で docker が呼ばれた: $LOG"
done

# RUN_LABEL の不正（空白・スラッシュ・長すぎ）→ 64（docker は呼ばない）
for bad in "a b" "../x" 'a"b' "$(printf 'x%.0s' $(seq 1 33))"; do
  RUN_LABEL="$bad" run_case "$ALL"
  [ "$RC" -eq 64 ] || fail "RUN_LABEL '$bad' は exit 64 のはずが $RC: $ERR"
  [ -z "$LOG" ] || fail "RUN_LABEL '$bad' で docker が呼ばれた: $LOG"
done

# --- 起動前ガード
# ros2real（実機ドライバ）起動中 → 拒否
run_case "ros2real $ALL"
[ "$RC" -eq 2 ] || fail "ros2real 起動中は exit 2 のはずが $RC"
grep -q "down-real" <<<"$ERR" || fail "ros2real: stderr に down-real の案内が無い: $ERR"
grep -qF 'name=^ros2real$' <<<"$LOG" || fail "ros2real: docker ps の name filter が無い: $LOG"
grep -q "status=running" <<<"$LOG" || fail "ros2real: docker ps の status filter が無い: $LOG"
no_exec "ros2real"

# ros2arm / ros2lab-a が起動していない → up-arm.sh を案内して終了
for missing in ros2arm ros2lab-a; do
  run_case "${ALL//$missing/}"
  [ "$RC" -eq 2 ] || fail "$missing 未起動は exit 2 のはずが $RC"
  grep -q "$missing" <<<"$ERR" || fail "$missing: stderr にコンテナ名が無い: $ERR"
  grep -q "up-arm.sh" <<<"$ERR" || fail "$missing: up-arm.sh の案内が無い: $ERR"
  no_exec "$missing 未起動"
done

# ros2lab-a / ros2arm のどちらかが SROS2 環境 b / c → 指令が届かないので、up-env.sh a --arm を案内して exit 2
for env in b c; do
  STUB_LAB_ENV="$env" run_case "$ALL"
  [ "$RC" -eq 2 ] || fail "ros2lab-a 環境 $env は exit 2 のはずが $RC: $ERR"
  grep -q "ros2lab-a が SROS2 環境 $env" <<<"$ERR" || fail "ros2lab-a 環境 $env: stderr に環境名が無い: $ERR"
  grep -q "up-env.sh a --arm" <<<"$ERR" || fail "ros2lab-a 環境 $env: up-env.sh a --arm の案内が無い: $ERR"
  grep -q "^inspect .*ros2poc.env.* ros2lab-a$" <<<"$LOG" || fail "環境 $env: ros2lab-a のラベルを見ていない: $LOG"
  no_exec "ros2lab-a 環境 $env"
  STUB_ARM_ENV="$env" run_case "$ALL"
  [ "$RC" -eq 2 ] || fail "ros2arm 環境 $env は exit 2 のはずが $RC: $ERR"
  grep -q "ros2arm が SROS2 環境 $env" <<<"$ERR" || fail "ros2arm 環境 $env: stderr に環境名が無い: $ERR"
  grep -q "^inspect .*ros2poc.env.* ros2arm$" <<<"$LOG" || fail "環境 $env: ros2arm のラベルを見ていない: $LOG"
  no_exec "ros2arm 環境 $env"
done

# 環境 a・ラベル無し（空 / <no value>）は従来どおり進む
for env in a "" "<no value>"; do
  STUB_LAB_ENV="$env" STUB_ARM_ENV="$env" run_case "$ALL"
  [ "$RC" -eq 0 ] || fail "環境 '$env' は従来どおり exit 0 のはずが $RC: $ERR"
  sent || fail "環境 '$env' で指令が送られていない: $LOG"
done

# 録画・合成の前提（ffmpeg 等）が無い → 出力先も作らずに exit 2
STUB_EXEC_FAIL='scenario_cli doctor' run_case "$ALL"
[ "$RC" -eq 2 ] || fail "doctor 失敗は exit 2 のはずが $RC: $ERR"
grep -q "前提が揃っていない" <<<"$ERR" || fail "doctor: 案内が無い: $ERR"
if grep -q "mkdir -p /workspace/runs" <<<"$LOG"; then fail "doctor 失敗で出力先が作られた: $LOG"; fi

# run-vla が動いている（ros2server の /tmp/run-vla.lock あり）→ 同じアームに指令が混ざるので断る（排他は双方向）。
# 先に自分のロックを取ってから見る（set → check。run-vla も同じ順なので、同時に始まっても両方が通ることはない）。
# 断るときは取った自分のロックを外す。記録は始めない。ros2server が起動していなければ見に行かない
STUB_VLA_LOCK=1 run_case "$ALL ros2server"
[ "$RC" -eq 2 ] || fail "run-vla 実行中は exit 2 のはずが $RC: $ERR"
grep -q "run-vla が実行中" <<<"$ERR" || fail "run-vla 実行中: 案内が無い: $ERR"
grep -q "docker exec ros2server rm -r /tmp/run-vla.lock" <<<"$ERR" || fail "run-vla 実行中: 強制終了の残りを外す案内が無い: $ERR"
grep -q "^exec ros2server test -d /tmp/run-vla.lock" <<<"$LOG" || fail "run-vla 実行中: ロックを見に行っていない: $LOG"
grep -q "mkdir /tmp/run-scenario.lock" <<<"$LOG" || fail "run-vla 実行中: 先に自分のロックを取るはず: $LOG"
lock_released || fail "run-vla 実行中: 取った自分のロックを外していない: $LOG"
if grep -qE "scenario_observer --out|x11grab" <<<"$LOG"; then fail "run-vla 実行中なのに記録が動いた: $LOG"; fi
STUB_VLA_LOCK=1 run_case "$ALL"
if grep -q "run-vla.lock" <<<"$LOG"; then fail "ros2server が無いのに run-vla のロックを見に行った: $LOG"; fi
STUB_VLA_LOCK=0 run_case "$ALL ros2server"
[ "$RC" -eq 0 ] || fail "run-vla が動いていなければ実行できるはずが $RC: $ERR"
sent || fail "run-vla が動いていないのに指令が送られない"

# ロックの取得の途中（docker exec の往復中）に TERM が来た場合: bash はフォアグラウンドのコマンドが終わってからトラップを実行する。
# そのとき取得はコンテナ側で済んでいるので、トラップが取得の前に入っていて、所有者を照合して外す（外さないとロックが漏れる）
hang_ws="$TMP/hang.$RANDOM"
mkdir -p "$hang_ws"
STUB_LOG="$TMP/log.hang"
: > "$STUB_LOG"
export STUB_LOG
( cd "$ROOT" && PATH="$TMP:$PATH" STUB_RUNNING="$ALL" STUB_WS="$hang_ws" RUN_SCENARIO_WORKSPACE="$hang_ws" STUB_HANG_ARM=3 \
    exec bash "$ROOT/scripts/run-scenario.sh" > /dev/null 2>&1 ) &
hang_pid=$!
for _ in $(seq 1 200); do
  [ -f "$hang_ws/waiting.arm" ] && break
  sleep 0.1
done
[ -f "$hang_ws/waiting.arm" ] || fail "取得の途中の状態を作れなかった"
kill -TERM "$hang_pid" 2>/dev/null || true
wait "$hang_pid" 2>/dev/null || true
LOG="$(cat "$STUB_LOG")"
grep -q "grep -qxF .* /tmp/run-scenario.lock/owner && rm -r /tmp/run-scenario.lock" <<<"$LOG" \
  || fail "取得の途中の TERM で、所有者を照合してロックを外していない: $LOG"
if grep -qE "scenario_observer --out|x11grab" <<<"$LOG"; then fail "取得の途中で止めたのに記録が動いた: $LOG"; fi

# ロックが取れない・記録プロセスが動いている → 実行中として exit 2。他人のロックは外さない
STUB_EXEC_FAIL='mkdir /tmp/run-scenario.lock' STUB_LEFTOVER=1 run_case "$ALL"
[ "$RC" -eq 2 ] || fail "実行中は exit 2 のはずが $RC: $ERR"
grep -q "別の run-scenario が実行中" <<<"$ERR" || fail "実行中: 案内が無い: $ERR"
if lock_released; then fail "実行中の他人のロックを外した: $LOG"; fi
if grep -qE "scenario_observer --out|x11grab|ros2 launch" <<<"$LOG"; then fail "実行中なのに記録・シミュが動いた: $LOG"; fi

# ロックが取れない・同じホストで持ち主のプロセスが生きている（記録前の段階）→ 実行中として exit 2
STUB_EXEC_FAIL='mkdir /tmp/run-scenario.lock' STUB_OWNER="20990101-000000 $(hostname -s 2>/dev/null || echo host) $$" run_case "$ALL"
[ "$RC" -eq 2 ] || fail "持ち主が生きているロックは exit 2 のはずが $RC: $ERR"
grep -q "別の run-scenario が実行中（開始 20990101-000000）" <<<"$ERR" || fail "持ち主が生きている: 実行中と案内しない: $ERR"

# ロックが取れない・記録プロセスは無い → 取り残しとして外し方を案内（自分では外さない）
STUB_EXEC_FAIL='mkdir /tmp/run-scenario.lock' run_case "$ALL"
[ "$RC" -eq 2 ] || fail "取り残しのロックは exit 2 のはずが $RC: $ERR"
grep -q "ロックが残っている" <<<"$ERR" || fail "取り残し: 案内が無い: $ERR"
if lock_released; then fail "取り残しのロックを勝手に外した: $LOG"; fi

# 前回の記録プロセスが残っている → exit 2。自分のロックは外す
STUB_LEFTOVER=1 run_case "$ALL"
[ "$RC" -eq 2 ] || fail "残留は exit 2 のはずが $RC: $ERR"
grep -q "前回の記録プロセスが残っている" <<<"$ERR" || fail "残留: 案内が無い: $ERR"
lock_released || fail "残留で終わったのに自分のロックを外していない: $LOG"
if grep -qE "scenario_observer --out|x11grab" <<<"$LOG"; then fail "残留なのに記録を始めた: $LOG"; fi

# --start-sim でも、別のシミュ（カメラ無しなど）が動いていれば重ねて起動しない
STUB_EXEC_FAIL='ros2 topic echo' STUB_SIM_RUNNING=other run_case "$ALL" --start-sim
[ "$RC" -eq 2 ] || fail "カメラ無しのシミュが動いているときは exit 2 のはずが $RC: $ERR"
grep -q "カメラ無しのシミュ" <<<"$ERR" || fail "カメラ無しのシミュ: 案内が無い: $ERR"
if grep -q "ros2 launch ros2_poc_sim" <<<"$LOG"; then fail "別のシミュに重ねて起動した: $LOG"; fi

# カメラ付きのシミュが起動途中なら、重ねて起動せずに待つ（トピックが来ないままなら時間切れで 2）
STUB_EXEC_FAIL='ros2 topic echo' STUB_SIM_RUNNING=camera run_case "$ALL" --start-sim --timeout 1
[ "$RC" -eq 2 ] || fail "起動途中で時間切れは exit 2 のはずが $RC: $ERR"
grep -q "トピックが流れない" <<<"$ERR" || fail "起動途中: 待たずに止めた: $ERR"
if grep -q "ros2 launch ros2_poc_sim" <<<"$LOG"; then fail "起動途中のシミュに重ねて起動した: $LOG"; fi

# コントローラが 2 つ見える（二重起動・実機と混在）→ 送信せずに exit 2
STUB_CONTROLLERS=2 run_case "$ALL"
[ "$RC" -eq 2 ] || fail "コントローラ 2 つは exit 2 のはずが $RC: $ERR"
grep -q "2 個見える" <<<"$ERR" || fail "コントローラ 2 つ: 案内が無い: $ERR"
if sent; then fail "コントローラ 2 つで送信した: $LOG"; fi

# --scenario の相対パスは呼び出し元のディレクトリから解決する
mkdir -p "$TMP/caller"
printf 'steps:\n  - {name: a, positions: [0, 0, 0, 0, 0, 0, 0]}\n' > "$TMP/caller/my.yaml"
RUN_CWD="$TMP/caller" run_case "$ALL" --scenario my.yaml
[ "$RC" -eq 0 ] || fail "呼び出し元からの相対パスが解決されない: $RC: $ERR"
RUN_CWD="$TMP" run_case "$ALL" --scenario my.yaml
[ "$RC" -eq 64 ] || fail "別のディレクトリからは見つからないはずが $RC: $ERR"

# --- シナリオの展開と送信前の検査
# commands が 64（シナリオの誤り）→ 64、それ以外の失敗 → 2
STUB_EXEC_FAIL='scenario_cli commands' STUB_EXEC_RC=64 run_case "$ALL"
[ "$RC" -eq 64 ] || fail "commands 64 は exit 64 のはずが $RC: $ERR"
STUB_EXEC_FAIL='scenario_cli commands' STUB_EXEC_RC=1 run_case "$ALL"
[ "$RC" -eq 2 ] || fail "commands の想定外の失敗は exit 2 のはずが $RC: $ERR"

# 不明な送信先・想定外の形のコマンド → 送信も記録もせずに exit 2
for tsv in 'a\tother\t3.0\tros2 topic pub -w 1 --times 3 -r 2 /crane_x7_arm_controller/joint_trajectory trajectory_msgs/msg/JointTrajectory "{a: [1]}"\n' \
           'a\tlab\t3.0\tros2 topic pub -w 1 --times 3 -r 2 /other trajectory_msgs/msg/JointTrajectory "{a: [1]}"\n' \
           'a\tlab\t3.0\tros2 topic pub -w 1 --times 3 -r 2 /crane_x7_arm_controller/joint_trajectory trajectory_msgs/msg/JointTrajectory "{a: [1]}"; id\n'; do
  STUB_TSV="$tsv" run_case "$ALL"
  [ "$RC" -eq 2 ] || fail "不正な commands.tsv は exit 2 のはずが $RC: $tsv / $ERR"
  if sent || grep -q "scenario_observer --out" <<<"$LOG"; then fail "不正な commands.tsv で送信・記録した: $tsv"; fi
done

# --- 送信後の経路
# 正常 → judge の rc（0）で終わり、ロックを外す
run_case "$ALL"
[ "$RC" -eq 0 ] || fail "正常系は exit 0 のはずが $RC: $ERR"
sent || fail "正常系で指令が送られていない: $LOG"
grep -q "^exec ros2lab-a bash -lc date" <<<"$LOG" || fail "アームの指令が ros2lab-a から送られていない: $LOG"
lock_released || fail "正常系でロックを外していない: $LOG"

# グリッパ（sim）の指令は ros2arm から送る
STUB_TSV='g\tsim\t0.0\tros2 action send_goal /crane_x7_gripper_controller/gripper_cmd control_msgs/action/ParallelGripperCommand "{command: {name: [crane_x7_gripper_finger_a_joint], position: [1.047198]}}"\n' run_case "$ALL"
[ "$RC" -eq 0 ] || fail "グリッパの正常系は exit 0 のはずが $RC: $ERR"
grep -q "ros2arm bash -c .*date +%s.%N; timeout 30 ros2 action send_goal" <<<"$LOG" || fail "グリッパの指令が ros2arm から送られていない: $LOG"

# --light: GUI なしのシミュで実行する（RViz は止める。GUI だけを止めるとシミュ全体が落ちるので gz sim gui は止めない）
run_case "$ALL" --light
[ "$RC" -eq 0 ] || fail "--light の正常系は exit 0 のはずが $RC: $ERR"
grep -q "pkill -x rviz2" <<<"$LOG" || fail "--light で RViz を止めていない: $LOG"
if grep -E "pkill.*gz sim" <<<"$LOG"; then fail "--light で Gazebo を止めた（親ごと落ちる）: $LOG"; fi
run_case "$ALL"
if grep -q "pkill -x rviz2" <<<"$LOG"; then fail "--light 無しで RViz を止めた: $LOG"; fi
# --light で GUI 付きのシミュが動いている → 止めずに案内して exit 2（送信しない）
STUB_GZ_GUI=1 run_case "$ALL" --light
[ "$RC" -eq 2 ] || fail "--light で GUI 付きのシミュが動いているときは exit 2 のはずが $RC: $ERR"
grep -q "start-sim --light" <<<"$ERR" || fail "--light: 起動し直しの案内が無い: $ERR"
if sent; then fail "--light の拒否で送信した: $LOG"; fi
# --start-sim --light は GUI なしの launch を起動し、付けなければ GUI 付き
STUB_EXEC_FAIL='ros2 topic echo' run_case "$ALL" --start-sim --light --timeout 1
grep -q "ros2 launch ros2_poc_sim arm_with_camera_headless.launch.py" <<<"$LOG" || fail "--start-sim --light が headless の launch を起動していない: $LOG"
STUB_EXEC_FAIL='ros2 topic echo' run_case "$ALL" --start-sim --timeout 1
grep -q "ros2 launch ros2_poc_sim arm_with_camera.launch.py" <<<"$LOG" || fail "--start-sim が通常の launch を起動していない: $LOG"
if grep -q "arm_with_camera_headless" <<<"$LOG"; then fail "--light 無しで headless の launch を起動した: $LOG"; fi

# 最初の停止指示（INT）はカメラ用 ffmpeg に送らない（読み残したフレームが落ちる）。TERM / KILL では含める
run_case "$ALL"
grep "pkill -INT" <<<"$LOG" | grep -q "awvideo" && fail "INT をカメラ用 ffmpeg に送っている: $LOG"
grep "pkill -INT" <<<"$LOG" | grep -q "cenario_observer --out" || fail "INT を observer に送っていない: $LOG"

# 記録を始める前に終わったときは、記録の停止を試みない
STUB_EXEC_FAIL='scenario_cli commands' STUB_EXEC_RC=64 run_case "$ALL"
if grep -q "pkill" <<<"$LOG"; then fail "記録前の終了で pkill した: $LOG"; fi

# 指令を送れなかったステップがある → judge が PASS でも実行時の問題として exit 2（result.json は残す）
STUB_EXEC_FAIL='date \+%s\.%N; timeout 30' STUB_EXEC_RC=125 run_case "$ALL"
[ "$RC" -eq 2 ] || fail "送信失敗は exit 2 のはずが $RC: $ERR"
grep -q "指令を送れなかったステップが 1 個" <<<"$ERR" || fail "送信失敗: 案内が無い: $ERR"
grep -q "scenario_cli judge" <<<"$LOG" || fail "送信失敗でも判定（result.json）は残すはず: $LOG"

# 不正なステップ名・待ち秒 → 送信せずに exit 2
for tsv in 'a b\tlab\t3.0\tros2 topic pub -w 1 --times 3 -r 2 /crane_x7_arm_controller/joint_trajectory trajectory_msgs/msg/JointTrajectory "{a: [1]}"\n' \
           'a\tlab\t3;id\tros2 topic pub -w 1 --times 3 -r 2 /crane_x7_arm_controller/joint_trajectory trajectory_msgs/msg/JointTrajectory "{a: [1]}"\n'; do
  STUB_TSV="$tsv" run_case "$ALL"
  [ "$RC" -eq 2 ] || fail "不正な名前・待ち秒は exit 2 のはずが $RC: $tsv"
  if sent; then fail "不正な名前・待ち秒で送信した: $tsv"; fi
done

# FAIL あり（judge 1）→ 1
STUB_JUDGE_RC=1 run_case "$ALL"
[ "$RC" -eq 1 ] || fail "judge 1 は exit 1 のはずが $RC: $ERR"

# 記録の欠損など判定できない（judge 2 以上）→ 2
STUB_JUDGE_RC=2 run_case "$ALL"
[ "$RC" -eq 2 ] || fail "judge 2 は exit 2 のはずが $RC: $ERR"

# 記録プロセスが止まらない → 判定せずに exit 2、ロックは外す（停止の待ちを短くするため seq を差し替える）
cat > "$TMP/seq" <<'SEQ'
#!/usr/bin/env bash
echo 1
SEQ
chmod +x "$TMP/seq"
STUB_STUCK=1 run_case "$ALL"
[ "$RC" -eq 2 ] || fail "記録が止まらないときは exit 2 のはずが $RC: $ERR"
if grep -q "scenario_cli judge" <<<"$LOG"; then fail "記録が止まらないのに判定した: $LOG"; fi
grep -q "pkill -KILL" <<<"$LOG" || fail "KILL まで上げていない: $LOG"
grep "pkill -KILL" <<<"$LOG" | grep -q "awvideo" || fail "KILL でカメラ用 ffmpeg を含めていない: $LOG"
lock_released || fail "記録が止まらないときもロックは外す: $LOG"
[ "$(grep -c "pkill -KILL" <<<"$LOG")" -eq 1 ] || fail "止められなかった後に停止を繰り返した: $LOG"
rm "$TMP/seq"

# RUN_LABEL と実行環境が run_meta.json に残る（docker info が取れないスタブでは null）
RUN_LABEL=light run_case "$ALL"
meta="$(cat "$STUB_WS"/runs/*/run_meta.json)" || fail "run_meta.json が無い: $ERR"
grep -q '"label": "light"' <<<"$meta" || fail "run_meta.json に label が無い: $meta"
grep -q '"cpus": null' <<<"$meta" || fail "docker info 不可なら cpus は null のはず: $meta"
python3 -c 'import json,sys; json.loads(sys.argv[1])' "$meta" || fail "run_meta.json が JSON でない: $meta"
STUB_DOCKER_INFO="6 12884901888" run_case "$ALL"
meta="$(cat "$STUB_WS"/runs/*/run_meta.json)"
grep -q '"scenario": "default"' <<<"$meta" || fail "scenario 名が default にならない: $meta"
grep -q '"cpus": 6, "mem_gib": 12' <<<"$meta" || fail "docker info の実値が run_meta.json に入らない: $meta"
# シナリオ名に " や \ があっても run_meta.json は壊れない
printf 'steps: []\n' > "$TMP/we\"ird.yaml"
run_case "$ALL" --scenario "$TMP/we\"ird.yaml"
python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$STUB_WS"/runs/*/run_meta.json || fail "シナリオ名で run_meta.json が壊れた"
run_case "$ALL"
grep -q '"label": ""' "$STUB_WS"/runs/*/run_meta.json || fail "RUN_LABEL 未指定は空のラベル: $(cat "$STUB_WS"/runs/*/run_meta.json)"

echo "OK: run-scenario guard tests passed"
