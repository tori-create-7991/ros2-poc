#!/usr/bin/env bash
# shellcheck shell=bash
# 記録・判定・動画合成の共通部。scripts/run-scenario.sh と scripts/run-vla.sh --record が source して使う
# （単独では実行しない）。docker で ros2arm に入って scenario_observer / x11grab を起動し、止めて、判定して、合成する。
#
# 呼び出し側が source の前に定義しておくもの:
#   arm      ros2arm（ubuntu、DISPLAY=:1、ROS 環境読み込み済み）で bash -c を実行する関数
#   arm_bg   同じく背景（docker exec -d）で実行する関数
#   fail_env メッセージを stderr に出して exit 2 する関数
# 呼び出し側が先に決めておくもの:
#   REC_LABEL ロックの持ち主を表す名前（メッセージ用。既定 run-scenario）
# rec_init_run のあとに読めるもの: TS WORKSPACE_HOST RUN_HOST RUN。判定のあとは judge_rc。
#
# コマンド文字列（pgrep -f / pkill のパターン、ロックの mkdir・rm）は scripts/test-run-scenario.sh が見る。
# 変えるときはテストと一緒に直す。bash 3.2（macOS 標準）で動くこと。

REC_LABEL="${REC_LABEL:-run-scenario}"
REC_LOCK=/tmp/run-scenario.lock
HAVE_ARM_LOCK=0   # ロックを取れたか（取れた分だけ無条件に外す）
LOCK_ATTEMPT=0    # 取得の途中か（docker exec の往復中にシグナルが来た場合に備え、所有者が自分のときだけ外す）
RECORDING=0     # 記録プロセスを起動したか
STOPPED=0       # 記録プロセスが止まったことを確かめたか
STOP_GAVE_UP=0  # KILL まで送っても止まらなかったか（後片付けで同じ待ちを繰り返さない）

# 録画・合成に要る外部コマンド・フォント（ベースイメージ由来）を先に確かめる
rec_doctor() {
  arm "ros2 run ros2_poc_sim scenario_cli doctor" < /dev/null \
    || fail_env "ros2arm に録画・合成の前提が揃っていない（上のメッセージ参照。'bash scripts/up-arm.sh' でイメージを作り直す）"
}

# 実行ディレクトリ（TS / RUN_HOST / RUN）と、記録プロセスを探すパターンを決める
rec_init_run() {
  TS="$(date +%Y%m%d-%H%M%S)"
  # ホスト側の出力先は ros2arm の /workspace のマウント元（up-arm.sh を実行した checkout の ./workspace）。
  # このスクリプトのある checkout とは限らない（git worktree など）。テストでは RUN_SCENARIO_WORKSPACE で差し替える
  WORKSPACE_HOST="${RUN_SCENARIO_WORKSPACE:-$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/workspace"}}{{.Source}}{{end}}{{end}}' ros2arm 2>/dev/null || true)}"
  if [ -z "$WORKSPACE_HOST" ] || [ ! -d "$WORKSPACE_HOST" ]; then
    fail_env "ros2arm の /workspace のマウント元が分からない（${WORKSPACE_HOST:-空}）。'bash scripts/up-arm.sh' で作り直す"
  fi
  RUN_HOST="$WORKSPACE_HOST/runs/$TS"
  RUN="/workspace/runs/$TS"
  # 記録プロセス（observer・カメラ用 ffmpeg・デスクトップ録画）。[s] などは pkill / pgrep 自身を呼ぶ
  # bash -c のコマンドラインに一致させないため。ANY_* は他の実行（前回の残り）も含めて探すとき用
  # ロックの持ち主（取得の前に決めておく。トラップが取得の途中でも所有者を照合して外せる）
  OWNER="$TS $(hostname -s 2>/dev/null || echo host) $$"
  RECORDERS="[s]cenario_observer --out $RUN|[r]awvideo.*$RUN/camera\.mp4|[x]11grab.*$RUN/desktop\.mp4"
  # 最初の停止指示（INT）はカメラ用 ffmpeg に直接送らない。observer が入力を閉じれば ffmpeg は残りを
  # 書き出して終わる。ffmpeg に INT を送ると読み残したフレーム（最後のステップの判定用）が落ちる
  RECORDERS_INT="[s]cenario_observer --out $RUN|[x]11grab.*$RUN/desktop\.mp4"
  ANY_RECORDERS='[s]cenario_observer --out /workspace/runs/|[r]awvideo.*/workspace/runs/[0-9-]*/camera\.mp4|[x]11grab.*/workspace/runs/[0-9-]*/desktop\.mp4'
}

