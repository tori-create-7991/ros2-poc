#!/usr/bin/env bash
# up-real.sh / down-real.sh / up-arm.sh のガード分岐を docker スタブで検証する
# （実際の docker は呼ばない）。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# docker スタブ: 引数を記録し、`docker ps --filter name=^X$` は
# STUB_RUNNING（空白区切りの起動中コンテナ名）に X があるときだけ ID を返す
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
fi
exit 0
STUB
chmod +x "$TMP/docker"

# 存在するデバイス代わりのファイル
touch "$TMP/crane_x7"

fail() { echo "FAIL: $*" >&2; exit 1; }

# 引数: スクリプト名 STUB_RUNNING DEVICE STDIN [スクリプト引数...]
# stderr を ERR、終了コードを RC、docker 呼び出しログを LOG に入れる
run_case() {
  local script="$1" running="$2" device="$3" input="$4"
  shift 4
  STUB_LOG="$TMP/log.$$.$RANDOM"
  : > "$STUB_LOG"
  export STUB_LOG
  set +e
  ERR="$(printf '%s\n' "$input" | PATH="$TMP:$PATH" STUB_RUNNING="$running" CRANE_X7_DEVICE="$device" \
    DRIVER_STOP_WAIT=0 bash "$ROOT/scripts/$script" "$@" 2>&1 >/dev/null)"
  RC=$?
  set -e
  LOG="$(cat "$STUB_LOG")"
}

no_compose() { if grep -q "compose" <<<"$LOG"; then fail "$1: docker compose が呼ばれた: $LOG"; fi; }

# --- up-real.sh ---

# ros2arm 起動中 → 拒否
run_case up-real.sh "ros2arm" "$TMP/crane_x7" ""
[ "$RC" -eq 1 ] || fail "up-real: ros2arm 起動中は exit 1 のはずが $RC"
grep -q "ros2arm" <<<"$ERR" || fail "up-real: stderr に ros2arm が含まれない: $ERR"
no_compose "up-real(ros2arm)"

# デバイス無し → 拒否
run_case up-real.sh "" "$TMP/no-such-device" ""
[ "$RC" -eq 1 ] || fail "up-real: デバイス無しは exit 1 のはずが $RC"
grep -q "no-such-device" <<<"$ERR" || fail "up-real: stderr にデバイスパスが含まれない: $ERR"
no_compose "up-real(no device)"

# 正常系 → compose up。ガードの filter も検証する
run_case up-real.sh "" "$TMP/crane_x7" ""
[ "$RC" -eq 0 ] || fail "up-real: 正常系は exit 0 のはずが $RC: $ERR"
grep -qF 'name=^ros2arm$' <<<"$LOG" || fail "up-real: docker ps の name filter が無い: $LOG"
grep -q "status=running" <<<"$LOG" || fail "up-real: docker ps の status filter が無い: $LOG"
grep -q "compose --profile real up -d --build" <<<"$LOG" || fail "up-real: compose up が呼ばれない: $LOG"

# --- up-arm.sh ---

# ros2real 起動中 → 拒否
run_case up-arm.sh "ros2real" "" ""
[ "$RC" -eq 1 ] || fail "up-arm: ros2real 起動中は exit 1 のはずが $RC"
grep -q "ros2real" <<<"$ERR" || fail "up-arm: stderr に ros2real が含まれない: $ERR"
no_compose "up-arm(ros2real)"

# 正常系 → compose up
run_case up-arm.sh "" "" ""
[ "$RC" -eq 0 ] || fail "up-arm: 正常系は exit 0 のはずが $RC: $ERR"
grep -qF 'name=^ros2real$' <<<"$LOG" || fail "up-arm: docker ps の name filter が無い: $LOG"
grep -q "compose --profile arm up -d --build" <<<"$LOG" || fail "up-arm: compose up が呼ばれない: $LOG"

# --- down-real.sh ---

# ros2real 非起動 → 何もしない（exit 0、compose も exec も呼ばない）
run_case down-real.sh "" "" ""
[ "$RC" -eq 0 ] || fail "down-real: 非起動は exit 0 のはずが $RC"
no_compose "down-real(not running)"

# 確認に no → 中止（exit 1、ドライバ停止も削除もしない）
run_case down-real.sh "ros2real" "" "no"
[ "$RC" -eq 1 ] || fail "down-real: no は exit 1 のはずが $RC"
no_compose "down-real(no)"
if grep -q "exec" <<<"$LOG"; then fail "down-real(no): ドライバ停止が呼ばれた: $LOG"; fi

# 確認に yes → ドライバ停止 → コンテナ削除の順（ros2lab は止めない）
run_case down-real.sh "ros2real" "" "yes"
[ "$RC" -eq 0 ] || fail "down-real: yes は exit 0 のはずが $RC: $ERR"
grep -q "exec ros2real pkill -INT" <<<"$LOG" || fail "down-real: ドライバ停止が呼ばれない: $LOG"
grep -q "compose --profile real rm -sf ros2real" <<<"$LOG" || fail "down-real: rm が ros2real に限定されていない: $LOG"
[ "$(grep -n 'exec ros2real' <<<"$LOG" | cut -d: -f1)" -lt "$(grep -n 'rm -sf' <<<"$LOG" | cut -d: -f1)" ] || fail "down-real: ドライバ停止が rm より後"

# --yes → プロンプトなしで実行
run_case down-real.sh "ros2real" "" "" --yes
[ "$RC" -eq 0 ] || fail "down-real --yes は exit 0 のはずが $RC: $ERR"
grep -q "rm -sf ros2real" <<<"$LOG" || fail "down-real --yes: rm が呼ばれない: $LOG"

echo "OK: real-arm guard script tests passed"
