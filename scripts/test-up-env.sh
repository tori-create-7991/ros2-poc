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
  echo "ENV SROS2_ENV=${SROS2_ENV:-} SROS2_STRATEGY=${SROS2_STRATEGY:-} KS_ROOT=${SROS2_KEYSTORE_ROOT:-} CRL=${SROS2_REQUIRE_CRL:-} ENC_A=${SROS2_ENCLAVE_ROS2LAB_A:-}" >> "$STUB_LOG"
  # STUB_FAIL_COMPOSE=1 のとき compose だけ失敗させる
  if [ "${STUB_FAIL_COMPOSE:-0}" = "1" ]; then exit 1; fi
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
echo 'export SROS2_ENCLAVE_ROS2LAB_A=/lab/test_a' > "$TMP/keystores/b/env.sh"
echo 'export SROS2_ENCLAVE_ROS2LAB_A=/lab/test_a' > "$TMP/keystores/c/env.sh"

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
  ERR="$(PATH="$TMP:$PATH" STUB_RUNNING="$running" STUB_FAIL_COMPOSE="${STUB_FAIL_COMPOSE:-0}" SROS2_KEYSTORE_ROOT="$ks_root" SROS2_WORKSPACE_DIR="${WS_DIR:-$TMP/ws-none}" \
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
# 呼び出し元に SROS2_REQUIRE_CRL=true が残っていても、環境 b には渡らない
SROS2_REQUIRE_CRL=true run_case "" "$TMP/keystores" b
[ "$RC" -eq 0 ] || fail "b: exit 0 のはずが $RC: $ERR"
grep -q -- "-f docker-compose.yml -f docker-compose.sros2.yml" <<<"$LOG" || fail "b: override が付かない: $LOG"
grep -q "ENV SROS2_ENV=b SROS2_STRATEGY=Permissive" <<<"$LOG" || fail "b: SROS2_ENV/STRATEGY が違う: $LOG"
grep -q "docker-compose.sros2.yml up -d --build --force-recreate" <<<"$LOG" || fail "b: up -d --build --force-recreate の形でない: $LOG"
grep -q "KS_ROOT=$TMP/keystores" <<<"$LOG" || fail "b: SROS2_KEYSTORE_ROOT が compose に渡らない: $LOG"
grep -q "ENC_A=/lab/test_a" <<<"$LOG" || fail "b: env.sh の SROS2_ENCLAVE_* が compose に渡らない: $LOG"
grep -q "CRL= " <<<"$LOG" || fail "b: CRL を要求してはいけない: $LOG"

run_case "" "$TMP/keystores" c
[ "$RC" -eq 0 ] || fail "c: exit 0 のはずが $RC: $ERR"
grep -q "ENV SROS2_ENV=c SROS2_STRATEGY=Enforce" <<<"$LOG" || fail "c: SROS2_ENV/STRATEGY が違う: $LOG"
grep -q "docker-compose.sros2.yml up -d --build --force-recreate" <<<"$LOG" || fail "c: up -d --build --force-recreate の形でない: $LOG"
grep -q "CRL=true" <<<"$LOG" || fail "c: SROS2_REQUIRE_CRL=true が渡らない: $LOG"
grep -q "ENC_A=/lab/test_a" <<<"$LOG" || fail "c: env.sh の SROS2_ENCLAVE_* が compose に渡らない: $LOG"

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
run_case "" "$TMP/none" c
[ "$RC" -eq 1 ] || fail "c(keystore なし): exit 1 のはずが $RC"
grep -q "gen-keystore.sh c" <<<"$ERR" || fail "c(keystore なし): 生成コマンドの案内が無い: $ERR"
no_compose "c(keystore なし)"

# (f2) env.sh が無い b/c → 中断（既定の enclave 名で起動してしまわないように）
mkdir -p "$TMP/ks-noenv/b" "$TMP/ks-noenv/c"
for e in b c; do
  run_case "" "$TMP/ks-noenv" "$e"
  [ "$RC" -eq 1 ] || fail "$e(env.sh なし): exit 1 のはずが $RC"
  grep -q "env.sh" <<<"$ERR" || fail "$e(env.sh なし): stderr に env.sh が無い: $ERR"
  no_compose "$e(env.sh なし)"
done

# (g) b|c と --arm / --vla: override と profile が付き、指定した分だけ起動し直す（ros2arm・ros2server も同じ環境に揃える）
for e in b c; do
  run_case "" "$TMP/keystores" "$e" --arm
  [ "$RC" -eq 0 ] || fail "$e --arm: exit 0 のはずが $RC: $ERR"
  grep -q -- "-f docker-compose.yml -f docker-compose.sros2.yml --profile arm up -d --build --force-recreate" <<<"$LOG" \
    || fail "$e --arm: override と --profile arm が付かない: $LOG"
  if grep -q -- "--profile vla" <<<"$LOG"; then fail "$e --arm: 指定していない vla が付いた: $LOG"; fi
  run_case "" "$TMP/keystores" "$e" --vla
  [ "$RC" -eq 0 ] || fail "$e --vla: exit 0 のはずが $RC: $ERR"
  grep -q -- "docker-compose.sros2.yml --profile vla up -d" <<<"$LOG" || fail "$e --vla: --profile vla が付かない: $LOG"
  if grep -q -- "--profile arm" <<<"$LOG"; then fail "$e --vla: 指定していない arm が付いた: $LOG"; fi
  run_case "" "$TMP/keystores" "$e" --arm --vla
  [ "$RC" -eq 0 ] || fail "$e --arm --vla: exit 0 のはずが $RC: $ERR"
  grep -q -- "--profile arm --profile vla up -d" <<<"$LOG" || fail "$e --arm --vla: 両方の profile が付かない: $LOG"
