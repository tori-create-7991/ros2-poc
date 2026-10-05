#!/usr/bin/env bash
# VLA（OpenVLA または GPU 不要のスタブ）の手先差分で、シミュ上のアームを 1 ステップずつ動かす（シミュ専用）。
#   ros2server で変換ノード（vla_converter）を起動し、VLA ノード（vla_node）が仮想カメラ画像と指示を
#   VLA サーバー（POST /act）へ送って、返った差分を /vla/action に出す。変換ノードが IK でアーム指令にして /vla/ack を返す。
# 終了コード: 0 = 全ステップ ok / 1 = ok でないステップがあった / 2 = 環境の問題 / 64 = 引数の誤り / 130 = Ctrl-C
# 詳細は docs/openvla-ros2-bridge.md。
set -euo pipefail

cd "$(dirname "$0")/.."

usage() {
  cat >&2 <<'EOF'
usage: bash scripts/run-vla.sh --instruction "<指示文>" [--steps N] [--endpoint URL] [--unnorm-key KEY] [--timeout 秒]
  --instruction  VLA への指示文（英数字・空白・.,_!?- のみ。例: "move up"。スタブのキーワードは docs 参照）
  --steps        実行するステップ数（1〜100、既定 3）
  --endpoint     POST /act の URL（既定: 同じ compose の vla-server。本物の OpenVLA サーバーに替えるときに指定）
  --unnorm-key   OpenVLA の unnorm_key（英数字・_ . - のみ。スタブは無視。CRANE-X7 向けは未検証）
  --timeout      アームのコントローラが見えるまで待つ秒数（既定 120）
EOF
  exit 64
}

INSTRUCTION=""
STEPS=3
ENDPOINT=""
UNNORM_KEY=""
TIMEOUT=120
while [ "$#" -gt 0 ]; do
  case "$1" in
    --instruction) [ "$#" -ge 2 ] || usage; INSTRUCTION="$2"; shift 2 ;;
    --steps) [ "$#" -ge 2 ] || usage; STEPS="$2"; shift 2 ;;
    --endpoint) [ "$#" -ge 2 ] || usage; ENDPOINT="$2"; shift 2 ;;
    --unnorm-key) [ "$#" -ge 2 ] || usage; UNNORM_KEY="$2"; shift 2 ;;
    --timeout) [ "$#" -ge 2 ] || usage; TIMEOUT="$2"; shift 2 ;;
    -h|--help) usage ;;
    *) echo "不明な引数: $1" >&2; usage ;;
  esac
done

# 値は bash -c の文字列に入るので、シェルの特殊文字（引用符・$・; ・バックスラッシュなど）を含むものは実行しない。
# 正規表現は変数に入れて検査する（[[ =~ ]] の角括弧内の \ は文字として扱われ、すり抜けるため）。LC_ALL=C でロケール依存を避ける
re_instruction='^[A-Za-z0-9 .,_!?-]{1,200}$'
re_endpoint='^https?://[A-Za-z0-9.-]+(:[0-9]+)?(/[A-Za-z0-9._/-]*)?$'
re_key='^[A-Za-z0-9_.-]{1,100}$'
match() { (export LC_ALL=C; [[ "$1" =~ $2 ]]); }
{ match "$INSTRUCTION" "$re_instruction" && [[ "$INSTRUCTION" =~ [^\ ] ]]; } \
  || { echo "--instruction は英数字・空白・.,_!?- の 1〜200 文字" >&2; exit 64; }
[[ "$STEPS" =~ ^[0-9]+$ ]] && [ "$STEPS" -ge 1 ] && [ "$STEPS" -le 100 ] \
  || { echo "--steps は 1〜100" >&2; exit 64; }
[[ "$TIMEOUT" =~ ^[0-9]+$ ]] || { echo "--timeout は秒数（整数）" >&2; exit 64; }
if [ -n "$ENDPOINT" ]; then
  match "$ENDPOINT" "$re_endpoint" \
    || { echo "--endpoint は http(s)://host[:port]/path の形（認証情報・クエリは書かない）" >&2; exit 64; }
fi
if [ -n "$UNNORM_KEY" ]; then
  match "$UNNORM_KEY" "$re_key" || { echo "--unnorm-key は英数字と _ . - の 1〜100 文字" >&2; exit 64; }
fi

running() { [ -n "$(docker ps --filter "name=^$1\$" --filter 'status=running' -q)" ]; }
fail_env() { echo "$1" >&2; exit 2; }

# 実機ドライバが動いていると、同名のコントローラに指令が届いてしまう
if running ros2real; then
  fail_env "ros2real（実機ドライバ）が起動中。このスクリプトはシミュ専用なので実行しない（'bash scripts/down-real.sh' で止めてから）。"
