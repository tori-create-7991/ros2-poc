#!/usr/bin/env bash
# verify-env.sh から source して使う検証の部品。
# コンテナ名は環境変数で差し替えられる（別の compose project で試すとき用）:
#   LAB_A（既定 ros2lab-a） LAB_B（既定 ros2lab-b） DIAG（既定 ros2diag）

LAB_A="${LAB_A:-ros2lab-a}"
LAB_B="${LAB_B:-ros2lab-b}"
DIAG="${DIAG:-ros2diag}"
DIAG_IMAGE="${DIAG_IMAGE:-ros2diag:jazzy}"
# shellcheck disable=SC2034  # verify-env.sh が使う
TOPIC_STATE="lab/state"   # ros2lab-a が publish、ros2lab-b が subscribe（policy lab-c.xml）
# shellcheck disable=SC2034  # verify-env.sh が使う
PAYLOAD="LABPAYLOAD-7F3A" # sros2/tools/lab_pub.py が送る文字列（pcap で平文かを見る）

PASS_COUNT=0
FAIL_COUNT=0
# OUT（証拠の置き場）と TOOLS（sros2/tools の場所）は verify-env.sh が設定してから source する
: "${OUT:?OUT が未設定}" "${TOOLS:?TOOLS が未設定}"

log() { echo "$*" | tee -a "$OUT/result.txt"; }
pass() { PASS_COUNT=$((PASS_COUNT + 1)); log "PASS $1: $2"; }
fail() { FAIL_COUNT=$((FAIL_COUNT + 1)); log "FAIL $1: $2"; }

# check <id> <説明> <条件コマンド...>  条件コマンドが 0 を返せば PASS
check() {
  local id="$1" desc="$2"
  shift 2
  if "$@"; then pass "$id" "$desc"; else fail "$id" "$desc"; fi
}

dx() { docker exec "$@"; }

# 起動中か
running() { [ -n "$(docker ps --filter "name=^$1\$" --filter status=running -q)" ]; }

# コンテナ <c> の環境変数 <name> の値（無ければ空）
env_of() { docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$1" | sed -n "s/^$2=//p"; }

label_of() { docker inspect -f '{{index .Config.Labels "ros2poc.env"}}' "$1"; }

# publisher をバックグラウンドで起動する。  pub_bg <コンテナ> <topic> <秒> [docker exec の引数...]
# 出力は LAST_PUB_LOG に残る。publisher が起動できたか（PUB_UP）は rejected / pub_started で確認する。
# 「0 件受信 = 拒否された」と判定するときに、publisher が起動すらしていなかった場合を除くため。
LAST_PUB_LOG=""
pub_bg() {
  local c="$1" topic="$2" secs="$3"
  shift 3
  LAST_PUB_LOG="$OUT/pub-$RANDOM-$RANDOM.log"
  docker exec -i "$@" "$c" bash -lc "python3 - $topic $secs pub_$RANDOM" \
    < "$TOOLS/lab_pub.py" > "$LAST_PUB_LOG" 2>&1 &
}

# 最後に起動した publisher が起動できたか
pub_started() { grep -q PUB_UP "${1:-$LAST_PUB_LOG}"; }

# 拒否された: 受信が 0 件で、publisher は起動できていた（起動していないための 0 件を除く）。 rejected <受信数> <publisher のログ>
rejected() { [ "$1" = "0" ] && pub_started "$2"; }
# 鍵なしの参加者用: 「受信 0 件・見えた publisher 0」で、publisher は起動できていた。 rejected_pair "<受信数> <見えた数>" <ログ>
rejected_pair() { [ "$1" = "0 0" ] && pub_started "$2"; }

# 不正証明書の生成。失敗したら握りつぶさず FAIL にする（古い rogue が残っていて通ったり、無くて偽の拒否になるのを防ぐ）
must_gen_rogue() {
  if ! bash scripts/sros2/gen-rogue.sh "$@" >> "$OUT/gen-rogue.log" 2>&1; then
    fail GEN-ROGUE "不正証明書の生成に失敗した（$*）。$OUT/gen-rogue.log を見ること"
    return 1
  fi
}

# パケット取得のサイドカーが残っていたら消す（中断されたとき用）
cleanup_caps() {
  local ids
  ids="$(docker ps -aq --filter 'name=^cap-')"
  # shellcheck disable=SC2086
  [ -z "$ids" ] || docker rm -f $ids >/dev/null 2>&1 || true
}

# WIPE_ROGUE=1 のとき、終了時に不正証明書を削除する（診断コンテナに残さない）
wipe_if_requested() {
  if [ "${WIPE_ROGUE:-0}" = "1" ]; then bash scripts/sros2/wipe-rogue.sh >/dev/null 2>&1 || true; fi
}

# subscriber を実行して「受信数 見えた publisher 数」を返す。  sub_run <コンテナ> <topic> <秒> [docker exec の引数...]
sub_run() {
  local c="$1" topic="$2" secs="$3"
  shift 3
  docker exec -i "$@" "$c" bash -lc "python3 - $topic $secs sub_$RANDOM" < "$TOOLS/lab_sub.py" 2>&1 \
    | tee -a "$OUT/sub.log" \
    | sed -n 's/.*RECEIVED \([0-9]*\) PUBLISHERS_SEEN \([0-9]*\).*/\1 \2/p' | tail -1
}

# 診断コンテナから、指定の鍵（rogue）を提示するための docker exec の引数
rogue_args() { # <env> <case> <enclave>
  echo "-e ROS_SECURITY_ENABLE=true -e ROS_SECURITY_STRATEGY=Enforce -e ROS_SECURITY_KEYSTORE=/sros2/rogue/$1/$2 -e ROS_SECURITY_ENCLAVE_OVERRIDE=$3"
}

# 対象コンテナのネットワーク名前空間でパケットを取る（サイドカー）。  cap_start <コンテナ> <名前>
cap_start() {
  docker rm -f "$2" >/dev/null 2>&1 || true
  docker run -d --name "$2" --network "container:$1" --cap-add NET_RAW --cap-add NET_ADMIN \
    -v "$OUT:/out" --entrypoint tcpdump "$DIAG_IMAGE" -i any -nn -U -w "/out/$2.pcap" >/dev/null
  sleep 1
}
cap_stop() {
  docker stop -t 2 "$1" >/dev/null 2>&1 || true
  docker rm -f "$1" >/dev/null 2>&1 || true
}

# pcap の中の文字列の出現回数
pcap_count() { grep -a -c -- "$2" "$1" || true; }

# コンテナ内の証明書のフィンガープリント
cert_fp() { dx "$1" openssl x509 -in "$2" -noout -fingerprint -sha256 | sed 's/.*=//'; }

# コンテナ内の証明書の有効期間（日）
cert_days() {
  dx -i "$1" python3 - "$2" <<'PY'
import datetime
import subprocess
import sys

out = subprocess.check_output(
    ["openssl", "x509", "-in", sys.argv[1], "-noout", "-startdate", "-enddate"], text=True
)
d = dict(line.split("=", 1) for line in out.strip().splitlines())
fmt = "%b %d %H:%M:%S %Y %Z"
s = datetime.datetime.strptime(" ".join(d["notBefore"].split()), fmt)
e = datetime.datetime.strptime(" ".join(d["notAfter"].split()), fmt)
print(round((e - s).total_seconds() / 86400))
PY
}
