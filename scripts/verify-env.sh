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

trap 'cleanup_caps; wipe_if_requested' EXIT

# 証明書と CRL の残り有効期間（日）を「cert=N crl=M」で返す
remaining_days() {
  dx -i "$1" python3 - "$2" <<'PY'
import datetime
import subprocess
import sys

d = sys.argv[1]
now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def dt(s):
    return datetime.datetime.strptime(" ".join(s.split()), "%b %d %H:%M:%S %Y %Z")


end = dt(subprocess.check_output(["openssl", "x509", "-in", d + "/cert.pem", "-noout", "-enddate"], text=True).strip().split("=", 1)[1])
nxt = dt(subprocess.check_output(["openssl", "crl", "-in", d + "/crl.pem", "-noout", "-nextupdate"], text=True).strip().split("=", 1)[1])
print("cert=%d crl=%d" % ((end - now).days, (nxt - now).days))
PY
}

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
  check C2 "ros2lab-a/b とも STRATEGY=Enforce / ENABLE=true / RMW=rmw_fastrtps_cpp / REQUIRE_CRL=true（鍵と CRL が無ければ起動しない）" eq \
    "$(env_of "$LAB_A" ROS_SECURITY_STRATEGY)/$(env_of "$LAB_A" ROS_SECURITY_ENABLE)/$(env_of "$LAB_A" RMW_IMPLEMENTATION)/$(env_of "$LAB_A" SROS2_REQUIRE_CRL) $(env_of "$LAB_B" ROS_SECURITY_STRATEGY)/$(env_of "$LAB_B" ROS_SECURITY_ENABLE)/$(env_of "$LAB_B" RMW_IMPLEMENTATION)/$(env_of "$LAB_B" SROS2_REQUIRE_CRL)" \
    "Enforce/true/rmw_fastrtps_cpp/true Enforce/true/rmw_fastrtps_cpp/true"

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
  check C4 "鍵なしの参加者は拒否される（受信 ${r%% *} 件 / 見えた publisher ${r##* }。認証済みの publisher は起動できている）" rejected_pair "$r" "$LAST_PUB_LOG"

  # 不正証明書。対照（valid）が通ることで、試験が成立していることを確かめる
  must_gen_rogue c all || return 1
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
      check "C5-$c" "不正証明書（$c）は拒否される（受信 ${r%% *} 件。提示側は起動できている）" rejected "${r%% *}" "$LAST_PUB_LOG"
    fi
  done
  # 期限切れ: 期限内に提示側を起動し、期限（EXPIRES_EPOCH）を過ぎてから検証側を起動する
  must_gen_rogue c expired 90 || return 1
  local EXPIRES_EPOCH=""
  # shellcheck disable=SC1091
  . sros2/rogue/c/expired/meta.env
  # shellcheck disable=SC2046
  pub_bg "$DIAG" "$TOPIC_STATE" 100 $(rogue_args c expired /lab/ros2lab_a)
  local expired_log="$LAST_PUB_LOG"
  sleep 5
  r="$(sub_run "$LAB_B" "$TOPIC_STATE" 5)"
  check C5-expired-pre "対照: 期限前は参加できる（受信 ${r%% *} 件）" gt_zero "${r%% *}"
  while [ "$(date +%s)" -lt "$((EXPIRES_EPOCH + 3))" ]; do sleep 1; done
  r="$(sub_run "$LAB_B" "$TOPIC_STATE" 8)"
  wait
  check C5-expired "不正証明書（expired）は期限後に拒否される（受信 ${r%% *} 件。提示側は起動できている）" rejected "${r%% *}" "$expired_log"

  # 資格情報の一意性とライフサイクル
  local fa fb
  fa="$(cert_fp "$LAB_A" "$ea/cert.pem")"
  fb="$(cert_fp "$LAB_B" "$eb/cert.pem")"
  echo "cert fingerprint a=$fa b=$fb" >> "$OUT/result.txt"
  check C6 "証明書がコンテナごとに異なる" nonempty_and_differ "$fa" "$fb"
  local days
  days="$(cert_days "$LAB_A" "$ea/cert.pem")"
  check C7 "ros2lab-a の証明書の有効期間が 91 日以内（${days} 日）" le "${days:-9999}" 91
  days="$(cert_days "$LAB_B" "$eb/cert.pem")"
  check C7b "ros2lab-b の証明書の有効期間が 91 日以内（${days} 日）" le "${days:-9999}" 91
  local idfp pmfp
  idfp="$(cert_fp "$LAB_A" "$ea/identity_ca.cert.pem")"
  pmfp="$(cert_fp "$LAB_A" "$ea/permissions_ca.cert.pem")"
  check C14 "identity CA と permissions CA が別（ca-structure）" nonempty_and_differ "$idfp" "$pmfp"
  local remain
  remain="$(remaining_days "$LAB_A" "$ea")"
  check C15 "証明書と CRL の残り有効期間が 14 日以上（${remain}）" bash_ge14 "$remain"
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
  r="$(sub_run "$LAB_B" "$TOPIC_STATE" 7)"
  wait
  cap_stop cap-c
  check C11 "pcap に RTPS の通信があり、受信（${r%% *} 件）もあるが、payload は平文で出ない" encrypted_ok \
    "$(pcap_count "$OUT/cap-c.pcap" "RTPS")" "$(pcap_count "$OUT/cap-c.pcap" "$PAYLOAD")" "${r%% *}"

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
# 台帳（sros2/ledger/ledger.yaml）の各不備が、本当にこの環境に注入されているかを確認する。
# probe_B_* は「不備が存在する」ときに 0 を返す（0 なら PASS = 正しく仕込まれている）。
B_ENCLAVE_DIR=/run/sros2/keystore/enclaves/lab/shared

