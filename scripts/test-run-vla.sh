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
# --record の記録・判定まわり（ros2arm 側。run-scenario.sh のテストと同じ作り）:
#   mkdir /tmp/run-scenario.lock   STUB_SCENARIO_LOCK=1 のとき失敗（取れない）。持ち主の表示は STUB_OWNER
#   scenario_cli doctor            STUB_DOCTOR_FAIL=1 で失敗
#   pgrep                          既定は「プロセス無し」。STUB_LEFTOVER=1 なら起動前から有り、STUB_STUCK=1 なら記録の起動後ずっと有り
#   ros2 topic echo                STUB_SIM_DOWN=1 のとき失敗（トピックが流れない）
#   scenario_cli vla-prepare       STUB_PREPARE_RC で終了、標準出力は STUB_LAST_SENT（既定 2）
#   scenario_cli judge / compose   STUB_JUDGE_RC（既定 0。1 以下なら result.json を置く）/ STUB_COMPOSE_RC（既定 0）
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
run_dir() { [[ "$args" =~ /workspace/runs/([0-9-]+) ]] && echo "$STUB_WS/runs/${BASH_REMATCH[1]}"; }
case "$args" in
  *"test -d /tmp/run-scenario.lock"*) [ "${STUB_SCENARIO_LOCK:-0}" = 1 ] && exit 0; exit 1 ;;
  *"mkdir /tmp/run-scenario.lock"*)
    [ "${STUB_SCENARIO_LOCK:-0}" = 1 ] && exit 1
    # 取得の途中（コンテナ側では済んでいる）にシグナルが来る状況を作る。待っている印を置いて、解除（signal 後）まで待つ
    if [ -n "${STUB_HANG_ARM:-}" ]; then touch "$STUB_WS/waiting.arm"; sleep "$STUB_HANG_ARM"; fi
    exit 0 ;;
  *"cat /tmp/run-scenario.lock/owner"*) echo "${STUB_OWNER:-}" ;;
  *"scenario_cli doctor"*) [ "${STUB_DOCTOR_FAIL:-0}" = 1 ] && exit 2; exit 0 ;;
  *"mkdir /tmp/run-vla.lock"*) [ "${STUB_VLA_LOCK:-0}" = 1 ] && exit 1; if [ -n "${STUB_HANG_LOCK:-}" ]; then touch "$STUB_WS/waiting.vla"; sleep "$STUB_HANG_LOCK"; fi; exit 0 ;;
  *"grep -c 'Node name"*) echo "${STUB_CONTROLLERS:-1}" ;;
  *"ros2 run ros2_poc_sim vla_node"*) exit "${STUB_NODE_RC:-0}" ;;
  *pgrep*)
    if [ "${STUB_LEFTOVER:-0}" = 1 ]; then exit 0; fi
    if [ "${STUB_STUCK:-0}" = 1 ] && [ -f "$STUB_WS/started" ]; then exit 0; fi
    exit 1 ;;
  *"mkdir -p /workspace/runs/"*) mkdir -p "$(run_dir)" ;;
  *"ros2 topic echo"*) [ "${STUB_SIM_DOWN:-0}" = 1 ] && exit 1; exit 0 ;;
  *xdpyinfo*) echo 1920x1080 ;;
  *"scenario_observer --out"*) touch "$STUB_WS/started"; printf 'n,t\n0,1\n1,2\n2,3\n3,4\n' > "$(run_dir)/camera_frames.csv" ;;
  *"scenario_cli vla-prepare"*) echo "${STUB_LAST_SENT-2}"; exit "${STUB_PREPARE_RC:-0}" ;;
  *"scenario_cli judge"*) rc="${STUB_JUDGE_RC:-0}"; [ "$rc" -le 1 ] && echo '{}' > "$(run_dir)/result.json"; exit "$rc" ;;
  *"scenario_cli compose"*) exit "${STUB_COMPOSE_RC:-0}" ;;
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
  ERR="$(cd "$ROOT" && PATH="$TMP:$PATH" STUB_RUNNING="$running" RUN_SCENARIO_WORKSPACE="$STUB_WS" \
    bash "$ROOT/scripts/run-vla.sh" "$@" 2>&1 >/dev/null)"
  RC=$?
  set -e
  LOG="$(cat "$STUB_LOG")"
}

