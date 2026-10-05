#!/usr/bin/env bash
# sros2/entrypoint.sh の分岐を検証する（docker は使わない）。
# SROS2 が有効なのに鍵が無い・欠けているときは起動しない（fail closed）こと、
# 揃っているときは 0700/0600 で置いて次の entrypoint に渡すこと、SROS2 が無効なら素通しすることを確認する。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }
mode() { stat -c %a "$1" 2>/dev/null || stat -f %Lp "$1"; }

run() { # run <SRC> [環境変数 KEY=VAL...]  → RC / ERR
  local src="$1"
  shift
  rm -rf "$TMP/dst"
  set +e
  ERR="$(env "$@" SROS2_SRC="$src" SROS2_DST="$TMP/dst" SROS2_NEXT=true sh "$ROOT/sros2/entrypoint.sh" 2>&1 >/dev/null)"
  RC=$?
  set -e
}

# 鍵が揃った src
mkdir -p "$TMP/full"
for f in cert.pem key.pem identity_ca.cert.pem permissions_ca.cert.pem governance.p7s permissions.p7s crl.pem; do
  echo x > "$TMP/full/$f"
done
echo "PLACE_AT=/lab/test_enclave" > "$TMP/full/enclave.env"

# 1) SROS2 有効 + src が空（enclave.env も無い）→ 起動しない
mkdir -p "$TMP/empty"
run "$TMP/empty" ROS_SECURITY_ENABLE=true
[ "$RC" -eq 1 ] || fail "空の src: exit 1 のはずが $RC"
grep -q "enclave.env" <<<"$ERR" || fail "空の src: stderr に enclave.env が無い: $ERR"

# 2) SROS2 有効 + src が存在しない → 起動しない
run "$TMP/none" ROS_SECURITY_ENABLE=true
[ "$RC" -eq 1 ] || fail "src なし: exit 1 のはずが $RC"

# 3) SROS2 有効 + 秘密鍵が欠けている → 起動しない
cp -r "$TMP/full" "$TMP/nokey"
rm "$TMP/nokey/key.pem"
run "$TMP/nokey" ROS_SECURITY_ENABLE=true
[ "$RC" -eq 1 ] || fail "key.pem 欠け: exit 1 のはずが $RC"
grep -q "key.pem" <<<"$ERR" || fail "key.pem 欠け: stderr に key.pem が無い: $ERR"

# 4) SROS2 有効 + 揃っている → 0700/0600 で置いて次の entrypoint へ
run "$TMP/full" ROS_SECURITY_ENABLE=true
[ "$RC" -eq 0 ] || fail "揃っている: exit 0 のはずが $RC: $ERR"
d="$TMP/dst/enclaves/lab/test_enclave"
[ "$(mode "$d/key.pem")" = "600" ] || fail "key.pem が 0600 でない: $(mode "$d/key.pem")"
[ "$(mode "$d")" = "700" ] || fail "enclave ディレクトリが 0700 でない: $(mode "$d")"
[ -f "$d/crl.pem" ] || fail "crl.pem がコピーされていない"
[ ! -e "$d/enclave.env" ] || fail "enclave.env が keystore にコピーされている"

# 5) SROS2 無効 → 鍵が無くても素通し
run "$TMP/none"
[ "$RC" -eq 0 ] || fail "SROS2 無効: exit 0 のはずが $RC: $ERR"
run "$TMP/none" ROS_SECURITY_ENABLE=false
[ "$RC" -eq 0 ] || fail "ENABLE=false: exit 0 のはずが $RC: $ERR"

echo "OK: entrypoint tests passed"
