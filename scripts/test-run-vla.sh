#!/usr/bin/env bash
# run-vla.sh の引数検査・起動前ガード・終了コードの経路を docker スタブで検証する（実際の docker は呼ばない）。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# docker スタブ。呼び出しを STUB_LOG に記録し、コンテナ内の動作をまねる:
#   docker ps --filter name=^X$   STUB_RUNNING（空白区切り）に X があれば ID を返す
#   docker inspect（ros2poc.env）  ros2server は STUB_SRV_ENV、ros2arm は STUB_ARM_ENV を返す（未設定なら a、空文字なら空 = ラベル導入前）
#   test -d /tmp/run-scenario.lock STUB_SCENARIO_LOCK=1 のとき「ある」
#   mkdir /tmp/run-vla.lock        STUB_VLA_LOCK=1 のとき「すでにある」（失敗）
#   grep -c 'Node name             STUB_CONTROLLERS を返す（既定 1）
#   vla_node                       STUB_NODE_RC で終了（既定 0）
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
if [ "${1:-}" = "inspect" ]; then
  if [[ "$*" == *ros2poc.env* ]]; then
    case "$*" in
      *" ros2arm") echo "${STUB_ARM_ENV-a}" ;;
      *) echo "${STUB_SRV_ENV-a}" ;;
    esac
  fi
  exit 0
fi
[ "${1:-}" = "exec" ] || exit 0
args="$*"
# ros2server のログインシェル（bash -lc）は実物と同じくバナーを先に出す
[[ "$args" == "exec ros2server bash -lc "* ]] && echo "[ros2server] DOMAIN=42 discovery=SUBNET"
case "$args" in
  *"test -d /tmp/run-scenario.lock"*) [ "${STUB_SCENARIO_LOCK:-0}" = 1 ] && exit 0; exit 1 ;;
  *"mkdir /tmp/run-vla.lock"*) [ "${STUB_VLA_LOCK:-0}" = 1 ] && exit 1; exit 0 ;;
  *"grep -c 'Node name"*) echo "${STUB_CONTROLLERS:-1}" ;;
  *"ros2 run ros2_poc_sim vla_node"*) exit "${STUB_NODE_RC:-0}" ;;
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
  : > "$STUB_LOG"
  export STUB_LOG
  set +e
  ERR="$(cd "$ROOT" && PATH="$TMP:$PATH" STUB_RUNNING="$running" bash "$ROOT/scripts/run-vla.sh" "$@" 2>&1 >/dev/null)"
  RC=$?
  set -e
  LOG="$(cat "$STUB_LOG")"
}

ALL="ros2arm ros2server vla-server"
no_docker() { [ -z "$LOG" ] || fail "$1: docker が呼ばれた: $LOG"; }
no_exec() { if grep -qE "^(exec|compose)" <<<"$LOG"; then fail "$1: コンテナに命令が送られた: $LOG"; fi; }
started() { grep -q "vla_converter" <<<"$LOG" && grep -q "ros2 run ros2_poc_sim vla_node" <<<"$LOG"; }
lock_released() { grep -q "rm -r /tmp/run-vla.lock" <<<"$LOG"; }
lock_taken() { grep -q "mkdir /tmp/run-vla.lock" <<<"$LOG"; }

# --- 引数の誤り → 64（docker は呼ばない）
check_bad() {
  run_case "$ALL" "$@"
  [ "$RC" = 64 ] || fail "引数 [$*]: 終了コード $RC（期待 64）: $ERR"
  no_docker "引数 [$*]"
}
check_bad
check_bad --instruction
check_bad --instruction ''
check_bad --instruction ' '
check_bad --instruction 'a;b'
check_bad --instruction 'a\b'
# shellcheck disable=SC2016  # シェルに展開させず、そのまま文字列として渡すのが目的
check_bad --instruction '$(id)'
check_bad --instruction "a'b"
check_bad --instruction 'a"b'
check_bad --instruction 'é'
check_bad --instruction x --steps 0
check_bad --instruction x --steps 101
check_bad --instruction x --steps abc
check_bad --instruction x --timeout abc
check_bad --instruction x --endpoint file:///etc/passwd
check_bad --instruction x --endpoint http://user:pw@h/act
check_bad --instruction x --endpoint 'http://h/act?x=1'
check_bad --instruction x --unnorm-key ../x
check_bad --instruction x --bogus

