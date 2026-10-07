#!/usr/bin/env bash
# VLA（OpenVLA または GPU 不要のスタブ）の手先差分で、シミュ上のアームを 1 ステップずつ動かす（シミュ専用）。
#   ros2server で変換ノード（vla_converter）を起動し、VLA ノード（vla_node）が仮想カメラ画像と指示を
#   VLA サーバー（POST /act）へ送って、返った差分を /vla/action に出す。変換ノードが IK でアーム指令にして /vla/ack を返す。
#   --record を付けると、ros2arm で記録（カメラ・/joint_states・手先 TF・デスクトップ画面）→ 判定 → 1 本の mp4 に合成する
#   （run-scenario.sh と同じ仕組み。出力は workspace/runs/<日時>/）。
# 終了コード: 0 = 全ステップ ok（--record では判定も全 PASS）/ 1 = ok でないステップがあった（または判定が FAIL）/
#            2 = 環境・記録の問題 / 64 = 引数の誤り / 130 = Ctrl-C
# 詳細は docs/openvla-ros2-bridge.md。
set -euo pipefail

cd "$(dirname "$0")/.."

usage() {
  cat >&2 <<'EOF'
usage: bash scripts/run-vla.sh --instruction "<指示文>" [--steps N] [--endpoint URL] [--unnorm-key KEY] [--timeout 秒] [--record]
  --instruction  VLA への指示文（英数字・空白・.,_!?- のみ。例: "move up"。スタブのキーワードは docs 参照）
  --steps        実行するステップ数（1〜100、既定 3）
  --endpoint     POST /act の URL（既定: 同じ compose の vla-server。本物の OpenVLA サーバーに替えるときに指定）
  --unnorm-key   OpenVLA の unnorm_key（英数字・_ . - のみ。スタブは無視。CRANE-X7 向けは未検証）
  --timeout      アームのコントローラ・シミュのトピックが見えるまで待つ秒数（各待ちごと。既定 120）
  --record       録画して判定する（動画 scenario.mp4 と result.json を workspace/runs/<日時>/ に作る）。
                 判定は「指令どおり動いたか」（関節・映像・静止・手先が VLA の差分どおりか）。VLA の出力の良し悪しは見ない
EOF
  exit 64
}

INSTRUCTION=""
STEPS=3
ENDPOINT=""
UNNORM_KEY=""
TIMEOUT=120
RECORD=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --instruction) [ "$#" -ge 2 ] || usage; INSTRUCTION="$2"; shift 2 ;;
    --steps) [ "$#" -ge 2 ] || usage; STEPS="$2"; shift 2 ;;
    --endpoint) [ "$#" -ge 2 ] || usage; ENDPOINT="$2"; shift 2 ;;
    --unnorm-key) [ "$#" -ge 2 ] || usage; UNNORM_KEY="$2"; shift 2 ;;
    --timeout) [ "$#" -ge 2 ] || usage; TIMEOUT="$2"; shift 2 ;;
    --record) RECORD=1; shift ;;
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
if ! { [[ "$STEPS" =~ ^[0-9]+$ ]] && [ "$STEPS" -ge 1 ] && [ "$STEPS" -le 100 ]; }; then
  echo "--steps は 1〜100" >&2
  exit 64
fi
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
  running "$c" || fail_env "$c が起動していない。'bash scripts/up-arm.sh' でシミュを起動し、'docker compose --profile vla up -d --build ros2server vla-server' で ros2server と vla-server を起動する。"
done
# ros2server と ros2arm が別の SROS2 環境（a: 未適用 / b: 誤設定 / c: 適正）だと DDS で通信できず、
# コントローラ待ちで時間切れになる。同じ環境ならどの環境でも使える。
# 空（docker によっては <no value>）はラベル導入前のコンテナなので a として扱う
env_of() {
  local e
  e="$(docker inspect -f '{{index .Config.Labels "ros2poc.env"}}' "$1" 2>/dev/null || true)"
  case "$e" in "" | '<no value>') echo a ;; *) echo "$e" ;; esac
}
srv_env="$(env_of ros2server)"
arm_env="$(env_of ros2arm)"
if [ "$srv_env" != "$arm_env" ]; then
  fail_env "ros2server が SROS2 環境 $srv_env、ros2arm が環境 $arm_env で起動していて、DDS で通信できない。同じ環境に揃える: 'bash scripts/up-env.sh $srv_env --arm --vla'（または a / b / c のどれかに合わせる）"
fi