b_governance() { dx "$LAB_A" openssl smime -verify -noverify -in "$B_ENCLAVE_DIR/governance.p7s" -text 2>/dev/null; }
b_permissions() { dx "$LAB_A" openssl smime -verify -noverify -in "$B_ENCLAVE_DIR/permissions.p7s" -text 2>/dev/null; }

# B-AU-01: 未認証参加者の許可（governance。静的に確認する）
probe_B_AU_01() { grep -q "<allow_unauthenticated_participants>true<" <<<"$(b_governance)"; }

# B-AU-02: ros2lab-b がセキュリティなしにフォールバックしている。
# Permissive で、指定の enclave のディレクトリが無く、鍵なしの参加者が ros2lab-b の publisher から受信できる
probe_B_AU_02() {
  [ "$(env_of "$LAB_B" ROS_SECURITY_STRATEGY)" = "Permissive" ] || return 1
  local enc
  enc="$(env_of "$LAB_B" ROS_SECURITY_ENCLAVE_OVERRIDE)"
  dx "$LAB_B" test ! -d "/run/sros2/keystore/enclaves$enc" || return 1
  pub_bg "$LAB_B" "$TOPIC_STATE" 14
  sleep 3
  local r
  r="$(sub_run "$DIAG" "$TOPIC_STATE" 7)"
  wait
  gt_zero "${r%% *}"
}

# B-AU-03: ros2lab-a と ros2lab-b が同じ証明書を使っている
probe_B_AU_03() {
  local fa fb
  fa="$(cert_fp "$LAB_A" "$B_ENCLAVE_DIR/cert.pem")"
  fb="$(cert_fp "$LAB_B" "$B_ENCLAVE_DIR/cert.pem")"
  [ -n "$fa" ] && [ "$fa" = "$fb" ]
}

# B-AU-04: 証明書が実質無期限（3000 日超）
probe_B_AU_04() { [ "$(cert_days "$LAB_A" "$B_ENCLAVE_DIR/cert.pem")" -gt 3000 ] 2>/dev/null; }

# B-AU-05: CRL がなく、失効済みの証明書を提示した参加者が受理される。
# 環境 B は未認証許可・RTPS 保護なしでもあるため、何でも受理する構成と区別するために、
# 同じ環境で別 CA の証明書が拒否される（= 検証自体は働いている）ことも確認する
probe_B_AU_05() {
  dx "$LAB_A" test ! -e "$B_ENCLAVE_DIR/crl.pem" || return 1
  local r
  # shellcheck disable=SC2046
  pub_bg "$DIAG" "$TOPIC_STATE" 14 $(rogue_args b revoked /lab/shared)
  sleep 3
  r="$(sub_run "$LAB_A" "$TOPIC_STATE" 7)"
  wait
  pub_started "$LAST_PUB_LOG" || return 1
  gt_zero "${r%% *}" || return 1
  # shellcheck disable=SC2046
  pub_bg "$DIAG" "$TOPIC_STATE" 14 $(rogue_args b wrongca /lab/shared)
  sleep 3
  r="$(sub_run "$LAB_A" "$TOPIC_STATE" 7)"
  wait
  echo "B-AU-05: 別 CA（wrongca）の受信=${r%% *}（0 なら検証は働いている）" >> "$OUT/result.txt"
  rejected "${r%% *}" "$LAST_PUB_LOG"
}

# B-AU-06: identity CA と permissions CA が同一
probe_B_AU_06() {
  local i p
  i="$(cert_fp "$LAB_A" "$B_ENCLAVE_DIR/identity_ca.cert.pem")"
  p="$(cert_fp "$LAB_A" "$B_ENCLAVE_DIR/permissions_ca.cert.pem")"
  [ -n "$i" ] && [ "$i" = "$p" ]
}

# B-AU-07: 別のコンテナ（ros2lab-b）から、共有領域の秘密鍵が読める。鍵のパーミッションが誰でも読める
probe_B_AU_07() {
  dx "$LAB_B" test -r /workspace/sros2-keystore/key.pem || return 1
  local mode
  mode="$(dx "$LAB_B" stat -c %a /workspace/sros2-keystore/key.pem)"
  [ "${mode: -1}" -ge 4 ] 2>/dev/null
}