# --- 実機ドライバが動いている → 2（コンテナに命令を送らない）
run_case "$ALL ros2real" --instruction "move up"
[ "$RC" = 2 ] || fail "ros2real 起動中: $RC"
grep -q ros2real <<<"$ERR" || fail "ros2real のメッセージが無い: $ERR"
no_exec "ros2real 起動中"

# --- 必要なコンテナが起動していない → 2
for missing in ros2arm ros2server vla-server; do
  others=""
  for c in $ALL; do [ "$c" = "$missing" ] || others="$others $c"; done
  run_case "$others" --instruction "move up"
  [ "$RC" = 2 ] || fail "$missing 未起動: $RC"
  grep -q "$missing" <<<"$ERR" || fail "$missing のメッセージが無い: $ERR"
  no_exec "$missing 未起動"
done
# --endpoint を指定したときは vla-server が無くてもよい（外部の VLA サーバーを使う）
run_case "ros2arm ros2server" --instruction "move up" --endpoint http://gpu-host:8000/act --timeout 0
[ "$RC" = 0 ] || fail "--endpoint 指定で vla-server 不要のはず: $RC $ERR"

# --- ros2server と ros2arm が別の SROS2 環境 → 2（up-env.sh で揃える案内）。同じ環境なら a / b / c のどれでも通る。
# 空（ラベル導入前）と <no value> は a 扱い
for env in b c; do
  STUB_SRV_ENV="$env" run_case "$ALL" --instruction "move up"
  [ "$RC" = 2 ] || fail "ros2server 環境 $env / ros2arm a: $RC"
  grep -q "ros2server が SROS2 環境 $env、ros2arm が環境 a" <<<"$ERR" || fail "環境 $env のメッセージが無い: $ERR"
  grep -q "up-env.sh $env --arm --vla" <<<"$ERR" || fail "環境 $env: up-env.sh の案内が無い: $ERR"
  no_exec "ros2server 環境 $env / ros2arm a"
  STUB_ARM_ENV="$env" run_case "$ALL" --instruction "move up"
  [ "$RC" = 2 ] || fail "ros2server a / ros2arm 環境 $env: $RC"
  grep -q "ros2arm が環境 $env" <<<"$ERR" || fail "ros2arm 環境 $env のメッセージが無い: $ERR"
  no_exec "ros2server a / ros2arm 環境 $env"
  STUB_SRV_ENV="$env" STUB_ARM_ENV="$env" run_case "$ALL" --instruction "move up" --timeout 0
  [ "$RC" = 0 ] || fail "ros2server と ros2arm が同じ環境 $env なら通るはず: $RC $ERR"
  started || fail "環境 $env（同じ）で起動していない: $LOG"
done
STUB_SRV_ENV=b STUB_ARM_ENV=c run_case "$ALL" --instruction "move up"
[ "$RC" = 2 ] || fail "b と c の混在は止まるはず: $RC"
for env in "" "<no value>"; do
  STUB_SRV_ENV="$env" run_case "$ALL" --instruction "move up"
  [ "$RC" = 0 ] || fail "環境ラベル [$env] は a 扱いのはず: $RC $ERR"
  STUB_SRV_ENV="$env" STUB_ARM_ENV="$env" run_case "$ALL" --instruction "move up"
  [ "$RC" = 0 ] || fail "ros2arm も空ラベルなら a 同士で通るはず: $RC $ERR"
done

# --- run-scenario が実行中 → 2（変換ノードも起動しない。ロックも取らない）
STUB_SCENARIO_LOCK=1 run_case "$ALL" --instruction "move up"
[ "$RC" = 2 ] || fail "run-scenario 実行中: $RC"
grep -q run-scenario <<<"$ERR" || fail "run-scenario のメッセージが無い: $ERR"
if started || lock_taken; then fail "run-scenario 実行中なのに起動した: $LOG"; fi

