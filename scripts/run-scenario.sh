#!/usr/bin/env bash
# 動作シナリオを 1 コマンドで実行し、判定して録画する（シミュ専用）。
#   ros2lab-a からアームへ関節指令（README と同じ ros2 topic pub）、グリッパは ros2arm から action を送る。
#   ros2arm で記録（カメラ・/joint_states・手先 TF・デスクトップ画面）→ 終了後に判定 → 1 本の mp4 に合成。
# 出力: workspace/runs/<日時>/（scenario.mp4, result.json, commands.log ほか）
# 終了コード: 0 = 全ステップ PASS / 1 = FAIL あり / 2 = 環境・実行時の問題（指令が送れなかった場合を含む）/
#            64 = 引数の誤り / 130 = Ctrl-C
# 環境変数 RUN_LABEL=<名前> を付けると run_meta.json に残り、性能比較（scenario_cli perf）の目印になる（例: RUN_LABEL=light）。
set -euo pipefail

CALLER_DIR="$PWD"   # --scenario の相対パスは呼び出し元から解決する
cd "$(dirname "$0")/.."

usage() {
  cat >&2 <<'EOF'
usage: bash scripts/run-scenario.sh [--scenario <名前|YAMLのパス>] [--repeat N] [--start-sim] [--light] [--timeout 秒]
  --scenario   default（既定）/ fail_demo / examples、または YAML ファイルのパス
  --repeat     シナリオ全体の繰り返し回数（YAML の repeat を上書き）
  --start-sim  シミュ（Gazebo + 仮想カメラ、三人称視点）が動いていなければ起動する
  --light      軽量モード: GUI なし（gz sim サーバーのみ、RViz なし）のシミュで実行する（CPU を減らす。録画の左側は空になる）。--start-sim と使う
  --timeout    トピックが流れ始めるまで待つ秒数（既定 120、--start-sim 時 900）
EOF
  exit 64
}

SCENARIO=default
REPEAT=""
START_SIM=0
LIGHT=0
TIMEOUT=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --scenario) [ "$#" -ge 2 ] || usage; SCENARIO="$2"; shift 2 ;;
    --repeat) [ "$#" -ge 2 ] || usage; REPEAT="$2"; shift 2 ;;
    --start-sim) START_SIM=1; shift ;;
    --light) LIGHT=1; shift ;;
    --timeout) [ "$#" -ge 2 ] || usage; TIMEOUT="$2"; shift 2 ;;
    -h|--help) usage ;;
    *) echo "不明な引数: $1" >&2; usage ;;
  esac
done
if [ "$LIGHT" = 1 ]; then LAUNCH_FILE=arm_with_camera_headless.launch.py; else LAUNCH_FILE=arm_with_camera.launch.py; fi
case "$REPEAT" in ''|[1-9]|1[0-9]|20) ;; *) echo "--repeat は 1〜20" >&2; exit 64 ;; esac
if [ -z "$TIMEOUT" ]; then
  if [ "$START_SIM" = 1 ]; then TIMEOUT=900; else TIMEOUT=120; fi
fi
case "$TIMEOUT" in ''|*[!0-9]*) echo "--timeout は秒数（整数）" >&2; exit 64 ;; esac

RUN_LABEL="${RUN_LABEL:-}"
if [ -n "$RUN_LABEL" ] && [[ ! "$RUN_LABEL" =~ ^[A-Za-z0-9_-]{1,32}$ ]]; then
  echo "RUN_LABEL は英数字・_・- の 32 文字まで" >&2
  exit 64
fi

