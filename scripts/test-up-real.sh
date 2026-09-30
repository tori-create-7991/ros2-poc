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

fail() { echo "FAIL: $*" >&2; exit 1; }

# (a) ros2arm 起動中 → 拒否（exit 1、stderr に ros2arm、compose up は呼ばれない）
export STUB_LOG="$TMP/a.log"
set +e
err="$(PATH="$TMP:$PATH" STUB_ARM_RUNNING=1 bash "$ROOT/scripts/up-real.sh" 2>&1 >/dev/null)"
rc=$?
set -e
[ "$rc" -eq 1 ] || fail "ros2arm 起動中は exit 1 のはずが $rc"
grep -q "ros2arm" <<<"$err" || fail "stderr に ros2arm が含まれない: $err"
if grep -q "compose" "$STUB_LOG"; then fail "拒否時に docker compose が呼ばれた"; fi

# (b) ros2arm 非起動 → compose up が呼ばれる
export STUB_LOG="$TMP/b.log"
PATH="$TMP:$PATH" STUB_ARM_RUNNING=0 bash "$ROOT/scripts/up-real.sh" >/dev/null
grep -q "compose --profile real up -d --build" "$STUB_LOG" || fail "compose --profile real up が呼ばれない: $(cat "$STUB_LOG")"

echo "OK: up-real.sh guard tests passed"