ALL="ros2arm ros2server vla-server"
no_docker() { [ -z "$LOG" ] || fail "$1: docker が呼ばれた: $LOG"; }
no_exec() { if grep -qE "^(exec|compose)" <<<"$LOG"; then fail "$1: コンテナに命令が送られた: $LOG"; fi; }
called() { grep -q -- "$1" <<<"$LOG"; }
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
# ラベル導入前（空）のコンテナは a 扱い。環境 c のコンテナと混在したら止まる
STUB_SRV_ENV="" STUB_ARM_ENV=c run_case "$ALL" --instruction "move up"
[ "$RC" = 2 ] || fail "空ラベル（a 扱い）と c の混在は止まるはず: $RC"
grep -q "ros2server が SROS2 環境 a、ros2arm が環境 c" <<<"$ERR" || fail "空ラベルは a と表示されるはず: $ERR"
for env in "" "<no value>"; do
  STUB_SRV_ENV="$env" run_case "$ALL" --instruction "move up"
  [ "$RC" = 0 ] || fail "環境ラベル [$env] は a 扱いのはず: $RC $ERR"
  STUB_SRV_ENV="$env" STUB_ARM_ENV="$env" run_case "$ALL" --instruction "move up"
  [ "$RC" = 0 ] || fail "ros2arm も空ラベルなら a 同士で通るはず: $RC $ERR"
done

# --- run-scenario が実行中 → 2（変換ノードは起動しない）。自分のロックを先に取ってから見て（set → check）、断るときは外す
STUB_SCENARIO_LOCK=1 run_case "$ALL" --instruction "move up"
[ "$RC" = 2 ] || fail "run-scenario 実行中: $RC"
grep -q run-scenario <<<"$ERR" || fail "run-scenario のメッセージが無い: $ERR"
if started; then fail "run-scenario 実行中なのに起動した: $LOG"; fi
lock_taken || fail "run-scenario 実行中: 先に自分のロックを取るはず: $LOG"
lock_released || fail "run-scenario 実行中: 取った自分のロックを外していない: $LOG"
# 他人の run-vla のロック（取れなかった）は外さない。取れた自分のロックの後片付けでは、ノードを止めるのは自分が取ったあとだけ

# --- ロック取得の途中で Ctrl-C / TERM が来ても、docker exec は取得の途中で終わらない（コンテナ側で mkdir が済む）ので、
# トラップが所有者を照合して外す。取れなかったときは何も外さない（他人のロックを外さない・他人のノードを止めない）
STUB_VLA_LOCK=1 run_case "$ALL" --instruction "move up"
if called "pkill -INT"; then fail "ロックが取れなかったのに VLA ノードを止めた（他人のノードを止めうる）: $LOG"; fi
if called "grep -qxF"; then fail "ロックが取れなかったのに所有者照合の解放を試みた: $LOG"; fi