ROS_ENV='source /opt/ros/jazzy/setup.bash; source /opt/ros2_poc_ws/install/setup.bash'
# コンテナ名で直接入る（compose のプロジェクト名に依存しない）
srv() { docker exec ros2server bash -lc "$1"; }
srv_bg() { docker exec -d ros2server bash -lc "$1"; }
if [ "$RECORD" = 1 ]; then
  # 記録・判定・合成（scripts/lib/record.sh。run-scenario.sh と共通）は ros2arm の ubuntu・デスクトップ（DISPLAY=:1）で動かす
  ARM_ROS_ENV='source /opt/ros/jazzy/setup.bash; source /opt/crane_ws/install/setup.bash; source /opt/ros2_poc_ws/install/setup.bash'
  arm() { docker exec -u ubuntu -e DISPLAY=:1 ros2arm bash -c "$ARM_ROS_ENV; $1"; }
  arm_bg() { docker exec -d -u ubuntu -e DISPLAY=:1 ros2arm bash -c "$ARM_ROS_ENV; $1"; }
  REC_LABEL='run-scenario / run-vla --record'
  # shellcheck source=scripts/lib/record.sh
  . scripts/lib/record.sh
  rec_doctor
  rec_init_run
else
  arm() { docker exec ros2arm bash -c "$1"; }
fi
CONVERTER='[v]la_converter'
NODE='[v]la_node'
VLA_LOCK=/tmp/run-vla.lock
HAVE_VLA_LOCK=0
VLA_LOCK_ATTEMPT=0
VLA_OWNER="$(date +%Y%m%d-%H%M%S) $(hostname -s 2>/dev/null || echo host) $$"
# 後片付け。トラップはロックを取る前に入れる（取得の途中の Ctrl-C / TERM でロックを漏らさない）。
# 取れた分だけ無条件に外し、取得の途中だった分は所有者が自分のときだけ外す。取れなかったとき（他人が持っている）は何も外さない
# shellcheck disable=SC2317,SC2329  # trap から呼ぶ（shellcheck の版によって指摘の番号が違う）
cleanup() {
  # 後片付け（記録の停止は最大 2 分ほどかかる）の途中のシグナルでも、ロックを外すところまで進める
  trap '' INT TERM HUP
  if [ "$HAVE_VLA_LOCK" = 1 ]; then
    # docker exec は（TTY なしでは）シグナルを転送しないので、Ctrl-C のあとコンテナ内に残らないよう VLA ノードも止める
    srv "pkill -INT -f '$NODE'; pkill -INT -f '$CONVERTER'; rm -r $VLA_LOCK" < /dev/null > /dev/null 2>&1 || true
  elif [ "$VLA_LOCK_ATTEMPT" = 1 ]; then
    srv "grep -qxF '$VLA_OWNER' $VLA_LOCK/owner && rm -r $VLA_LOCK" < /dev/null > /dev/null 2>&1 || true
  fi
  if [ "$RECORD" = 1 ]; then rec_cleanup; fi
}
trap cleanup EXIT
trap 'exit 130' INT TERM HUP

# 同じアームに run-scenario の指令が混ざらないよう、ロックは「自分のロックを取ってから、相手のロックを見る」（set → check）。
# --record は run-scenario と同じロック（ros2arm の /tmp/run-scenario.lock）を取る。run-scenario は自分のロックを取ってから
# run-vla のロック（ros2server の /tmp/run-vla.lock）を見るので、同時に始まっても両方が通ることはない（両方が断ることはありうる）
if [ "$RECORD" = 1 ]; then
  rec_lock_acquire
fi
# 自分自身の同時実行も拒否する（ロックは ros2server の /tmp。コンテナを作り直すと消える）
VLA_LOCK_ATTEMPT=1
if ! srv "mkdir $VLA_LOCK && echo '$VLA_OWNER' > $VLA_LOCK/owner" < /dev/null > /dev/null 2>&1; then
  VLA_LOCK_ATTEMPT=0
  fail_env "別の run-vla が実行中（ロック $VLA_LOCK あり）。前回を強制終了したなら、実行中でないと確かめて外す: docker exec ros2server rm -r $VLA_LOCK"
fi
VLA_LOCK_ATTEMPT=0
HAVE_VLA_LOCK=1
if [ "$RECORD" = 0 ] && arm "test -d /tmp/run-scenario.lock" < /dev/null 2>/dev/null; then
  fail_env "run-scenario が実行中（ロック /tmp/run-scenario.lock あり）。同じアームに指令が混ざるので、終わってから実行する。"
fi

