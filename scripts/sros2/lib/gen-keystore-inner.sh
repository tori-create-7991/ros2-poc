#!/usr/bin/env bash
# SROS2 keystore の生成本体。ros2 CLI と openssl だけに依存する（Docker に依存しない）。
#   gen-keystore-inner.sh <b|c> <sros2 ディレクトリ>
# 環境 C: identity CA と permissions CA を分離、証明書 90 日、失効エントリ入りの CRL、RTPS ENCRYPT。
# 環境 B: （誤設定の注入は別 Task で追加）
set -euo pipefail

ENV_NAME="${1:?usage: gen-keystore-inner.sh <b|c> <sros2-dir>}"
BASE="${2:?usage: gen-keystore-inner.sh <b|c> <sros2-dir>}"

# shellcheck source=scripts/sros2/lib/common.sh
. "$(dirname "${BASH_SOURCE[0]}")/common.sh"
need_ros2

# governance に Domain ID が焼き込まれる。ros2-poc は Domain 42 固定なので 42 で生成する。
export ROS_DOMAIN_ID=42

CERT_DAYS=90      # 環境 C の証明書の有効期間（日）
CRL_DAYS=180      # CRL の nextUpdate。証明書より長くする（過ぎると検証側が全拒否になる）

POLICY="$BASE/policy/lab-$ENV_NAME.xml"
KS_OUT="$BASE/keystores/$ENV_NAME"
CA_OUT="$BASE/ca-private/$ENV_NAME"
ROGUE_OUT="$BASE/rogue/$ENV_NAME"
[ -f "$POLICY" ] || { echo "policy が無い: $POLICY" >&2; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cd "$WORK"

# コンテナ名の一覧。enclave 名はコンテナ名の - を _ にしたもの（sros2 の enclave 名に - は使えない）。
# policy の enclave path と一致させる。
CONTAINERS="ros2lab-a ros2lab-b"
enclave_of() { echo "/lab/${1//-/_}"; }

ros2 security create_keystore ks >/dev/null

sign_governance() {
  openssl smime -sign -text -in ks/enclaves/governance.xml -out ks/enclaves/governance.p7s \
    -signer ks/public/permissions_ca.cert.pem -inkey ks/private/permissions_ca.key.pem
}

# --- 環境 C: 適正設定 ---
setup_c() {
  # identity CA を permissions CA から分離する（sros2 既定は同一の CA）
  rm ks/public/identity_ca.cert.pem ks/private/identity_ca.key.pem
  new_key ks/private/identity_ca.key.pem
  openssl req -x509 -new -key ks/private/identity_ca.key.pem -subj "/CN=ros2-poc-identity-ca" \
    -days 365 -out ks/public/identity_ca.cert.pem

  # RTPS を暗号化まで引き上げる（sros2 既定は SIGN）
  sed -i 's#<rtps_protection_kind>SIGN</rtps_protection_kind>#<rtps_protection_kind>ENCRYPT</rtps_protection_kind>#' \
    ks/enclaves/governance.xml
  grep -q '<rtps_protection_kind>ENCRYPT<' ks/enclaves/governance.xml
  sign_governance

  for c in $CONTAINERS; do ros2 security create_enclave ks "$(enclave_of "$c")" >/dev/null; done

  # 証明書を CERT_DAYS 日で再発行する（sros2 は 3650 日固定）
  init_ca_db ./ks/private/identity_ca.key.pem ./ks/public/identity_ca.cert.pem "$CRL_DAYS"
  for c in $CONTAINERS; do
    e="$(enclave_of "$c")"
    issue_cert "ks/enclaves$e/key.pem" "$e" "ks/enclaves$e/cert.pem" -days "$CERT_DAYS"
    chmod 0600 "ks/enclaves$e/key.pem"
  done
  chmod 0600 ks/private/*.pem

  # permissions を作り直す（証明書の有効期間を継承する）
  for c in $CONTAINERS; do ros2 security create_permission ks "$(enclave_of "$c")" "$POLICY" >/dev/null; done
}

case "$ENV_NAME" in
  c) setup_c ;;
  b) echo "環境 b の生成は未実装" >&2; exit 1 ;;
esac

# --- 失効済み証明書（不正証明書の一種）: 正規 CA で発行してから失効させる ---
# 雛形はコンテナ ros2lab-a の enclave。subject と permissions を合わせる。
FIRST="$(echo "$CONTAINERS" | cut -d' ' -f1)"
FIRST_E="$(enclave_of "$FIRST")"
new_key revoked.key
issue_cert revoked.key "$FIRST_E" revoked.pem -days "$CERT_DAYS"
openssl ca -config openssl.cnf -revoke revoked.pem >/dev/null 2>&1
make_rogue_dir "$WORK/rogue-revoked" "$FIRST_E" "ks/enclaves$FIRST_E" revoked.pem revoked.key ks/public/identity_ca.cert.pem

# --- CRL ---
# 環境 C: 失効エントリ入りの CRL を全 enclave に crl.pem として置く（rmw_fastrtps は enclave の crl.pem を読む）。
openssl ca -config openssl.cnf -gencrl -out crl.pem >/dev/null 2>&1
if [ "$ENV_NAME" = "c" ]; then
  for c in $CONTAINERS; do cp crl.pem "ks/enclaves$(enclave_of "$c")/crl.pem"; done
fi

# --- 出力 ---
rm -rf "$KS_OUT" "$CA_OUT" "$ROGUE_OUT"
mkdir -p "$KS_OUT/containers" "$CA_OUT" "$ROGUE_OUT"
chmod 0700 "$CA_OUT"

# コンテナごとに、自分の enclave だけをシンボリックリンクを実体にして置く（-L）
: > "$KS_OUT/env.sh"
for c in $CONTAINERS; do
  dst="$KS_OUT/containers/$c"
  src="ks/enclaves$(enclave_of "$c")"
  mkdir -p "$dst"
  for f in cert.pem key.pem identity_ca.cert.pem permissions_ca.cert.pem governance.p7s permissions.p7s crl.pem; do
    [ -f "$src/$f" ] && cp -L "$src/$f" "$dst/$f"
  done
  chmod 0600 "$dst/key.pem"
  echo "PLACE_AT=$(enclave_of "$c")" > "$dst/enclave.env"
  var="SROS2_ENCLAVE_$(echo "$c" | tr 'a-z-' 'A-Z_')"
  echo "export $var=$(enclave_of "$c")" >> "$KS_OUT/env.sh"
done

# CA の秘密鍵と発行台帳は、コンテナに渡さない場所へ（後から不正証明書を作るため相対パスのまま保存する）
mkdir -p "$CA_OUT/ks"
cp -r ks/private ks/public "$CA_OUT/ks/"
cp -r ca openssl.cnf crl.pem "$CA_OUT/"
chmod -R go-rwx "$CA_OUT"

# 失効済みの不正証明書（診断コンテナだけに渡す）
cp -r "$WORK/rogue-revoked" "$ROGUE_OUT/revoked"

echo "生成した: $KS_OUT（コンテナ用）、$CA_OUT（CA の秘密鍵。コンテナには渡さない）、$ROGUE_OUT/revoked（失効済みの不正証明書）"