# ロック取得の途中（docker exec の往復中）に TERM が来た場合: bash はフォアグラウンドのコマンドが終わってからトラップを実行する。
# そのとき取得はコンテナ側で済んでいるので、所有者を照合して外す（外さないとロックが漏れて次の実行が止まる）。
# 引数: マーカー名（待っている印）, 期待する解放のコマンド（正規表現）, スクリプトの引数...（環境変数は呼び出し側で渡す）
hang_term() {
  local marker="$1" expect="$2" ws="$TMP/hang.$RANDOM"
  shift 2
  mkdir -p "$ws"
  STUB_LOG="$TMP/log.hang.$RANDOM"
  : > "$STUB_LOG"
  export STUB_LOG
  ( cd "$ROOT" && PATH="$TMP:$PATH" STUB_RUNNING="$ALL" STUB_WS="$ws" RUN_SCENARIO_WORKSPACE="$ws" exec "$@" > /dev/null 2>&1 ) &
  local pid=$! _
  # スタブが「待っている」印を置くまで待つ（時間に依存しない。上限 20 秒）
  for _ in $(seq 1 200); do
    [ -f "$ws/$marker" ] && break
    sleep 0.1
  done
  [ -f "$ws/$marker" ] || fail "取得の途中の状態を作れなかった（$marker）"
  kill -TERM "$pid" 2>/dev/null || true
  wait "$pid" 2>/dev/null || true
  LOG="$(cat "$STUB_LOG")"
  grep -q "$expect" <<<"$LOG" || fail "取得の途中の TERM で、所有者を照合してロックを外していない（$marker）: $LOG"
  if started; then fail "取得の途中で止めたのに変換ノードが起動した（$marker）: $LOG"; fi
}
# VLA のロック（非 --record。env の前に exec で、TERM がスクリプト本体に届くようにする）
hang_term waiting.vla "grep -qxF .* /tmp/run-vla.lock/owner && rm -r /tmp/run-vla.lock" \
  env STUB_HANG_LOCK=3 bash "$ROOT/scripts/run-vla.sh" --instruction "move up" --timeout 0
# run-scenario と共有する arm のロック（--record）。取得途中の TERM で、arm のロックを所有者照合で外し、VLA のロックは取らない
hang_term waiting.arm "grep -qxF .* /tmp/run-scenario.lock/owner && rm -r /tmp/run-scenario.lock" \
  env STUB_HANG_ARM=3 bash "$ROOT/scripts/run-vla.sh" --record --instruction "move up" --timeout 0
if called "mkdir /tmp/run-vla.lock"; then fail "arm のロックの取得途中で止めたのに VLA のロックを取った: $LOG"; fi

# --- 別の run-vla が実行中 → 2。他人のロックは外さない
STUB_VLA_LOCK=1 run_case "$ALL" --instruction "move up"
[ "$RC" = 2 ] || fail "run-vla 実行中: $RC"
grep -q "別の run-vla" <<<"$ERR" || fail "run-vla ロックのメッセージが無い: $ERR"
if started || lock_released; then fail "他人のロックを外した、または起動した: $LOG"; fi

# --- 探索の待ち時間: 環境 a は短く、SROS2（b / c）は認証のやり取りぶん長い
run_case "$ALL" --instruction "move up" --timeout 0
grep -q -- "--spin-time 2 --no-daemon" <<<"$LOG" || fail "環境 a の --spin-time が 2 でない: $LOG"
STUB_SRV_ENV=c STUB_ARM_ENV=c run_case "$ALL" --instruction "move up" --timeout 0
grep -q -- "--spin-time 15 --no-daemon" <<<"$LOG" || fail "環境 c の --spin-time が 15 でない: $LOG"

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

# =====================================================================================================
# --record: 記録 → 変換ノード → VLA ノード → 判定の入力を作る → 最後の送信を待つ → 止める → 判定 → 合成
# =====================================================================================================
line_of() { grep -n -m1 -- "$1" <<<"$LOG" | cut -d: -f1; }
RC_ARGS=(--instruction "move down" --steps 3 --record --timeout 0)
scenario_lock_released() { called "rm -r /tmp/run-scenario.lock"; }
locks_released() { lock_released && scenario_lock_released; }

check_bad --instruction x --record --steps 0
check_bad --instruction 'a;b' --record
# --record を付けない従来の実行は、記録も判定もしない（記録用のコマンドは 1 つも出ない）
run_case "$ALL" --instruction "move up" --timeout 0
[ "$RC" = 0 ] || fail "--record なし: $RC $ERR"
if called "scenario_observer\|x11grab\|scenario_cli\|mkdir /tmp/run-scenario.lock\|vla-prepare\|record-file"; then
  fail "--record なしなのに記録まわりが動いた: $LOG"