if [[ "$SCENARIO" == */* || "$SCENARIO" == *.yaml || "$SCENARIO" == *.yml ]]; then
  # パス指定: 呼び出し元のディレクトリから解決する（リポジトリ内の同名ファイルを黙って選ばない）
  case "$SCENARIO" in /*) SCENARIO_FILE="$SCENARIO" ;; *) SCENARIO_FILE="$CALLER_DIR/$SCENARIO" ;; esac
  if [ ! -f "$SCENARIO_FILE" ]; then
    echo "シナリオが見つからない: $SCENARIO" >&2
    exit 64
  fi
elif [[ "$SCENARIO" =~ ^[A-Za-z0-9_-]+$ ]] && [ -f "ros2_poc_sim/config/scenarios/$SCENARIO.yaml" ]; then
  SCENARIO_FILE="ros2_poc_sim/config/scenarios/$SCENARIO.yaml"
else
  echo "シナリオが見つからない: $SCENARIO" >&2
  exit 64
fi

running() { [ -n "$(docker ps --filter "name=^$1\$" --filter 'status=running' -q)" ]; }

# 実機ドライバが動いていると、同名のコントローラに指令が届いてしまう
if running ros2real; then
  echo "ros2real（実機ドライバ）が起動中。このスクリプトはシミュ専用なので実行しない（'bash scripts/down-real.sh' で止めてから）。" >&2
  exit 2
fi
for c in ros2arm ros2lab-a; do
  if ! running "$c"; then
    echo "$c が起動していない。先に 'bash scripts/up-arm.sh' を実行する。" >&2
    exit 2
  fi
done
# このスクリプトは ros2lab-a から ros2arm へ指令する。ros2lab-a は環境 b / c では最小権限のポリシーで、アームの
# トピックに触れない（ros2arm と ros2lab-a が別の環境でも DDS で通信できず、コントローラ待ちで時間切れになって
# Discovery の問題と誤って案内してしまう）。両方が環境 a のときだけ使える。
# 空（docker によっては <no value>）はラベル導入前のコンテナなので a として扱う
for c in ros2lab-a ros2arm; do
  c_env="$(docker inspect -f '{{index .Config.Labels "ros2poc.env"}}' "$c" 2>/dev/null || true)"
  if [ -n "$c_env" ] && [ "$c_env" != a ] && [ "$c_env" != '<no value>' ]; then
    echo "$c が SROS2 環境 $c_env で起動している。run-scenario は ros2lab-a から指令するので、ros2lab-a と ros2arm を環境 a に揃える: 'bash scripts/up-env.sh a --arm'" >&2
    exit 2
  fi
done

ROS_ENV='source /opt/ros/jazzy/setup.bash; source /opt/crane_ws/install/setup.bash; source /opt/ros2_poc_ws/install/setup.bash'
arm() { docker exec -u ubuntu -e DISPLAY=:1 ros2arm bash -c "$ROS_ENV; $1"; }
arm_bg() { docker exec -d -u ubuntu -e DISPLAY=:1 ros2arm bash -c "$ROS_ENV; $1"; }
# compose のプロジェクト名（= ディレクトリ名）に依存しないよう、コンテナ名で直接入る
lab() { docker exec ros2lab-a bash -lc "$1"; }
# scenario_cli の送信先（論理名）とコンテナの対応はここだけが持つ
send() {
  case "$1" in
    lab) lab "$2" ;;
    sim) arm "$2" ;;
    *) echo "不明な送信先: $1" >&2; return 2 ;;
  esac
}
fail_env() { echo "$1" >&2; exit 2; }

# 記録・判定・合成の共通部（run-vla.sh --record と共有）
# shellcheck source=scripts/lib/record.sh
. scripts/lib/record.sh

# 録画・合成に要る外部コマンド・フォント（ベースイメージ由来）を先に確かめる
rec_doctor
rec_init_run

# 同時実行の拒否（同じアームに 2 本の指令が混ざる）。ロックは ros2arm の /tmp に置く。
# コンテナを作り直す（up-arm.sh）と消えるが、docker restart や Colima の再起動では残る。
# トラップは取得の前に入れる（取得の途中の Ctrl-C でロックを漏らさない。取得に失敗したときは他人のロックを外さない）
# shellcheck disable=SC2317,SC2329  # trap から呼ぶ（shellcheck のバージョンでコードが違う）
cleanup() {
  trap '' INT TERM HUP   # 後片付けの途中のシグナルでも、ロックを外すところまで進める
  rec_cleanup
}
trap cleanup EXIT
rec_lock_acquire
# このスクリプトは同じアームに指令する。run-vla.sh（--record なしを含む）が動いていると指令が混ざるので断る。
# run-vla は ros2server の /tmp にロックを置く（ros2server が無い構成では何もしない）。
# 自分のロックを取ってから見る（set → check）。run-vla も自分のロックを取ってから run-scenario のロックを見るので、
# 同時に始まっても両方が通ることはない（両方が断ることはありうる）
if running ros2server && docker exec ros2server test -d /tmp/run-vla.lock < /dev/null 2>/dev/null; then
  fail_env "run-vla が実行中（ロック /tmp/run-vla.lock あり）。同じアームに指令が混ざるので、終わってから実行する。強制終了の残りなら、実行中でないと確かめて外す: docker exec ros2server rm -r /tmp/run-vla.lock"
fi
rec_check_leftovers

rec_make_run_dir
# 性能比較の目印: ラベルと実行環境（Colima VM の CPU・メモリ）。取れない項目は null（比較表では「-」）
VM_INFO="$(docker info --format '{{.NCPU}} {{.MemTotal}}' 2>/dev/null || true)"
read -r VM_CPUS VM_MEM <<<"$VM_INFO" || true
[[ "${VM_CPUS:-}" =~ ^[1-9][0-9]*$ ]] || VM_CPUS=null
if [[ "${VM_MEM:-}" =~ ^[1-9][0-9]*$ ]]; then VM_MEM_GIB=$(( VM_MEM / 1073741824 )); else VM_MEM_GIB=null; fi
printf '{"label": "%s", "cpus": %s, "mem_gib": %s, "scenario": "%s", "started": "%s"}\n' \
  "$RUN_LABEL" "$VM_CPUS" "$VM_MEM_GIB" "$(basename "$SCENARIO_FILE" .yaml | tr -c 'A-Za-z0-9._\n-' '_' | tr -d '\n')" "$TS" > "$RUN_HOST/run_meta.json" \
  || echo "run_meta.json を書けない（性能比較の目印が残らない）" >&2
cp "$SCENARIO_FILE" "$RUN_HOST/scenario.yaml" || fail_env "シナリオをコピーできない"
echo "出力先: $RUN_HOST"

if ! sim_ready; then
  if [ "$START_SIM" = 1 ]; then
    # 別のシミュ（カメラ無しの公式 launch など）や起動途中のシミュに重ねて起動すると、Gazebo と
    # コントローラが二重になり、指令が両方に届いて判定が無意味になる
    if arm "pgrep -f '[a]rm_with_camera(_headless)?\.launch\.py'" > /dev/null 2>&1 < /dev/null; then
      # カメラ付きのシミュが起動途中（前回の --start-sim が待ちの上限で終わった後など）: 重ねずに待つ
      echo "カメラ付きのシミュが起動途中なので、起動はせずに待つ"
    elif arm "pgrep -f '[r]os2 launch|[g]z sim'" > /dev/null 2>&1 < /dev/null; then
      fail_env "ros2arm でカメラ無しのシミュ（README のデモなど）が動いている。止めてから --start-sim で起動し直す: docker exec ros2arm pkill -INT -f '[r]os2 launch'"
    else
      echo "シミュを起動する（初回やホストが重いときは数分〜十数分かかる）。ログ: $WORKSPACE_HOST/runs/sim-$TS.log"
      arm_bg "exec ros2 launch ros2_poc_sim $LAUNCH_FILE placement:=fixed_front_wide > /workspace/runs/sim-$TS.log 2>&1"
    fi
  fi
  rec_wait_sim "$TIMEOUT" "カメラ付きのシミュ（$LAUNCH_FILE）が動いていなければ、他のシミュを止めて --start-sim を付ける（docs/sim-scenario-recording.md）。"
fi

if [ "$LIGHT" = 1 ]; then
  # GUI（gz sim gui）だけを止めると親の gz sim ごと終了して物理サーバーも落ちるので、止めずに GUI なしで起動する。
  # GUI 付きのシミュが動いていたら、止めてから --start-sim --light で起動し直してもらう
  if arm "pgrep -f '[a]rm_with_camera\.launch\.py|[g]z sim gui'" > /dev/null 2>&1 < /dev/null; then
    fail_env "GUI 付きのシミュが動いている。--light は GUI なしで起動するので、止めてから --start-sim --light で起動し直す: docker exec ros2arm pkill -INT -f '[r]os2 launch'"
  fi
  echo "軽量モード: GUI なしのシミュで実行する（デスクトップ録画の左側は空になる）"
  # RViz は move_group の launch が起動する。止めても他のノードは生きている。起動直後は空振りしうるので、止まるまで数回やる
  arm "for _ in 1 2 3; do pkill -x rviz2; sleep 1; pgrep -x rviz2 > /dev/null || exit 0; done; exit 1" < /dev/null \
    || echo "RViz を止められなかった（CPU を余分に使うが、判定には影響しない）" >&2
  sim_ready || fail_env "RViz を止めたらシミュのトピックが止まった。--light を付けずにシミュを起動し直す"
elif arm "pgrep -f '[a]rm_with_camera_headless\.launch\.py'" > /dev/null 2>&1 < /dev/null; then
  echo "GUI なしのシミュ（arm_with_camera_headless）に接続する。デスクトップ録画の左側は空になる" >&2
fi

lab true < /dev/null > /dev/null || fail_env "ros2lab-a でコマンドを実行できない"
# ros2lab-a からアームのコントローラが見えるまで待つ。`ros2 topic pub -w 1` は「誰か 1 つ」の購読者で
# 送ってしまうので、Discovery が遅れている（同じネットワークの別コンテナが多いと起きる）と指令が届かない
# 購読者がちょうど 1 つであることも確かめる（シミュの二重起動や実機ドライバと混ざっていないこと）
controller_count() {
  lab "timeout 25 ros2 topic info -v --no-daemon /crane_x7_arm_controller/joint_trajectory 2>/dev/null \
       | grep -c 'Node name: crane_x7_arm_controller'" < /dev/null 2>/dev/null || true
}
controller_seen() {
  local n
  # ros2lab のログインシェルは最初にバナー行（[ros2lab] DOMAIN=...）を出すので、数値の行だけを取る
  n="$(controller_count | grep -E '^[0-9]+$' | tail -n 1 || true)"
  [ "${n:-0}" -gt 1 ] && fail_env "crane_x7_arm_controller が ${n} 個見える（シミュの二重起動か実機ドライバと混在）。シミュを止めて起動し直す"
  [ "${n:-0}" -eq 1 ]
}
deadline=$(( $(date +%s) + TIMEOUT ))
until controller_seen; do
  if [ "$(date +%s)" -ge "$deadline" ]; then
    fail_env "ros2lab-a から crane_x7_arm_controller が見えない（Discovery の問題）。数十秒待ってから再実行する。"
  fi
  echo "ros2lab-a から crane_x7_arm_controller が見えるのを待つ"
  sleep 5
done

REPEAT_ARG=""
[ -n "$REPEAT" ] && REPEAT_ARG="--repeat $REPEAT"
rc=0
arm "ros2 run ros2_poc_sim scenario_cli commands $RUN/scenario.yaml $REPEAT_ARG --steps-out $RUN/steps.json" \
  > "$RUN_HOST/commands.tsv" < /dev/null || rc=$?
if [ "$rc" = 64 ]; then
  echo "シナリオの検証に失敗した（上のメッセージ参照）" >&2
  exit 64
fi
[ "$rc" = 0 ] || fail_env "シナリオを展開できない（rc=$rc）"
# 送るコマンドは scenario.py が作る 2 種類の形に限る（YAML の値は数値に変換済み。生成側の検証が漏れても
# ここで止める）。bash -c の文字列に入るので、シェルの特殊文字が混ざったら実行しない
# 送信先・トピック・型まで固定し、メッセージ部分は二重引用符の中の数値・名前・記号だけを許す
# （引用の中なのでグロブやブレース展開も起きない）
CMD_RE_LAB='^ros2 topic pub -w 1 --times 3 -r 2 /crane_x7_arm_controller/joint_trajectory trajectory_msgs/msg/JointTrajectory "[][A-Za-z0-9_ .:{},-]+"$'
CMD_RE_SIM='^ros2 action send_goal /crane_x7_gripper_controller/gripper_cmd control_msgs/action/ParallelGripperCommand "[][A-Za-z0-9_ .:{},-]+"$'
while IFS=$'\t' read -r name target wait cmd; do
  [[ "$name" =~ ^[A-Za-z0-9_#-]{1,48}$ ]] || fail_env "想定外のステップ名なので実行しない: $name"
  [[ "$wait" =~ ^[0-9]+(\.[0-9]+)?$ ]] || fail_env "想定外の待ち秒なので実行しない: $wait"
  case "$target" in
    lab) re="$CMD_RE_LAB" ;;
    sim) re="$CMD_RE_SIM" ;;
    *) fail_env "不明な送信先なので実行しない: $target" ;;
  esac
  (export LC_ALL=C; [[ "$cmd" =~ $re ]]) || fail_env "想定外の形のコマンドなので実行しない: $cmd"
done < "$RUN_HOST/commands.tsv"
MAX_SEC="$(arm "ros2 run ros2_poc_sim scenario_cli budget $RUN" < /dev/null)" || fail_env "記録時間の上限を計算できない"
[[ "$MAX_SEC" =~ ^[0-9]+$ ]] || fail_env "記録時間の上限が数値でない: $MAX_SEC"

rec_start "$MAX_SEC"

SEND_FAILED=0
: > "$RUN_HOST/events.jsonl"
: > "$RUN_HOST/commands.log"
total=$(wc -l < "$RUN_HOST/commands.tsv" | tr -d ' ')
i=0
while IFS=$'\t' read -r name target wait cmd <&3; do
  i=$((i + 1))
  echo "[$i/$total] $name ($target)"
  echo "$(date '+%H:%M:%S') [$target] \$ $cmd" | tee -a "$RUN_HOST/commands.log"
  # 送信前後の時刻はコンテナ内で取る（コンテナは Colima VM の時計を共有し、記録ノードと揃う）
  script="date +%s.%N; timeout 30 $cmd > /dev/null 2>&1; rc=\$?; date +%s.%N; exit \$rc"
  rc=0
  out="$(send "$target" "$script" < /dev/null)" || rc=$?
  stamps="$(printf '%s\n' "$out" | grep -E '^[0-9]+\.[0-9]+$' || true)"
  t_start="$(printf '%s\n' "$stamps" | sed -n 1p)"
  t_sent="$(printf '%s\n' "$stamps" | sed -n 2p)"
  if [ -z "$t_start" ] || [ -z "$t_sent" ]; then
    echo "  送信時刻が取れない（rc=$rc）" >&2
    [ "$rc" = 0 ] && rc=2
    t_start="${t_start:-0}"; t_sent="${t_sent:-$t_start}"
  fi
  if [ "$rc" != 0 ]; then
    echo "  命令が失敗した（rc=$rc）" >&2
    SEND_FAILED=$((SEND_FAILED + 1))
  fi
  # name は scenario.py で英数字・_・-（と repeat の #k）に限られるので JSON の文字列にそのまま入れてよい
  printf '{"index": %d, "name": "%s", "target": "%s", "t_start": %s, "t_sent": %s, "rc": %d}\n' \
    "$((i - 1))" "$name" "$target" "$t_start" "$t_sent" "$rc" >> "$RUN_HOST/events.jsonl"
  # 静止するまで待つ（シミュは実時間より遅いので指令時間では足りない）。wait が使えなければ目安の秒数
  arm "ros2 run ros2_poc_sim scenario_cli wait $RUN $((i - 1))" < /dev/null || sleep "$wait"
done 3< "$RUN_HOST/commands.tsv"

rec_stop_and_judge
rec_compose
if [ "$SEND_FAILED" -gt 0 ]; then
  # 判定（result.json）は残すが、指令が届いていないので動作の FAIL ではなく実行時の問題として返す
  fail_env "指令を送れなかったステップが ${SEND_FAILED} 個ある（result.json の send_failed）"
fi
exit "$judge_rc"