# --- 別の run-vla が実行中 → 2。他人のロックは外さない
STUB_VLA_LOCK=1 run_case "$ALL" --instruction "move up"
[ "$RC" = 2 ] || fail "run-vla 実行中: $RC"
grep -q "別の run-vla" <<<"$ERR" || fail "run-vla ロックのメッセージが無い: $ERR"
if started || lock_released; then fail "他人のロックを外した、または起動した: $LOG"; fi

# --- コントローラが見えない / 2 つ以上 → 2（ロックは後始末する）
STUB_CONTROLLERS=0 run_case "$ALL" --instruction "move up" --timeout 0
[ "$RC" = 2 ] || fail "コントローラ 0: $RC"
grep -q "見えない" <<<"$ERR" || fail "見えないのメッセージが無い: $ERR"
started && fail "コントローラが見えないのに起動した: $LOG"
lock_released || fail "コントローラ 0: ロックが残る"
STUB_CONTROLLERS=2 run_case "$ALL" --instruction "move up" --timeout 0
[ "$RC" = 2 ] || fail "コントローラ 2: $RC"
grep -q "2 個見える" <<<"$ERR" || fail "二重起動のメッセージが無い: $ERR"
lock_released || fail "コントローラ 2: ロックが残る"

# --- 正常: 変換ノード → VLA ノードの順に起動し、終わったら変換ノードを止めてロックを外す
run_case "$ALL" --instruction "move up" --timeout 0
[ "$RC" = 0 ] || fail "正常: $RC $ERR"
started || fail "変換ノードか VLA ノードが起動していない: $LOG"
grep -q "vla_node --instruction='move up' --steps 3" <<<"$LOG" || fail "既定の引数が渡っていない: $LOG"
grep -q -- "--endpoint" <<<"$LOG" && fail "--endpoint を指定していないのに渡った: $LOG"
conv_line="$(grep -n "exec -d ros2server" <<<"$LOG" | head -n 1 | cut -d: -f1)"
node_line="$(grep -n "vla_node" <<<"$LOG" | head -n 1 | cut -d: -f1)"
if ! { [ -n "$conv_line" ] && [ "$conv_line" -lt "$node_line" ]; }; then
  fail "変換ノードが VLA ノードより先に起動していない: $LOG"
fi
grep -q "pkill -INT -f '\[v\]la_converter'" <<<"$LOG" || fail "変換ノードを止めていない: $LOG"
grep -q "pkill -INT -f '\[v\]la_node'" <<<"$LOG" || fail "VLA ノードを止めていない（Ctrl-C で残る）: $LOG"
lock_released || fail "正常: ロックが残る"

# --- 引数が VLA ノードに渡る
run_case "$ALL" --instruction "close the gripper" --steps 5 --endpoint http://gpu-host:8000/act --unnorm-key bridge_orig --timeout 0
[ "$RC" = 0 ] || fail "引数付き: $RC $ERR"
grep -q "vla_node --instruction='close the gripper' --steps 5 --endpoint='http://gpu-host:8000/act' --unnorm-key='bridge_orig'" <<<"$LOG" \
  || fail "引数が渡っていない: $LOG"

# --- VLA ノードの終了コードをそのまま返す（1 = 失敗ステップ、2 = 環境の問題）。後始末はする
STUB_NODE_RC=1 run_case "$ALL" --instruction "move up" --timeout 0
[ "$RC" = 1 ] || fail "ノード rc=1: $RC"
lock_released || fail "ノード rc=1: ロックが残る"
STUB_NODE_RC=2 run_case "$ALL" --instruction "move up" --timeout 0
[ "$RC" = 2 ] || fail "ノード rc=2: $RC"
grep -q "vla_converter.log" <<<"$ERR" || fail "変換ノードのログの案内が無い: $ERR"
lock_released || fail "ノード rc=2: ロックが残る"

echo "test-run-vla: OK"