fi

# --- 正常: 記録 → 変換ノード → VLA ノード（--record-file）→ prepare → wait（最後に送ったステップ）→ 判定 → 合成の順。
# ロックは run-scenario と同じもの（ros2arm）と run-vla のもの（ros2server）の両方を取って、両方外す
run_case "$ALL" "${RC_ARGS[@]}"
[ "$RC" = 0 ] || fail "--record 正常: $RC $ERR"
for pattern in "scenario_cli doctor" "mkdir /tmp/run-scenario.lock" "mkdir /tmp/run-vla.lock" "scenario_observer --out" \
               "x11grab" "exec -d ros2server" "vla_node --instruction='move down' --steps 3" "scenario_cli vla-prepare" \
               "scenario_cli wait /workspace/runs/" "scenario_cli judge" "scenario_cli compose"; do
  called "$pattern" || fail "--record 正常: [$pattern] が呼ばれていない: $LOG"
done
prev=0
for pattern in "scenario_cli doctor" "mkdir /tmp/run-scenario.lock" "mkdir /tmp/run-vla.lock" "scenario_observer --out" \
               "exec -d ros2server" "ros2 run ros2_poc_sim vla_node" "scenario_cli vla-prepare" "scenario_cli wait" \
               "scenario_cli judge" "scenario_cli compose"; do
  n="$(line_of "$pattern")"
  [ "$n" -gt "$prev" ] || fail "--record 正常: [$pattern] の順序が違う（$n <= $prev）: $LOG"
  prev="$n"
done
grep -q "vla_node .*--record-file='/workspace/runs/[0-9-]*/vla_steps.jsonl'" <<<"$LOG" || fail "--record-file が渡っていない: $LOG"
called "scenario_cli wait /workspace/runs/[0-9-]* 2" || fail "最後に送ったステップ（2）を待っていない: $LOG"
called "^exec -u ubuntu -e DISPLAY=:1 ros2arm bash -c source /opt/ros/jazzy/setup.bash; source /opt/crane_ws/install/setup.bash" \
  || fail "ros2arm の記録系は ubuntu・DISPLAY=:1・crane_ws 込みで動かす: $LOG"
called "pkill -INT -f '\[s\]cenario_observer --out" || fail "記録を止めていない: $LOG"
called "pkill -INT -f '\[v\]la_node'" || fail "VLA ノードを止めていない: $LOG"
locks_released || fail "--record 正常: ロックが残る: $LOG"

# --- 判定の結果が終了コードになる（VLA が全ステップ ok でも、判定が FAIL なら 1）。判定できなければ 2
STUB_JUDGE_RC=1 run_case "$ALL" "${RC_ARGS[@]}"
[ "$RC" = 1 ] || fail "判定 FAIL: $RC $ERR"
STUB_JUDGE_RC=2 run_case "$ALL" "${RC_ARGS[@]}"
[ "$RC" = 2 ] || fail "判定できない: $RC"
grep -q "判定に失敗した" <<<"$ERR" || fail "判定できないメッセージが無い: $ERR"
locks_released || fail "判定できない: ロックが残る"
# 合成の失敗は終了コードを変えない
STUB_COMPOSE_RC=2 run_case "$ALL" "${RC_ARGS[@]}"
[ "$RC" = 0 ] || fail "合成の失敗は終了コードを変えないはず: $RC $ERR"
grep -q "合成に失敗した" <<<"$ERR" || fail "合成の失敗のメッセージが無い: $ERR"