done
run_case "" "$TMP/keystores" a --vla
[ "$RC" -eq 0 ] || fail "a --vla: exit 0 のはずが $RC: $ERR"
grep -q -- "compose --profile vla up -d --build --force-recreate" <<<"$LOG" || fail "a --vla: --profile vla が付かない: $LOG"
run_case "" "$TMP/keystores" a --arm --vla
grep -q -- "compose --profile arm --profile vla up -d" <<<"$LOG" || fail "a --arm --vla: 両方の profile が付かない: $LOG"
# profile を付けないときは、空の配列でも macOS の bash 3.2 で落ちない（compose に profile が渡らない）
for e in a b c; do
  run_case "" "$TMP/keystores" "$e"
  [ "$RC" -eq 0 ] || fail "$e（profile なし）: exit 0 のはずが $RC: $ERR"
  if grep -q -- "--profile" <<<"$LOG"; then fail "$e（profile なし）: profile が付いた: $LOG"; fi
done

# (h) b/c で ros2arm / ros2server が起動中なのに --arm / --vla が無い → 警告は出すが続行する（前の環境のまま混在する旨）
run_case "ros2arm" "$TMP/keystores" c
[ "$RC" -eq 0 ] || fail "c(ros2arm 起動中): exit 0 のはずが $RC: $ERR"
grep -q "ros2arm.*--arm" <<<"$ERR" || fail "c(ros2arm 起動中): --arm の警告が出ない: $ERR"
run_case "ros2server" "$TMP/keystores" b
[ "$RC" -eq 0 ] || fail "b(ros2server 起動中): exit 0 のはずが $RC: $ERR"
grep -q "ros2server.*--vla" <<<"$ERR" || fail "b(ros2server 起動中): --vla の警告が出ない: $ERR"
# 指定したときは警告しない
run_case "ros2arm ros2server" "$TMP/keystores" c --arm --vla
[ "$RC" -eq 0 ] || fail "c(両方起動中 + 両方指定): exit 0 のはずが $RC: $ERR"
if grep -q "混在" <<<"$ERR"; then fail "c(指定済み): 警告が出た: $ERR"; fi

# 環境 a に戻すときも警告する（ros2arm / ros2server は前の環境のまま残り、ros2lab と通信できなくなる）
run_case "ros2arm" "$TMP/keystores" a
[ "$RC" -eq 0 ] || fail "a(ros2arm 起動中): exit 0 のはずが $RC: $ERR"
grep -q "ros2arm.*--arm" <<<"$ERR" || fail "a(ros2arm 起動中): --arm の警告が出ない: $ERR"
run_case "ros2arm ros2server" "$TMP/keystores" a --arm --vla
if grep -q "混在" <<<"$ERR"; then fail "a(指定済み): 警告が出た: $ERR"; fi

# (i) 環境 b が ./workspace に置いた鍵のコピー（目印ファイルつき）の扱い
mk_leak() { rm -rf "$TMP/ws"; mkdir -p "$TMP/ws/sros2-keystore"; touch "$TMP/ws/sros2-keystore/key.pem" "$TMP/ws/sros2-keystore/.sros2-generated"; }
# b では残す
mk_leak
WS_DIR="$TMP/ws" run_case "" "$TMP/keystores" b
[ "$RC" -eq 0 ] || fail "b(workspace の鍵): exit 0 のはずが $RC: $ERR"
[ -f "$TMP/ws/sros2-keystore/key.pem" ] || fail "b: workspace の鍵のコピーが消えた"
# c では compose の成功後に消す
mk_leak
WS_DIR="$TMP/ws" run_case "" "$TMP/keystores" c
[ "$RC" -eq 0 ] || fail "c(workspace の鍵): exit 0 のはずが $RC: $ERR"
[ ! -e "$TMP/ws/sros2-keystore" ] || fail "c: workspace の鍵のコピーが残っている"
grep -q "削除" <<<"$ERR" || fail "c: 削除したことが stderr に出ない: $ERR"
# a でも消す
mk_leak
WS_DIR="$TMP/ws" run_case "" "$TMP/keystores" a
[ ! -e "$TMP/ws/sros2-keystore" ] || fail "a: workspace の鍵のコピーが残っている"
# 目印がない同名のディレクトリ（ユーザーのもの）は消さない
mk_leak
rm "$TMP/ws/sros2-keystore/.sros2-generated"
WS_DIR="$TMP/ws" run_case "" "$TMP/keystores" c
[ -f "$TMP/ws/sros2-keystore/key.pem" ] || fail "c: 目印のない同名ディレクトリを消した"
# compose が失敗したときは消さない（環境 b が残っているため）
mk_leak
STUB_FAIL_COMPOSE=1 WS_DIR="$TMP/ws" run_case "" "$TMP/keystores" c
[ "$RC" -ne 0 ] || fail "c(compose 失敗): 失敗のはずが exit 0"
[ -f "$TMP/ws/sros2-keystore/key.pem" ] || fail "c(compose 失敗): 鍵のコピーを消した"
# 拒否された（ros2real 起動中・keystore なし）ときも消さない
mk_leak
WS_DIR="$TMP/ws" run_case "ros2real" "$TMP/keystores" c
[ -f "$TMP/ws/sros2-keystore/key.pem" ] || fail "c(ros2real): 鍵のコピーを消した"
WS_DIR="$TMP/ws" run_case "" "$TMP/none" c
[ -f "$TMP/ws/sros2-keystore/key.pem" ] || fail "c(keystore なし): 鍵のコピーを消した"

echo "OK: up-env guard script tests passed"