if [ "$RECORD" = 1 ]; then
  rec_check_leftovers
  rec_make_run_dir
  echo "出力先: $RUN_HOST"
  if [ "$arm_env" = a ]; then
    if ! sim_ready; then
      rec_wait_sim "$TIMEOUT" "カメラ付きのシミュ（arm_with_camera.launch.py）が動いているか確認する（'bash scripts/run-scenario.sh --start-sim' でも起動できる。docs/sim-scenario-recording.md）。"
    fi
  else
    # SROS2（環境 b / c）の ros2arm では ROS CLI（ros2 topic echo）の購読が許可されていないので、トピックの確認は省く。
    # 代わりに記録の立ち上がり（カメラのフレームが 4 枚記録されるか）で確かめる
    echo "SROS2 環境 $arm_env: ROS CLI で購読できないので、シミュのトピックの確認は記録の立ち上がりで代える"
  fi
fi

# 前回の変換ノード・VLA ノードが残っていると ack が二重になる。残りがあれば止めてから起動する
srv "pkill -INT -f '$NODE'; pkill -INT -f '$CONVERTER'; true" < /dev/null > /dev/null 2>&1 || true

# ros2server からアームのコントローラがちょうど 1 つ見えるまで待つ（Discovery 待ち。2 つ以上ならシミュの二重起動か
# 実機ドライバと混在として止める）
# SROS2（環境 b / c）では Discovery に認証のやり取りが入り、既定の待ち時間では何も見えない（実測: --spin-time 15 で見える）
SPIN=2
if [ "$srv_env" != a ]; then SPIN=15; fi
controller_count() {
  srv "timeout $((SPIN + 25)) ros2 topic info -v --spin-time $SPIN --no-daemon /crane_x7_arm_controller/joint_trajectory 2>/dev/null \
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

# 記録の最大秒数（記録プロセスの安全弁。通常は終了時に止める）。1 ステップあたりの待ちの上限（画像 60 + VLA 60 + ack 240 + 余裕）で見積もる
if [ "$RECORD" = 1 ]; then
  MAX_SEC=$(( 300 + STEPS * 400 ))
  rec_start "$MAX_SEC"
fi

echo "変換ノード（vla_converter）を起動"
srv_bg "$ROS_ENV; exec ros2 run ros2_poc_sim vla_converter > /tmp/vla_converter.log 2>&1" < /dev/null \
  || fail_env "ros2server で変換ノードを起動できない"

# 値が - で始まっても別のオプションと解釈されないよう --name=値 の形で渡す
NODE_ARGS="--instruction='$INSTRUCTION' --steps $STEPS"
[ -n "$ENDPOINT" ] && NODE_ARGS="$NODE_ARGS --endpoint='$ENDPOINT'"
[ -n "$UNNORM_KEY" ] && NODE_ARGS="$NODE_ARGS --unnorm-key='$UNNORM_KEY'"
[ "$RECORD" = 0 ] || NODE_ARGS="$NODE_ARGS --record-file='$RUN/vla_steps.jsonl'"
rc=0
srv "$ROS_ENV; ros2 run ros2_poc_sim vla_node $NODE_ARGS" < /dev/null || rc=$?
if [ "$rc" != 0 ] && [ "$rc" != 1 ]; then
  echo "変換ノードのログ（末尾）: docker exec ros2server tail -n 20 /tmp/vla_converter.log" >&2
fi
if [ "$RECORD" = 1 ] && { [ "$rc" = 0 ] || [ "$rc" = 1 ]; }; then
  # 記録（vla_steps.jsonl）を判定の入力に直し、最後に指令を送ったステップの判定に要る記録が揃うまで待つ。
  # 以降はシナリオと同じ（判定 → 合成）。VLA が失敗したステップ（rc=1）も判定して動画に残す
  last_sent="$(arm "ros2 run ros2_poc_sim scenario_cli vla-prepare $RUN" < /dev/null)" \
    || fail_env "VLA の実行記録から判定の入力を作れない（$RUN_HOST/vla_steps.jsonl を確認）"
  [[ -z "$last_sent" || "$last_sent" =~ ^[0-9]+$ ]] || fail_env "最後に送ったステップの番号が数値でない: $last_sent"
  if [ -n "$last_sent" ]; then
    arm "ros2 run ros2_poc_sim scenario_cli wait $RUN $last_sent" < /dev/null || true
  fi
  rec_stop_and_judge
  rec_compose
  # VLA の全ステップが ok で、判定も全 PASS のときだけ 0。VLA が失敗したステップは 1
  if [ "$rc" = 0 ]; then exit "$judge_rc"; fi
fi
exit "$rc"
