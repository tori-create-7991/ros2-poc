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

echo "OK: run-scenario guard tests passed"