# B-CR-01: lab/cmd だけ保護対象外（governance の規則）で、lab/cmd の payload は平文、lab/state は暗号化されている。
# 「平文が 0 件」は、通信が流れていなかっただけでも成り立つので、受信と RTPS のパケットがあることも条件にする
probe_B_CR_01() {
  grep -q "<topic_expression>rt/lab/cmd</topic_expression>" <<<"$(b_governance)" || return 1
  local cmd_hits state_hits cmd_recv state_recv cmd_rtps state_rtps r
  cap_start "$LAB_A" cap-cmd
  pub_bg "$LAB_A" lab/cmd 12
  sleep 2
  # 認証済み（shared の正規の証明書）の参加者が受信する
  # shellcheck disable=SC2046
  r="$(sub_run "$DIAG" lab/cmd 6 $(rogue_args b valid /lab/shared))"
  wait
  cap_stop cap-cmd
  cmd_recv="${r%% *}"
  cmd_hits="$(pcap_count "$OUT/cap-cmd.pcap" "$PAYLOAD")"
  cmd_rtps="$(pcap_count "$OUT/cap-cmd.pcap" "RTPS")"
  cap_start "$LAB_A" cap-state
  pub_bg "$LAB_A" "$TOPIC_STATE" 12
  sleep 2
  # shellcheck disable=SC2046
  r="$(sub_run "$DIAG" "$TOPIC_STATE" 6 $(rogue_args b valid /lab/shared))"
  wait
  cap_stop cap-state
  state_recv="${r%% *}"
  state_hits="$(pcap_count "$OUT/cap-state.pcap" "$PAYLOAD")"
  state_rtps="$(pcap_count "$OUT/cap-state.pcap" "RTPS")"
  echo "B-CR-01: lab/cmd 受信=$cmd_recv 平文=$cmd_hits RTPS=$cmd_rtps / lab/state 受信=$state_recv 平文=$state_hits RTPS=$state_rtps" >> "$OUT/result.txt"
  gt_zero "$cmd_recv" && gt_zero "$state_recv" && gt_zero "$cmd_rtps" && gt_zero "$state_rtps" \
    && [ "$cmd_hits" -gt 0 ] && [ "$state_hits" -eq 0 ]
}

# B-CR-02: RTPS 保護が NONE
probe_B_CR_02() { grep -q "<rtps_protection_kind>NONE<" <<<"$(b_governance)"; }

# B-AC-01: permissions にワイルドカード
probe_B_AC_01() { grep -Eq "<topic>[^<]*\*" <<<"$(b_permissions)"; }

# B-AC-02: permissions の default が ALLOW
probe_B_AC_02() { grep -q "<default>ALLOW</default>" <<<"$(b_permissions)"; }

# 注入した ID の一覧（gen-keystore が書く）と台帳の ID が一致するか
injected_matches_ledger() {
  local ledger_ids injected
  ledger_ids="$(bash scripts/sros2/check-ledger.sh --list | cut -f1 | sort | tr '\n' ' ')"
  injected="$(sort sros2/keystores/b/injected.txt 2>/dev/null | tr '\n' ' ')"
  echo "台帳: $ledger_ids / 注入: $injected" >> "$OUT/result.txt"
  [ -n "$ledger_ids" ] && [ "$ledger_ids" = "$injected" ]
}

verify_b() {
  require_running "$LAB_A" "$LAB_B" "$DIAG"
  check B1 "ros2lab-a/b の環境ラベルが b" eq "$(label_of "$LAB_A")$(label_of "$LAB_B")" "bb"
  check B2 "STRATEGY=Permissive" eq "$(env_of "$LAB_A" ROS_SECURITY_STRATEGY)" "Permissive"
  check B3 "注入した不備の ID が台帳と一致する（台帳にない不備・注入漏れがない）" injected_matches_ledger
  must_gen_rogue b all || return 1

  local id probe title count=0
  while IFS=$'\t' read -r id probe title; do
    count=$((count + 1))
    check "$id" "$title（不備が注入されている）" "$probe"
  done < <(bash scripts/sros2/check-ledger.sh --list)
  # 台帳が空・読めないときに、何も検査せず PASS にならないようにする
  check B-LEDGER "台帳から probe を 1 件以上読めた（${count} 件）" gt_zero "$count"
}

# 途中で中断した（不正証明書を作れないなど）ときは、実行できなかった検査があることを FAIL として残す
"verify_$ENV_NAME" || fail ABORT "検証を最後まで実行できなかった（途中で中断した。上の FAIL と $OUT を見ること）"

echo
echo "結果: PASS=$PASS_COUNT FAIL=$FAIL_COUNT（証拠: ${OUT#"$PWD"/}）"
[ "$FAIL_COUNT" -eq 0 ]
