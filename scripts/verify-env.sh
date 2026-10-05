#!/usr/bin/env bash
# 環境 A / B / C が「その環境になっている」ことを確認する。
#   bash scripts/verify-env.sh a|b|c
# 事前に `bash scripts/up-env.sh <env>` で ros2lab-a/b を起動し、診断コンテナを起動しておく:
#   docker compose --profile diag up -d --build ros2diag
# 結果（PASS/FAIL）と証拠（pcap・ログ）は .artifacts/sros2-verify/<env>-<日時>/ に残る。
# 全項目 PASS なら exit 0。
# コンテナ名は LAB_A / LAB_B / DIAG で差し替えられる（別の compose project で試すとき用）。
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1

usage() {
  echo "Usage: bash scripts/verify-env.sh a|b|c" >&2
  exit 2
}

[ "$#" -eq 1 ] || usage
ENV_NAME="$1"
case "$ENV_NAME" in a | b | c) ;; *) usage ;; esac

TOOLS="$PWD/sros2/tools"
OUT="$PWD/.artifacts/sros2-verify/${ENV_NAME}-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$OUT"

# shellcheck source=scripts/sros2/lib/probe.sh
. scripts/sros2/lib/probe.sh

require_running() {
  local c
  for c in "$@"; do
    if ! running "$c"; then
      echo "$c が起動していない。先に scripts/up-env.sh $ENV_NAME と 'docker compose --profile diag up -d --build ros2diag' を実行すること。" >&2
      exit 1
    fi
  done
}

# 条件（check に渡す）
eq() { [ "$1" = "$2" ]; }
is_zero() { [ "${1:-x}" = "0" ]; }
gt_zero() { [ "${1:-0}" -gt 0 ] 2>/dev/null; }
le() { [ "$1" -le "$2" ] 2>/dev/null; }
nonempty_and_differ() { [ -n "$1" ] && [ "$1" != "$2" ]; }

# governance: 未認証を許可しない・RTPS / discovery が ENCRYPT・保護なし（NONE）がない
gov_ok() {
  grep -q "<allow_unauthenticated_participants>false<" <<<"$1" &&
    grep -q "<rtps_protection_kind>ENCRYPT<" <<<"$1" &&
    grep -q "<discovery_protection_kind>ENCRYPT<" <<<"$1" &&
    ! grep -Eq "_protection_kind>NONE<" <<<"$1"
}

# permissions: ワイルドカードなし・default が DENY
perm_ok() {
  ! grep -Eq "<topic>[^<]*\*" <<<"$1" && grep -q "<default>DENY</default>" <<<"$1"
}

# pcap に RTPS の通信はあるが payload は平文で出ない（$1: RTPS の出現数、$2: payload の出現数）
encrypted_ok() { [ "$1" -gt 0 ] && [ "$2" -eq 0 ]; }

# --- 環境 A ---
verify_a() {
  require_running "$LAB_A" "$LAB_B" "$DIAG"
  check A1 "ros2lab-a/b の環境ラベルが a" eq "$(label_of "$LAB_A")$(label_of "$LAB_B")" "aa"
  check A2 "ROS_SECURITY_* が設定されていない" eq "$(env_of "$LAB_A" ROS_SECURITY_ENABLE)$(env_of "$LAB_B" ROS_SECURITY_ENABLE)$(env_of "$LAB_A" ROS_SECURITY_STRATEGY)" ""

  cap_start "$LAB_A" cap-a
  pub_bg "$LAB_A" "$TOPIC_STATE" 14
  sleep 3
  local r
  r="$(sub_run "$DIAG" "$TOPIC_STATE" 7)"
  wait
  cap_stop cap-a
  echo "keyless diag -> lab-a: $r" >> "$OUT/result.txt"
  check A3 "鍵なしの診断機が ros2lab-a の publisher を見て受信できる（見える）" gt_zero "${r%% *}"
  check A4 "pcap に payload が平文で出る" gt_zero "$(pcap_count "$OUT/cap-a.pcap" "$PAYLOAD")"
}