# --- VLA ノードが失敗ステップで終わっても（1）判定して動画を残し、1 を返す。環境の問題（2）では判定しない
STUB_NODE_RC=1 run_case "$ALL" "${RC_ARGS[@]}"
[ "$RC" = 1 ] || fail "ノード rc=1（--record）: $RC $ERR"
if ! { called "scenario_cli judge" && called "scenario_cli compose"; }; then fail "失敗ステップも判定・合成するはず: $LOG"; fi
STUB_NODE_RC=1 STUB_JUDGE_RC=1 run_case "$ALL" "${RC_ARGS[@]}"
[ "$RC" = 1 ] || fail "ノード rc=1 かつ判定 FAIL: $RC"
STUB_NODE_RC=2 run_case "$ALL" "${RC_ARGS[@]}"
[ "$RC" = 2 ] || fail "ノード rc=2（--record）: $RC"
if called "vla-prepare\|scenario_cli judge\|scenario_cli compose"; then fail "環境の問題なのに判定した: $LOG"; fi
called "pkill -INT -f '\[s\]cenario_observer --out" || fail "ノード rc=2: 記録を止めていない: $LOG"
locks_released || fail "ノード rc=2（--record）: ロックが残る"
# 実行記録から判定の入力を作れない → 2（判定しない）
STUB_PREPARE_RC=2 run_case "$ALL" "${RC_ARGS[@]}"
[ "$RC" = 2 ] || fail "prepare 失敗: $RC"
grep -q "判定の入力を作れない" <<<"$ERR" || fail "prepare 失敗のメッセージが無い: $ERR"
if called "scenario_cli judge"; then fail "prepare 失敗なのに判定した: $LOG"; fi
# 指令を送ったステップが 1 つも無い（最初から ik_failed など）→ wait は呼ばないが、判定と動画は残す
STUB_NODE_RC=1 STUB_LAST_SENT="" run_case "$ALL" "${RC_ARGS[@]}"
[ "$RC" = 1 ] || fail "送ったステップが無い: $RC $ERR"
if called "scenario_cli wait"; then fail "送ったステップが無いのに wait した: $LOG"; fi
called "scenario_cli judge" || fail "送ったステップが無くても判定する: $LOG"
STUB_LAST_SENT='1;id' run_case "$ALL" "${RC_ARGS[@]}"
[ "$RC" = 2 ] || fail "最後のステップ番号が数値でないとき: $RC"
if called "scenario_cli wait"; then fail "不正な番号で wait した: $LOG"; fi

# --- 排他は双方向: run-scenario のロックが取れない → 2。他人のロックは外さず、run-vla のロックも取らない・記録もしない
STUB_SCENARIO_LOCK=1 run_case "$ALL" "${RC_ARGS[@]}"
[ "$RC" = 2 ] || fail "run-scenario のロックが取れない: $RC"
grep -q "前回の run-scenario / run-vla --record のロックが残っている" <<<"$ERR" || fail "ロックのメッセージが無い: $ERR"
if scenario_lock_released || lock_taken || started || called "scenario_observer --out"; then
  fail "他人のロックを外した、または起動した: $LOG"
fi
STUB_SCENARIO_LOCK=1 STUB_OWNER="20990101-000000 $(hostname -s 2>/dev/null || echo host) $$" run_case "$ALL" "${RC_ARGS[@]}"
grep -q "別の run-scenario / run-vla --record が実行中（開始 20990101-000000）" <<<"$ERR" || fail "持ち主が生きているロック: $ERR"
STUB_SCENARIO_LOCK=1 run_case "$ALL" --instruction x --timeout 0       # --record なしは従来どおり run-scenario のロックを見るだけ
if ! { [ "$RC" = 2 ] && grep -q "run-scenario が実行中" <<<"$ERR"; }; then fail "--record なしの run-scenario ロック: $RC $ERR"; fi
# 別の run-vla が実行中 → 取った run-scenario のロックは外す。他人の run-vla のロックは外さない
STUB_VLA_LOCK=1 run_case "$ALL" "${RC_ARGS[@]}"
[ "$RC" = 2 ] || fail "run-vla のロックが取れない（--record）: $RC"
grep -q "別の run-vla" <<<"$ERR" || fail "run-vla ロックのメッセージが無い: $ERR"
scenario_lock_released || fail "取った run-scenario のロックを外していない: $LOG"
if lock_released || called "scenario_observer --out"; then fail "他人の run-vla ロックを外した、または記録した: $LOG"; fi