# 同時実行の拒否（同じアームに 2 本の指令が混ざる）。ロックは ros2arm の /tmp に置く。
# コンテナを作り直す（up-arm.sh）と消えるが、docker restart や Colima の再起動では残る
rec_lock_acquire() {
  local owner started owner_host owner_pid
  LOCK_ATTEMPT=1
  if ! arm "mkdir $REC_LOCK && echo '$OWNER' > $REC_LOCK/owner" < /dev/null 2>/dev/null; then
    LOCK_ATTEMPT=0   # 取れなかった（他人のロックは外さない）
    owner="$(arm "cat $REC_LOCK/owner" < /dev/null 2>/dev/null || true)"
    read -r started owner_host owner_pid <<<"${owner:-}" || true
    # 記録中、または同じホストで持ち主のプロセスが生きている（シミュ起動待ちなど記録前の段階）なら実行中
    if arm "pgrep -f '$ANY_RECORDERS'" > /dev/null 2>&1 < /dev/null \
       || { [ "${owner_host:-}" = "$(hostname -s 2>/dev/null || echo host)" ] && [ -n "${owner_pid:-}" ] \
            && kill -0 "$owner_pid" 2>/dev/null; }; then
      fail_env "別の $REC_LABEL が実行中（開始 ${started:-不明}）。実行中でないと確かめられたら外す: docker exec ros2arm rm -r $REC_LOCK"
    fi
    fail_env "前回の $REC_LABEL のロックが残っている（開始 ${started:-不明}、記録プロセスは無い）。実行中でなければ外す: docker exec ros2arm rm -r $REC_LOCK"
  fi
  LOCK_ATTEMPT=0
  HAVE_ARM_LOCK=1
}
# shellcheck disable=SC2317,SC2329  # trap から呼ぶ
release_lock() {
  if [ "$HAVE_ARM_LOCK" = 1 ]; then
    arm "rm -r $REC_LOCK" < /dev/null > /dev/null 2>&1 || true
    HAVE_ARM_LOCK=0
  elif [ "$LOCK_ATTEMPT" = 1 ]; then
    # 取得の途中でシグナルが来た: コンテナ側で mkdir が済んでいたら漏れるので、所有者が自分のときだけ外す
    arm "grep -qxF '$OWNER' $REC_LOCK/owner && rm -r $REC_LOCK" < /dev/null > /dev/null 2>&1 || true
    LOCK_ATTEMPT=0
  fi
}

# 前回の記録プロセスが残っていたら止めてもらう（残るとシミュがさらに遅くなる）
rec_check_leftovers() {
  if arm "pgrep -f '$ANY_RECORDERS'" > /dev/null 2>&1 < /dev/null; then
    fail_env "前回の記録プロセスが残っている。止めてから再実行する: docker exec ros2arm pkill -INT -f '$ANY_RECORDERS'"
  fi
}

stop_recorders() {
  # observer は ffmpeg の書き出しを最大 60 秒待つので、それより長く待ってから TERM → KILL に上げる
  [ "$RECORDING" = 1 ] && [ "$STOPPED" = 0 ] || return 0
  [ "$STOP_GAVE_UP" = 0 ] || return 1
  echo "記録を止めている（最大 2 分ほどかかる）"
  local sig
  for sig in INT TERM KILL; do
    local pattern="$RECORDERS"
    if [ "$sig" = INT ]; then pattern="$RECORDERS_INT"; fi
    arm "pkill -$sig -f '$pattern'" < /dev/null > /dev/null 2>&1 || true
    for _ in $(seq 1 "$([ "$sig" = INT ] && echo 90 || echo 10)"); do
      if ! arm "pgrep -f '$RECORDERS'" > /dev/null 2>&1 < /dev/null; then
        STOPPED=1
        return 0
      fi
      sleep 1
    done
    echo "記録プロセスが SIG$sig で止まらない" >&2
  done
  STOP_GAVE_UP=1
  return 1
}