# --- 環境 C ---
verify_c() {
  require_running "$LAB_A" "$LAB_B" "$DIAG"
  local ks=/run/sros2/keystore/enclaves/lab
  local ea="$ks/ros2lab_a" eb="$ks/ros2lab_b"

  check C1 "ros2lab-a/b の環境ラベルが c" eq "$(label_of "$LAB_A")$(label_of "$LAB_B")" "cc"
  check C2 "STRATEGY=Enforce / ENABLE=true / RMW=rmw_fastrtps_cpp" eq \
    "$(env_of "$LAB_A" ROS_SECURITY_STRATEGY)/$(env_of "$LAB_A" ROS_SECURITY_ENABLE)/$(env_of "$LAB_A" RMW_IMPLEMENTATION)" \
    "Enforce/true/rmw_fastrtps_cpp"

  # 正規の通信
  pub_bg "$LAB_A" "$TOPIC_STATE" 14
  sleep 3
  local r
  r="$(sub_run "$LAB_B" "$TOPIC_STATE" 7)"
  wait
  check C3 "認証済みの ros2lab-a → ros2lab-b は通信できる（受信 ${r%% *} 件）" gt_zero "${r%% *}"

  # 鍵なしの参加者
  pub_bg "$LAB_A" "$TOPIC_STATE" 14
  sleep 3
  r="$(sub_run "$DIAG" "$TOPIC_STATE" 7)"
  wait
  check C4 "鍵なしの参加者は拒否される（受信 ${r%% *} 件 / 見えた publisher ${r##* }）" eq "$r" "0 0"

  # 不正証明書（AU-3）。対照（valid）が通ることで、試験が成立していることを確かめる
  bash scripts/sros2/gen-rogue.sh c all >/dev/null 2>&1 || true
  local c
  for c in valid wrongca selfsigned revoked; do
    # shellcheck disable=SC2046
    pub_bg "$DIAG" "$TOPIC_STATE" 14 $(rogue_args c "$c" /lab/ros2lab_a)
    sleep 3
    r="$(sub_run "$LAB_B" "$TOPIC_STATE" 7)"
    wait
    if [ "$c" = valid ]; then
      check C5-valid "対照: 正規 CA が発行した有効な証明書は参加できる（受信 ${r%% *} 件）" gt_zero "${r%% *}"
    else
      check "C5-$c" "不正証明書（$c）は拒否される（受信 ${r%% *} 件）" is_zero "${r%% *}"
    fi
  done
  # 期限切れ: 期限内に提示側を起動し、期限後に検証側を起動する
  bash scripts/sros2/gen-rogue.sh c expired 40 >/dev/null 2>&1 || true
  # shellcheck disable=SC2046
  pub_bg "$DIAG" "$TOPIC_STATE" 75 $(rogue_args c expired /lab/ros2lab_a)
  sleep 5
  r="$(sub_run "$LAB_B" "$TOPIC_STATE" 5)"
  check C5-expired-pre "対照: 期限前は参加できる（受信 ${r%% *} 件）" gt_zero "${r%% *}"
  sleep 40
  r="$(sub_run "$LAB_B" "$TOPIC_STATE" 8)"
  wait
  check C5-expired "不正証明書（expired）は期限後に拒否される（受信 ${r%% *} 件）" is_zero "${r%% *}"

  # 資格情報の一意性とライフサイクル
  local fa fb
  fa="$(cert_fp "$LAB_A" "$ea/cert.pem")"
  fb="$(cert_fp "$LAB_B" "$eb/cert.pem")"
  echo "cert fingerprint a=$fa b=$fb" >> "$OUT/result.txt"
  check C6 "証明書がコンテナごとに異なる" nonempty_and_differ "$fa" "$fb"
  local days
  days="$(cert_days "$LAB_A" "$ea/cert.pem")"
  check C7 "証明書の有効期間が 91 日以内（${days} 日）" le "${days:-9999}" 91
  local crl_ok
  crl_ok="$(dx -i "$LAB_A" python3 - "$ea" <<'PY'
import datetime, subprocess, sys
d = sys.argv[1]
def dt(s):
    return datetime.datetime.strptime(" ".join(s.split()), "%b %d %H:%M:%S %Y %Z")
crl = subprocess.check_output(["openssl", "crl", "-in", d + "/crl.pem", "-noout", "-nextupdate"], text=True)
nxt = dt(crl.strip().split("=", 1)[1])
cert = subprocess.check_output(["openssl", "x509", "-in", d + "/cert.pem", "-noout", "-enddate"], text=True)
end = dt(cert.strip().split("=", 1)[1])
txt = subprocess.check_output(["openssl", "crl", "-in", d + "/crl.pem", "-noout", "-text"], text=True)
revoked = txt.count("Serial Number")
print("ok" if (nxt > datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) and nxt > end and revoked >= 1) else "ng nextUpdate=%s certEnd=%s revoked=%d" % (nxt, end, revoked))
PY
)"
  check C8 "CRL がある・nextUpdate が未来で証明書の期限より後・失効エントリがある（$crl_ok）" eq "$crl_ok" "ok"

  # governance / permissions の静的解析
  local gov perm
  gov="$(dx "$LAB_A" openssl smime -verify -in "$ea/governance.p7s" -CAfile "$ea/permissions_ca.cert.pem" -text 2>/dev/null)"
  perm="$(dx "$LAB_A" openssl smime -verify -in "$ea/permissions.p7s" -CAfile "$ea/permissions_ca.cert.pem" -text 2>/dev/null)"
  printf '%s\n' "$gov" > "$OUT/governance.txt"
  printf '%s\n' "$perm" > "$OUT/permissions.txt"
  check C9 "governance: 未認証を許可しない・RTPS と discovery が ENCRYPT・保護なし(NONE)がない" gov_ok "$gov"
  check C10 "permissions: ワイルドカードなし・default が DENY" perm_ok "$perm"

  # 平文の露出（pcap）
  cap_start "$LAB_A" cap-c
  pub_bg "$LAB_A" "$TOPIC_STATE" 14
  sleep 3
  sub_run "$LAB_B" "$TOPIC_STATE" 7 > /dev/null
  wait
  cap_stop cap-c
  check C11 "pcap に RTPS の通信はあるが payload は平文で出ない" encrypted_ok \
    "$(pcap_count "$OUT/cap-c.pcap" "RTPS")" "$(pcap_count "$OUT/cap-c.pcap" "$PAYLOAD")"

  # 鍵の分離とパーミッション
  local keys
  keys="$(dx "$LAB_A" find / -path /proc -prune -o \( -name key.pem -o -name 'ca.key.pem' -o -name '*_ca.key.pem' \) -print 2>/dev/null | sort | tr '\n' ' ')"
  echo "lab-a が読める鍵: $keys" >> "$OUT/result.txt"
  check C12 "ros2lab-a から見える秘密鍵は自分の enclave だけ（CA の秘密鍵・他コンテナの鍵は見えない）" \
    eq "$keys" "$ea/key.pem /sros2/src/key.pem "
  check C13 "秘密鍵が 0600、鍵のあるディレクトリが 0700" eq \
    "$(dx "$LAB_A" stat -c '%a' "$ea/key.pem" "$ea" | tr '\n' ' ')" "600 700 "
}

# --- 環境 B ---
verify_b() {
  require_running "$LAB_A" "$LAB_B" "$DIAG"
  fail B0 "環境 B の検証は未実装"
}

"verify_$ENV_NAME"

echo
echo "結果: PASS=$PASS_COUNT FAIL=$FAIL_COUNT（証拠: ${OUT#"$PWD"/}）"
[ "$FAIL_COUNT" -eq 0 ]
