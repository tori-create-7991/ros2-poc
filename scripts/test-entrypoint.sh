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

# 4b) 大文字小文字を区別しない（ENABLE=True でも鍵を検査する）。鍵が無ければ拒否、揃っていれば通す
run "$TMP/empty" ROS_SECURITY_ENABLE=True
[ "$RC" -eq 1 ] || fail "ENABLE=True + 空の src: exit 1 のはずが $RC"
run "$TMP/full" ROS_SECURITY_ENABLE=TRUE
[ "$RC" -eq 0 ] || fail "ENABLE=TRUE + 揃っている: exit 0 のはずが $RC: $ERR"

# 4c) true / false / 空以外の値は拒否する（ros2 が有効と解釈しうる値を見逃さない）
for v in 1 yes on; do
  run "$TMP/full" ROS_SECURITY_ENABLE=$v
  [ "$RC" -eq 1 ] || fail "ENABLE=$v: exit 1 のはずが $RC"
  grep -q "不正" <<<"$ERR" || fail "ENABLE=$v: 不正の説明が無い: $ERR"
done

# 4d) 空の鍵ファイルは拒否する
cp -r "$TMP/full" "$TMP/emptykey"
: > "$TMP/emptykey/key.pem"
run "$TMP/emptykey" ROS_SECURITY_ENABLE=true
[ "$RC" -eq 1 ] || fail "空の key.pem: exit 1 のはずが $RC"
grep -q "key.pem" <<<"$ERR" || fail "空の key.pem: stderr に key.pem が無い: $ERR"

# 4e) PLACE_AT が無い・不正（.. や空、相対パス）は拒否する
for bad in "" "../x" "relative/path" "/lab/a b" "/"; do
  rm -rf "$TMP/badplace"
  cp -r "$TMP/full" "$TMP/badplace"
  printf 'PLACE_AT=%s\n' "$bad" > "$TMP/badplace/enclave.env"
  run "$TMP/badplace" ROS_SECURITY_ENABLE=true
  [ "$RC" -eq 1 ] || fail "PLACE_AT='$bad': exit 1 のはずが $RC"
  grep -q "PLACE_AT" <<<"$ERR" || fail "PLACE_AT='$bad': stderr に PLACE_AT が無い（別の理由で失敗している）: $ERR"
done
: > "$TMP/badplace/enclave.env"
run "$TMP/badplace" ROS_SECURITY_ENABLE=true
[ "$RC" -eq 1 ] || fail "PLACE_AT が未設定: exit 1 のはずが $RC"

# 4f) SROS2_REQUIRE_CRL=true のときは crl.pem が必須（環境 c）。無ければ拒否、b（要求なし）では crl.pem なしで通る
cp -r "$TMP/full" "$TMP/nocrl"
rm "$TMP/nocrl/crl.pem"
run "$TMP/nocrl" ROS_SECURITY_ENABLE=true SROS2_REQUIRE_CRL=true
[ "$RC" -eq 1 ] || fail "crl.pem 欠け（REQUIRE_CRL）: exit 1 のはずが $RC"
grep -q "crl.pem" <<<"$ERR" || fail "crl.pem 欠け: stderr に crl.pem が無い: $ERR"
run "$TMP/nocrl" ROS_SECURITY_ENABLE=true
[ "$RC" -eq 0 ] || fail "crl.pem なし（要求なし）: exit 0 のはずが $RC: $ERR"
run "$TMP/nocrl" ROS_SECURITY_ENABLE=true SROS2_REQUIRE_CRL=false
[ "$RC" -eq 0 ] || fail "REQUIRE_CRL=false: exit 0 のはずが $RC: $ERR"
# REQUIRE_CRL の不正値（1 や typo）は「不要」扱いにせず拒否する
for v in 1 ture yes; do
  run "$TMP/nocrl" ROS_SECURITY_ENABLE=true SROS2_REQUIRE_CRL=$v
  [ "$RC" -eq 1 ] || fail "REQUIRE_CRL=$v: exit 1 のはずが $RC"
  grep -q "SROS2_REQUIRE_CRL" <<<"$ERR" || fail "REQUIRE_CRL=$v: 不正の説明が無い: $ERR"
done
# 4g) 途中のディレクトリも 0700（umask 077）
run "$TMP/full" ROS_SECURITY_ENABLE=true
[ "$(mode "$TMP/dst/enclaves/lab")" = "700" ] || fail "途中のディレクトリが 0700 でない: $(mode "$TMP/dst/enclaves/lab")"

# 4h) 次の entrypoint には元の umask で渡す（秘密を置く間だけ 077）
printf '#!/bin/sh\numask\n' > "$TMP/next.sh"
chmod +x "$TMP/next.sh"
rm -rf "$TMP/dst"
got="$(umask 022; env ROS_SECURITY_ENABLE=true SROS2_SRC="$TMP/full" SROS2_DST="$TMP/dst" SROS2_NEXT="$TMP/next.sh" sh "$ROOT/sros2/entrypoint.sh")"
[ "$got" = "0022" ] || fail "次の entrypoint の umask が元の値でない: $got"

# 5) SROS2 無効 → 鍵が無くても素通し
run "$TMP/none"
[ "$RC" -eq 0 ] || fail "SROS2 無効: exit 0 のはずが $RC: $ERR"
run "$TMP/none" ROS_SECURITY_ENABLE=false
[ "$RC" -eq 0 ] || fail "ENABLE=false: exit 0 のはずが $RC: $ERR"

echo "OK: entrypoint tests passed"
