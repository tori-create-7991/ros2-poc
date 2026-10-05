#!/usr/bin/env bash
# up-env.sh のガード分岐と compose への渡し方を docker スタブで検証する
# （実際の docker は呼ばない）。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# docker スタブ: 引数と、compose 呼び出し時の環境変数を記録する。
# `docker ps --filter name=^X$` は STUB_RUNNING（空白区切り）に X があるときだけ ID を返す
cat > "$TMP/docker" <<'STUB'
#!/usr/bin/env bash
echo "$*" >> "$STUB_LOG"
if [ "${1:-}" = "compose" ]; then
  echo "ENV SROS2_ENV=${SROS2_ENV:-} SROS2_STRATEGY=${SROS2_STRATEGY:-}" >> "$STUB_LOG"
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

# 生成済みの keystore の代わり（b と c だけ作る）
mkdir -p "$TMP/keystores/b" "$TMP/keystores/c"

fail() { echo "FAIL: $*" >&2; exit 1; }

# 引数: STUB_RUNNING KEYSTORE_ROOT [up-env.sh の引数...]
# stderr を ERR、終了コードを RC、docker 呼び出しログを LOG に入れる
run_case() {
  local running="$1" ks_root="$2"
  shift 2
  STUB_LOG="$TMP/log.$$.$RANDOM"
  : > "$STUB_LOG"
  export STUB_LOG
  set +e
  ERR="$(PATH="$TMP:$PATH" STUB_RUNNING="$running" SROS2_KEYSTORE_ROOT="$ks_root" \
    bash "$ROOT/scripts/up-env.sh" "$@" 2>&1 >/dev/null)"
  RC=$?
  set -e
  LOG="$(cat "$STUB_LOG")"
}

no_compose() { if grep -q "compose" <<<"$LOG"; then fail "$1: docker compose が呼ばれた: $LOG"; fi; }

# (a) a → override なし。SROS2_ENV=a。keystore は要らない
run_case "" "$TMP/none" a
[ "$RC" -eq 0 ] || fail "a: exit 0 のはずが $RC: $ERR"
grep -q "compose up -d --build --force-recreate" <<<"$LOG" || fail "a: compose up が呼ばれない: $LOG"
if grep -q "docker-compose.sros2.yml" <<<"$LOG"; then fail "a: override が付いている: $LOG"; fi
grep -q "ENV SROS2_ENV=a" <<<"$LOG" || fail "a: SROS2_ENV=a が渡らない: $LOG"

# (b) b / c → override と SROS2_ENV と戦略が渡る
# keystore の置き場は <SROS2_KEYSTORE_ROOT>/<env>
run_case "" "$TMP/keystores" b
[ "$RC" -eq 0 ] || fail "b: exit 0 のはずが $RC: $ERR"
grep -q -- "-f docker-compose.yml -f docker-compose.sros2.yml" <<<"$LOG" || fail "b: override が付かない: $LOG"
grep -q "ENV SROS2_ENV=b SROS2_STRATEGY=Permissive" <<<"$LOG" || fail "b: SROS2_ENV/STRATEGY が違う: $LOG"

run_case "" "$TMP/keystores" c
[ "$RC" -eq 0 ] || fail "c: exit 0 のはずが $RC: $ERR"
grep -q "ENV SROS2_ENV=c SROS2_STRATEGY=Enforce" <<<"$LOG" || fail "c: SROS2_ENV/STRATEGY が違う: $LOG"

# (c) a --arm → arm profile が付く
run_case "" "$TMP/keystores" a --arm
[ "$RC" -eq 0 ] || fail "a --arm: exit 0 のはずが $RC: $ERR"
grep -q -- "--profile arm" <<<"$LOG" || fail "a --arm: --profile arm が付かない: $LOG"

# (d) ros2real が running → b/c は拒否
for e in b c; do
  run_case "ros2real" "$TMP/keystores" "$e"
  [ "$RC" -eq 1 ] || fail "$e: ros2real 起動中は exit 1 のはずが $RC"
  grep -q "ros2real" <<<"$ERR" || fail "$e: stderr に ros2real が含まれない: $ERR"
  no_compose "$e(ros2real)"
done

# (e) 不正な引数 → exit 2 で使い方
run_case "" "$TMP/keystores" x
[ "$RC" -eq 2 ] || fail "x: exit 2 のはずが $RC"
grep -qi "usage" <<<"$ERR" || fail "x: 使い方が出ない: $ERR"
no_compose "x"
run_case "" "$TMP/keystores"
[ "$RC" -eq 2 ] || fail "引数なし: exit 2 のはずが $RC"
no_compose "引数なし"
run_case "" "$TMP/keystores" a --unknown
[ "$RC" -eq 2 ] || fail "a --unknown: exit 2 のはずが $RC"
no_compose "a --unknown"

# (f) keystore 未生成の b/c → 生成を促して exit 1
run_case "" "$TMP/none" b
[ "$RC" -eq 1 ] || fail "b(keystore なし): exit 1 のはずが $RC"
grep -q "gen-keystore.sh b" <<<"$ERR" || fail "b(keystore なし): 生成コマンドの案内が無い: $ERR"
no_compose "b(keystore なし)"

# (g) b|c と --arm の同時指定 → exit 2（ros2arm は環境 A のまま混在するため）
for e in b c; do
  run_case "" "$TMP/keystores" "$e" --arm
  [ "$RC" -eq 2 ] || fail "$e --arm: exit 2 のはずが $RC"
  grep -q "ros2arm" <<<"$ERR" || fail "$e --arm: stderr に ros2arm の説明が無い: $ERR"
  no_compose "$e --arm"
done

# (h) b/c で ros2arm が起動中 → 警告は出すが続行する（環境 A のまま混在する旨）
run_case "ros2arm" "$TMP/keystores" c
[ "$RC" -eq 0 ] || fail "c(ros2arm 起動中): exit 0 のはずが $RC: $ERR"
grep -q "ros2arm" <<<"$ERR" || fail "c(ros2arm 起動中): 警告が出ない: $ERR"

echo "OK: up-env guard script tests passed"
