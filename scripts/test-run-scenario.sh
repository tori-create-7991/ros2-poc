#!/usr/bin/env bash
# run-scenario.sh の引数検査と起動前ガードを docker スタブで検証する（実際の docker は呼ばない）。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# docker スタブ: 引数を記録し、`docker ps --filter name=^X$` は
# STUB_RUNNING（空白区切りの起動中コンテナ名）に X があるときだけ ID を返す
cat > "$TMP/docker" <<'STUB'
#!/usr/bin/env bash
echo "$*" >> "$STUB_LOG"
# STUB_EXEC_FAIL（正規表現）に一致する docker exec は rc=2 で失敗させる
if [ "${1:-}" = "exec" ] && [ -n "${STUB_EXEC_FAIL:-}" ] && [[ "$*" =~ $STUB_EXEC_FAIL ]]; then
  exit 2
fi
if [ "${1:-}" = "ps" ]; then
  for arg in "$@"; do
    case "$arg" in
      name=^*\$) name="${arg#name=^}"; name="${name%\$}"
        for running in ${STUB_RUNNING:-}; do
          [ "$running" = "$name" ] && echo "abc123"
        done ;;
    esac
  done
fi
exit 0
STUB
chmod +x "$TMP/docker"

fail() { echo "FAIL: $*" >&2; exit 1; }

# 引数: STUB_RUNNING [スクリプト引数...]。stderr を ERR、終了コードを RC、docker 呼び出しを LOG に入れる
run_case() {
  local running="$1"
  shift
  STUB_LOG="$TMP/log.$RANDOM"
  : > "$STUB_LOG"
  export STUB_LOG
  set +e
  ERR="$(PATH="$TMP:$PATH" STUB_RUNNING="$running" bash "$ROOT/scripts/run-scenario.sh" "$@" 2>&1 >/dev/null)"
  RC=$?
  set -e
  LOG="$(cat "$STUB_LOG")"
}

no_exec() { if grep -qE "^(exec|compose)" <<<"$LOG"; then fail "$1: コンテナに命令が送られた: $LOG"; fi; }

ALL="ros2arm ros2lab-a"

# 引数の誤り → 64（docker は呼ばない）
for args in "--bogus" "--repeat 0" "--repeat 21" "--repeat x" "--timeout 1.5" "--scenario no_such" \
            "--scenario ../etc/passwd" "--scenario" "--help"; do
  # shellcheck disable=SC2086
  run_case "$ALL" $args
  [ "$RC" -eq 64 ] || fail "引数 '$args' は exit 64 のはずが $RC: $ERR"
  [ -z "$LOG" ] || fail "引数 '$args' で docker が呼ばれた: $LOG"
done

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

# 別の run-scenario が実行中（ロックが取れない）→ exit 2、記録もシミュも触らない
STUB_EXEC_FAIL='mkdir /tmp/run-scenario.lock' run_case "$ALL"
[ "$RC" -eq 2 ] || fail "ロック取得失敗は exit 2 のはずが $RC: $ERR"
grep -q "別の run-scenario が実行中" <<<"$ERR" || fail "ロック: 案内が無い: $ERR"
if grep -qE "scenario_observer|x11grab -t|ros2 launch" <<<"$LOG"; then fail "ロック失敗で記録・シミュが動いた: $LOG"; fi

# 送信前のコマンド検査: scenario.py が作る形は通し、シェルの特殊文字が混ざったものは止める
CMD_RE="$(sed -n "s/^CMD_RE='\(.*\)'$/\1/p" "$ROOT/scripts/run-scenario.sh")"
[ -n "$CMD_RE" ] || fail "run-scenario.sh から CMD_RE を取り出せない"
ok_cmds=(
  'ros2 topic pub -w 1 --times 3 -r 2 /crane_x7_arm_controller/joint_trajectory trajectory_msgs/msg/JointTrajectory "{joint_names: [crane_x7_shoulder_fixed_part_pan_joint, crane_x7_wrist_joint], points: [{positions: [0.5, -1.2], time_from_start: {sec: 1, nanosec: 500000000}}]}"'
  'ros2 action send_goal /crane_x7_gripper_controller/gripper_cmd control_msgs/action/ParallelGripperCommand "{command: {name: [crane_x7_gripper_finger_a_joint], position: [1.047198]}}"'
)
for c in "${ok_cmds[@]}"; do [[ "$c" =~ $CMD_RE ]] || fail "正しいコマンドが検査で止まる: $c"; done
# shellcheck disable=SC2016  # 展開させない文字列そのものを検査する
bad_cmds=('ros2 topic pub /x std_msgs/msg/String "{data: a}"; rm -rf /' 'ros2 topic pub /x "$(id)"' 'ros2 topic pub /x `id`'
          'ros2 run demo_nodes_cpp talker' "ros2 topic pub /x 'a'" 'ros2 topic pub /x a | sh' 'ros2 topic pub /x a > /tmp/x')
for c in "${bad_cmds[@]}"; do if [[ "$c" =~ $CMD_RE ]]; then fail "危険なコマンドが検査を通る: $c"; fi; done

echo "OK: run-scenario guard tests passed"
