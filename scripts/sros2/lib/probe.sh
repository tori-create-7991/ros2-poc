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
pub_bg() {
  local c="$1" topic="$2" secs="$3"
  shift 3
  docker exec -i "$@" "$c" bash -lc "python3 - $topic $secs pub_$RANDOM" \
    < "$TOOLS/lab_pub.py" >> "$OUT/pub.log" 2>&1 &
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