# 後片付け: コンテナ内の wait を止め、記録プロセスを止め、ロックを外す
rec_cleanup() {
  # docker exec は（TTY なしでは）シグナルを転送しないので、コンテナ内の wait は明示的に止める
  [ "$RECORDING" = 0 ] || arm "pkill -f '[s]cenario_cli wait $RUN '" < /dev/null > /dev/null 2>&1 || true
  stop_recorders || true
  release_lock
}

# ros2arm の ubuntu が書けるよう、ディレクトリはコンテナ側で作る（/workspace は root と ubuntu が混在する）。
# Linux ホストではホストのユーザーが ubuntu(uid 1000) と別になりうるので、ホスト側からも書けるよう 777 にする
rec_make_run_dir() {
  arm "mkdir -p $RUN && chmod 777 $RUN" < /dev/null || fail_env "出力先を作れない: $RUN_HOST"
}

topic_ok() {
  arm "timeout 15 ros2 topic echo --once --field header.stamp $1 > /dev/null 2>&1" < /dev/null
}
sim_ready() { topic_ok /joint_states && topic_ok /camera/color/image_raw; }

# シミュのトピックが流れるまで待つ。$1 = 待つ秒数、$2 = 時間切れのときに添える案内
rec_wait_sim() {
  local deadline
  echo "トピック（/joint_states, /camera/color/image_raw）を待つ（最大 ${1} 秒）"
  deadline=$(( $(date +%s) + $1 ))
  until sim_ready; do
    if [ "$(date +%s)" -ge "$deadline" ]; then
      fail_env "トピックが流れない。$2"
    fi
    sleep 5
  done
}

# 記録プロセス（observer とデスクトップ録画）を起動し、カメラのフレームが 4 枚たまるまで待つ。$1 = 最大秒数
rec_start() {
  local max_sec="$1" size
  size="$(arm "xdpyinfo -display :1 | awk '/dimensions:/{print \$2}'" < /dev/null)"
  if [[ ! "$size" =~ ^[0-9]+x[0-9]+$ ]]; then
    fail_env "デスクトップ（DISPLAY=:1）の大きさが取れない（${size:-空}）。noVNC のデスクトップが起動しているか確認する。"
  fi
  # 記録プロセスは停止の指示が届かなくても max_sec 秒で止まる（-t / --max-duration）
  RECORDING=1
  arm_bg "exec ros2 run ros2_poc_sim scenario_observer --out $RUN --max-duration $max_sec > $RUN/observer.log 2>&1"
  arm_bg "date +%s.%N > $RUN/desktop_t0.txt; exec ffmpeg -y -v error -f x11grab -framerate 10 -video_size $size -t $max_sec -i :1 -c:v libx264 -preset ultrafast -pix_fmt yuv420p $RUN/desktop.mp4 2> $RUN/desktop_ffmpeg.log"
  # 記録の立ち上がり待ち: 送信前のフレームが要るので、カメラのフレームが記録され始めるまで待つ
  for _ in $(seq 1 60); do
    [ "$(rec_frames)" -ge 4 ] && break
    sleep 1
  done
  [ "$(rec_frames)" -ge 4 ] || fail_env "カメラのフレームが記録されない（$RUN_HOST/observer.log を確認）"
}
rec_frames() { { wc -l < "$RUN_HOST/camera_frames.csv"; } 2>/dev/null || echo 0; }

# 記録を止めて判定する。結果は judge_rc（0 = 全 PASS / 1 = FAIL あり）と result.json。判定できなければ exit 2
rec_stop_and_judge() {
  stop_recorders || fail_env "記録プロセスを止められないので判定しない（camera.mp4 が未完の可能性）"
  judge_rc=0
  arm "ros2 run ros2_poc_sim scenario_cli judge $RUN" < /dev/null || judge_rc=$?
  if [ "$judge_rc" -gt 1 ] || [ ! -f "$RUN_HOST/result.json" ]; then
    fail_env "判定に失敗した（rc=$judge_rc）"
  fi
}

# 判定結果と記録から動画を合成する。合成の失敗は判定結果（終了コード）を変えない。result.json が判定の正
rec_compose() {
  if arm "ros2 run ros2_poc_sim scenario_cli compose $RUN" < /dev/null; then
    echo "動画: $RUN_HOST/scenario.mp4"
  else
    echo "合成に失敗した（$RUN_HOST/overlay/filtergraph.txt を確認）。判定は result.json を見る" >&2
  fi
  echo "判定: $RUN_HOST/result.json"
}
