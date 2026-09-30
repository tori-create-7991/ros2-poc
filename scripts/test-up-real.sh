#!/usr/bin/env bash
# up-real.sh のガード分岐を docker スタブで検証する（実際の docker は呼ばない）。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# docker スタブ: 引数を記録し、`docker ps` は STUB_ARM_RUNNING が 1 のときだけ ID を返す
cat > "$TMP/docker" <<'STUB'
#!/usr/bin/env bash
echo "$*" >> "$STUB_LOG"
if [ "${1:-}" = "ps" ] && [ "${STUB_ARM_RUNNING:-0}" = "1" ]; then
  echo "abc123"
fi
STUB
chmod +x "$TMP/docker"

# 存在するデバイス代わりのファイル
touch "$TMP/crane_x7"

fail() { echo "FAIL: $*" >&2; exit 1; }

# 引数: ARM_RUNNING DEVICE。stderr を ERR、終了コードを RC、docker 呼び出しログを LOG に入れる
run_case() {
  STUB_LOG="$TMP/log.$1.$$"
  : > "$STUB_LOG"
  export STUB_LOG
  set +e
  ERR="$(PATH="$TMP:$PATH" STUB_ARM_RUNNING="$1" CRANE_X7_DEVICE="$2" bash "$ROOT/scripts/up-real.sh" 2>&1 >/dev/null)"
  RC=$?
  set -e
  LOG="$(cat "$STUB_LOG")"
}

# (a) ros2arm 起動中 → 拒否（exit 1、stderr に ros2arm、compose up は呼ばれない）
run_case 1 "$TMP/crane_x7"
[ "$RC" -eq 1 ] || fail "ros2arm 起動中は exit 1 のはずが $RC"
grep -q "ros2arm" <<<"$ERR" || fail "stderr に ros2arm が含まれない: $ERR"
if grep -q "compose" <<<"$LOG"; then fail "拒否時に docker compose が呼ばれた"; fi

# (b) デバイス無し → 拒否（exit 1、stderr にデバイスパス、compose up は呼ばれない）
run_case 0 "$TMP/no-such-device"
[ "$RC" -eq 1 ] || fail "デバイス無しは exit 1 のはずが $RC"
grep -q "no-such-device" <<<"$ERR" || fail "stderr にデバイスパスが含まれない: $ERR"
if grep -q "compose" <<<"$LOG"; then fail "デバイス無しで docker compose が呼ばれた"; fi

# (c) ros2arm 非起動 + デバイス有り → compose up が呼ばれる。ガードの filter も検証する
run_case 0 "$TMP/crane_x7"
[ "$RC" -eq 0 ] || fail "正常系は exit 0 のはずが $RC: $ERR"
grep -qF 'name=^ros2arm$' <<<"$LOG" || fail "docker ps の name filter が無い: $LOG"
grep -q "status=running" <<<"$LOG" || fail "docker ps の status filter が無い: $LOG"
grep -q "compose --profile real up -d --build" <<<"$LOG" || fail "compose --profile real up が呼ばれない: $LOG"

echo "OK: up-real.sh guard tests passed"