fi
need="ros2arm ros2server"
# --endpoint 未指定のときだけ、同じ compose のスタブが要る（指定時は外部のサーバーを使う）
[ -z "$ENDPOINT" ] && need="$need vla-server"
for c in $need; do
  running "$c" || fail_env "$c が起動していない。'bash scripts/up-arm.sh' でシミュを起動し、'docker compose --profile vla up -d --build' で ros2server と vla-server を起動する。"
done
# ros2arm は SROS2 化していない（常に環境 a）。ros2server が環境 b / c だと DDS で通信できない。
# 空（docker によっては <no value>）はラベル導入前のコンテナなので a として扱う
srv_env="$(docker inspect -f '{{index .Config.Labels "ros2poc.env"}}' ros2server 2>/dev/null || true)"
if [ -n "$srv_env" ] && [ "$srv_env" != a ] && [ "$srv_env" != '<no value>' ]; then
  fail_env "ros2server が SROS2 環境 $srv_env で起動している。ros2arm は環境 a でしか使えない。"
fi

ROS_ENV='source /opt/ros/jazzy/setup.bash; source /opt/ros2_poc_ws/install/setup.bash'
# コンテナ名で直接入る（compose のプロジェクト名に依存しない）
srv() { docker exec ros2server bash -lc "$1"; }
srv_bg() { docker exec -d ros2server bash -lc "$1"; }
arm() { docker exec ros2arm bash -c "$1"; }

# 同じアームに run-scenario の指令が混ざらないようにする（run-scenario は ros2arm の /tmp にロックを置く）
if arm "test -d /tmp/run-scenario.lock" < /dev/null 2>/dev/null; then
  fail_env "run-scenario が実行中（ロック /tmp/run-scenario.lock あり）。同じアームに指令が混ざるので、終わってから実行する。"
fi
# 自分自身の同時実行も拒否する（ロックは ros2server の /tmp。コンテナを作り直すと消える）
LOCK=/tmp/run-vla.lock
if ! srv "mkdir $LOCK" < /dev/null > /dev/null 2>&1; then
  fail_env "別の run-vla が実行中（ロック $LOCK あり）。前回を強制終了したなら、実行中でないと確かめて外す: docker exec ros2server rm -r $LOCK"
fi
CONVERTER='[v]la_converter'
# shellcheck disable=SC2329  # trap から呼ぶ
cleanup() {
  srv "pkill -INT -f '$CONVERTER'; rm -r $LOCK" < /dev/null > /dev/null 2>&1 || true
}
trap cleanup EXIT
trap 'exit 130' INT TERM HUP

# 前回の変換ノードが残っていると ack が二重になる。残りがあれば止めてから起動する
srv "pkill -INT -f '$CONVERTER'; true" < /dev/null > /dev/null 2>&1 || true

# ros2server からアームのコントローラがちょうど 1 つ見えるまで待つ（Discovery 待ち。2 つ以上ならシミュの二重起動か
# 実機ドライバと混在として止める）
controller_count() {
  srv "timeout 25 ros2 topic info -v --no-daemon /crane_x7_arm_controller/joint_trajectory 2>/dev/null \
       | grep -c 'Node name: crane_x7_arm_controller'" < /dev/null 2>/dev/null || true
}
controller_seen() {
  local n
  # ros2server のログインシェルは最初にバナー行を出すので、数値の行だけを取る
  n="$(controller_count | grep -E '^[0-9]+$' | tail -n 1 || true)"
  [ "${n:-0}" -gt 1 ] && fail_env "crane_x7_arm_controller が ${n} 個見える（シミュの二重起動か実機ドライバと混在）。シミュを止めて起動し直す"
  [ "${n:-0}" -eq 1 ]
}
deadline=$(( $(date +%s) + TIMEOUT ))
until controller_seen; do
  if [ "$(date +%s)" -ge "$deadline" ]; then
    fail_env "ros2server から crane_x7_arm_controller が見えない（シミュが起動していないか Discovery の問題）。数十秒待ってから再実行する。"
  fi
  echo "ros2server から crane_x7_arm_controller が見えるのを待つ"
  sleep 5
done

echo "変換ノード（vla_converter）を起動"
srv_bg "$ROS_ENV; exec ros2 run ros2_poc_sim vla_converter > /tmp/vla_converter.log 2>&1" < /dev/null \
  || fail_env "ros2server で変換ノードを起動できない"

NODE_ARGS="--instruction '$INSTRUCTION' --steps $STEPS"
[ -n "$ENDPOINT" ] && NODE_ARGS="$NODE_ARGS --endpoint '$ENDPOINT'"
[ -n "$UNNORM_KEY" ] && NODE_ARGS="$NODE_ARGS --unnorm-key '$UNNORM_KEY'"
rc=0
srv "$ROS_ENV; ros2 run ros2_poc_sim vla_node $NODE_ARGS" < /dev/null || rc=$?
if [ "$rc" != 0 ] && [ "$rc" != 1 ]; then
  echo "変換ノードのログ（末尾）: docker exec ros2server tail -n 20 /tmp/vla_converter.log" >&2
fi
exit "$rc"