# --- 録画・合成の前提が無い → 2（ロックも出力先も作らない）
STUB_DOCTOR_FAIL=1 run_case "$ALL" "${RC_ARGS[@]}"
[ "$RC" = 2 ] || fail "doctor 失敗: $RC"
grep -q "前提が揃っていない" <<<"$ERR" || fail "doctor 失敗のメッセージが無い: $ERR"
if called "mkdir"; then fail "doctor 失敗なのにロック・出力先を作った: $LOG"; fi
# 前回の記録プロセスが残っている → 2（取ったロックは両方外す。記録は始めない）
STUB_LEFTOVER=1 run_case "$ALL" "${RC_ARGS[@]}"
[ "$RC" = 2 ] || fail "記録プロセスが残っている: $RC"
grep -q "前回の記録プロセスが残っている" <<<"$ERR" || fail "残っているのメッセージが無い: $ERR"
locks_released || fail "残っている: ロックが残る"
if started || called "scenario_observer --out\|x11grab"; then fail "残っているのに起動した: $LOG"; fi
# シミュのトピックが流れない → 2（変換ノードは起動しない。ロックは外す）
STUB_SIM_DOWN=1 run_case "$ALL" "${RC_ARGS[@]}"
[ "$RC" = 2 ] || fail "トピックが流れない: $RC"
grep -q "トピックが流れない" <<<"$ERR" || fail "トピックのメッセージが無い: $ERR"
if started; then fail "トピックが流れないのに起動した: $LOG"; fi
locks_released || fail "トピックが流れない: ロックが残る"
# コントローラが見えない → 2（記録は始めない）
STUB_CONTROLLERS=0 run_case "$ALL" "${RC_ARGS[@]}"
[ "$RC" = 2 ] || fail "コントローラが見えない（--record）: $RC"
if called "scenario_observer --out"; then fail "コントローラが見えないのに記録を始めた: $LOG"; fi
locks_released || fail "コントローラが見えない（--record）: ロックが残る"

# --- 記録が止まらない（KILL まで送っても残る）→ 2、判定しない。ロックは外す。KILL は 1 回だけ
printf '#!/usr/bin/env bash\necho 1\n' > "$TMP/seq"
chmod +x "$TMP/seq"
STUB_STUCK=1 run_case "$ALL" "${RC_ARGS[@]}"
[ "$RC" = 2 ] || fail "記録が止まらない: $RC $ERR"
if called "scenario_cli judge"; then fail "記録が止まらないのに判定した: $LOG"; fi
called "pkill -KILL" || fail "止まらないとき KILL を送っていない: $LOG"
[ "$(grep -c "pkill -KILL" <<<"$LOG")" = 1 ] || fail "KILL を繰り返した: $LOG"
locks_released || fail "記録が止まらない: ロックが残る"
rm -f "$TMP/seq"

# --- 環境 b / c でも、ros2server と ros2arm が同じなら --record まで通る（記録は ros2arm 内）
for env in b c; do
  STUB_SRV_ENV="$env" STUB_ARM_ENV="$env" run_case "$ALL" "${RC_ARGS[@]}"
  [ "$RC" = 0 ] || fail "環境 $env の --record: $RC $ERR"
  if ! { called "scenario_observer --out" && called "scenario_cli judge"; }; then fail "環境 $env の --record: 記録・判定が動いていない: $LOG"; fi
  # ROS CLI の購読が許可されない環境なので、トピックの確認（ros2 topic echo）はしない
  if called "ros2 topic echo"; then fail "環境 $env の --record: ros2 topic echo を呼んだ: $LOG"; fi
done
run_case "$ALL" "${RC_ARGS[@]}"
called "ros2 topic echo" || fail "環境 a の --record は、シミュのトピックを確認するはず: $LOG"

echo "test-run-vla: OK"
