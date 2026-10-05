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
[ "${1:-}" = "exec" ] || exit 0
args="$*"
if [ -n "${STUB_EXEC_FAIL:-}" ] && [[ "$args" =~ $STUB_EXEC_FAIL ]]; then
  exit "${STUB_EXEC_RC:-2}"
fi
run_dir() { [[ "$args" =~ /workspace/runs/([0-9-]+) ]] && echo "$STUB_WS/runs/${BASH_REMATCH[1]}"; }
case "$args" in
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
  ERR="$(PATH="$TMP:$PATH" STUB_RUNNING="$running" RUN_SCENARIO_WORKSPACE="$STUB_WS" \
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

# 録画・合成の前提（ffmpeg 等）が無い → 出力先も作らずに exit 2
STUB_EXEC_FAIL='scenario_cli doctor' run_case "$ALL"
[ "$RC" -eq 2 ] || fail "doctor 失敗は exit 2 のはずが $RC: $ERR"
grep -q "前提が揃っていない" <<<"$ERR" || fail "doctor: 案内が無い: $ERR"
if grep -q "mkdir -p /workspace/runs" <<<"$LOG"; then fail "doctor 失敗で出力先が作られた: $LOG"; fi

# ロックが取れない・記録プロセスが動いている → 実行中として exit 2。他人のロックは外さない
STUB_EXEC_FAIL='mkdir /tmp/run-scenario.lock' STUB_LEFTOVER=1 run_case "$ALL"
[ "$RC" -eq 2 ] || fail "実行中は exit 2 のはずが $RC: $ERR"
grep -q "別の run-scenario が実行中" <<<"$ERR" || fail "実行中: 案内が無い: $ERR"
if lock_released; then fail "実行中の他人のロックを外した: $LOG"; fi
if grep -qE "scenario_observer --out|x11grab|ros2 launch" <<<"$LOG"; then fail "実行中なのに記録・シミュが動いた: $LOG"; fi

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

# 最初の停止指示（INT）はカメラ用 ffmpeg に送らない（読み残したフレームが落ちる）。TERM / KILL では含める
run_case "$ALL"
grep "pkill -INT" <<<"$LOG" | grep -q "awvideo" && fail "INT をカメラ用 ffmpeg に送っている: $LOG"
grep "pkill -INT" <<<"$LOG" | grep -q "cenario_observer --out" || fail "INT を observer に送っていない: $LOG"

# 記録を始める前に終わったときは、記録の停止を試みない
STUB_EXEC_FAIL='scenario_cli commands' STUB_EXEC_RC=64 run_case "$ALL"
if grep -q "pkill" <<<"$LOG"; then fail "記録前の終了で pkill した: $LOG"; fi

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

echo "OK: run-scenario guard tests passed"
