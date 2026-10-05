#!/usr/bin/env bash
# 認証が有効な環境（c）で、資格情報のない / 不正な参加者が加わろうとしたとき、
# 認証済みの相手が自分の証明書を含むハンドシェイクを平文で送ってくるかを tcpdump で観測する。
#   bash scripts/sros2/handshake-probe.sh
# 事前に scripts/up-env.sh c と `docker compose --profile diag up -d --build ros2diag` を済ませておく。
# 診断の強度は「参加者として加わる」まで（相手に不正な操作はしない）。
# 結果: パケットに ros2lab-a の証明書の本体（PEM）が出たか、subject 名が出たかを数える。
# コンテナ名は LAB_A / LAB_B / DIAG で差し替えられる。
set -uo pipefail

cd "$(dirname "$0")/../.." || exit 1

TOOLS="$PWD/sros2/tools"
OUT="$PWD/.artifacts/sros2-handshake/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$OUT"

# shellcheck source=scripts/sros2/lib/probe.sh
. scripts/sros2/lib/probe.sh
trap 'cleanup_caps; wipe_if_requested' EXIT

for c in "$LAB_A" "$LAB_B" "$DIAG"; do
  running "$c" || { echo "$c が起動していない" >&2; exit 1; }
done
must_gen_rogue c all || exit 1

# ros2lab-a の証明書に固有の行（PEM の 5 行目の base64。公開鍵の一部で、他の証明書とは一致しない。
# 1 行目は DER の定型の先頭で、同じ形式の別の証明書にも一致してしまう）。
# これが pcap に出れば、ros2lab-a の証明書が平文で送られている
CERT_LINE="$(dx "$LAB_A" sed -n 5p /run/sros2/keystore/enclaves/lab/ros2lab_a/cert.pem)"
SUBJECT="ros2lab_a"
echo "ros2lab-a の証明書に固有の行を使う（先頭 12 文字: ${CERT_LINE:0:12}）" | tee -a "$OUT/result.txt"

# probe <名前> <相手の起動方法: legit|keyless|rogue:<種別>>
# ros2lab-a（認証済み）の参加者を立て、相手を加わらせて、ros2lab-a 側のネットワークを取る。
# rogue は permissions 上 publish だけが許可されているので publisher として加わる。
probe() {
  local name="$1" kind="$2" r
  cap_start "$LAB_A" "cap-$name"
  pub_bg "$LAB_A" "$TOPIC_STATE" 16
  case "$kind" in
    legit) ;;
    keyless) pub_bg "$DIAG" "$TOPIC_STATE" 14 ;;
    rogue:*)
      # shellcheck disable=SC2046
      pub_bg "$DIAG" "$TOPIC_STATE" 14 $(rogue_args c "${kind#rogue:}" /lab/ros2lab_a)
      ;;
  esac
  sleep 3
  r="$(sub_run "$LAB_B" "$TOPIC_STATE" 9)"
  wait
  cap_stop "cap-$name"
  local pem cert sn
  pem="$(pcap_count "$OUT/cap-$name.pcap" "BEGIN CERTIFICATE")"
  cert="$(pcap_count "$OUT/cap-$name.pcap" "$CERT_LINE")"
  sn="$(pcap_count "$OUT/cap-$name.pcap" "$SUBJECT")"
  printf '%-14s 相手=%-18s lab-bの受信=%-3s PEM出現=%-3s ros2lab-aの証明書本体=%-3s subject名=%s\n' \
    "$name" "$kind" "${r%% *}" "$pem" "$cert" "$sn" | tee -a "$OUT/result.txt"
}

probe base legit
probe keyless keyless
probe selfsigned-1 rogue:selfsigned
probe selfsigned-2 rogue:selfsigned
probe wrongca-1 rogue:wrongca
probe wrongca-2 rogue:wrongca

echo "証拠: ${OUT#"$PWD"/}"
