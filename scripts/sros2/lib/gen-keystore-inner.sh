#!/usr/bin/env bash
# SROS2 keystore の生成本体。ros2 CLI と openssl だけに依存する（Docker に依存しない）。
#   gen-keystore-inner.sh <b|c> <sros2 ディレクトリ>
# 環境 C: identity CA と permissions CA を分離、証明書 90 日、CRL あり、RTPS ENCRYPT。
# 環境 B: （誤設定の注入は Task 10 で追加）
set -euo pipefail

ENV_NAME="${1:?usage: gen-keystore-inner.sh <b|c> <sros2-dir>}"
BASE="${2:?usage: gen-keystore-inner.sh <b|c> <sros2-dir>}"

if ! command -v ros2 >/dev/null 2>&1 && [ -f /opt/ros/jazzy/setup.bash ]; then
  set +u
  # shellcheck disable=SC1091
  . /opt/ros/jazzy/setup.bash
  set -u
fi
command -v ros2 >/dev/null 2>&1 || { echo "ros2 CLI が見つからない" >&2; exit 1; }

# governance に Domain ID が焼き込まれる。ros2-poc は Domain 42 固定なので 42 で生成する。
export ROS_DOMAIN_ID=42

CERT_DAYS=90      # 環境 C の証明書の有効期間（日）
CRL_DAYS=180      # CRL の nextUpdate。証明書より長くする（過ぎると検証側が全拒否になる）

POLICY="$BASE/policy/lab-$ENV_NAME.xml"
KS_OUT="$BASE/keystores/$ENV_NAME"
CA_OUT="$BASE/ca-private/$ENV_NAME"
[ -f "$POLICY" ] || { echo "policy が無い: $POLICY" >&2; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cd "$WORK"

# コンテナ名の一覧。enclave 名はコンテナ名の - を _ にしたもの（sros2 の enclave 名に - は使えない）。
# policy の enclave path と一致させる
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
  openssl ecparam -name prime256v1 -genkey -noout -out ks/private/identity_ca.key.pem
  openssl req -x509 -new -key ks/private/identity_ca.key.pem -subj "/CN=ros2-poc-identity-ca" \
    -days 365 -out ks/public/identity_ca.cert.pem

  # RTPS を暗号化まで引き上げる（sros2 既定は SIGN）
  sed -i 's#<rtps_protection_kind>SIGN</rtps_protection_kind>#<rtps_protection_kind>ENCRYPT</rtps_protection_kind>#' \
    ks/enclaves/governance.xml
  grep -q '<rtps_protection_kind>ENCRYPT<' ks/enclaves/governance.xml
  sign_governance

  for c in $CONTAINERS; do ros2 security create_enclave ks "$(enclave_of "$c")" >/dev/null; done

  # 証明書を CERT_DAYS 日で再発行する（sros2 は 3650 日固定）。openssl ca で台帳を持たせ、CRL を出せるようにする
  mkdir ca
  : > ca/index.txt
  echo 1000 > ca/serial
  echo 01 > ca/crlnumber
  cat > ca/openssl.cnf <<CNF
[ca]
default_ca=CA_default
[CA_default]
database=$WORK/ca/index.txt
new_certs_dir=$WORK/ca
serial=$WORK/ca/serial
crlnumber=$WORK/ca/crlnumber
default_md=sha256
policy=pol
unique_subject=no
default_crl_days=$CRL_DAYS
private_key=$WORK/ks/private/identity_ca.key.pem
certificate=$WORK/ks/public/identity_ca.cert.pem
[pol]
commonName=supplied
[v3]
basicConstraints=CA:FALSE
keyUsage=digitalSignature
CNF
  for c in $CONTAINERS; do
    e="$(enclave_of "$c")"
    d="ks/enclaves$e"
    cn="${e//\//\\/}"
    openssl req -new -key "$d/key.pem" -subj "/CN=$cn" -out "$c.csr"
    openssl ca -config ca/openssl.cnf -extensions v3 -batch -in "$c.csr" -out "$d/cert.pem" \
      -days "$CERT_DAYS" >/dev/null 2>&1
    chmod 0600 "$d/key.pem"
  done
  chmod 0600 ks/private/*.pem

  # permissions を作り直す（証明書の有効期間を継承する）
  for c in $CONTAINERS; do ros2 security create_permission ks "$(enclave_of "$c")" "$POLICY" >/dev/null; done

  # 空の CRL を発行し、全 enclave に crl.pem として置く（rmw_fastrtps は enclave の crl.pem を読む）
  openssl ca -config ca/openssl.cnf -gencrl -out crl.pem >/dev/null 2>&1
  for c in $CONTAINERS; do cp crl.pem "ks/enclaves$(enclave_of "$c")/crl.pem"; done
}

case "$ENV_NAME" in
  c) setup_c ;;
  b) echo "環境 b の生成は未実装" >&2; exit 1 ;;
esac

# --- 出力 ---
rm -rf "$KS_OUT" "$CA_OUT"
mkdir -p "$KS_OUT/containers" "$CA_OUT"
chmod 0700 "$CA_OUT"

# コンテナごとに、自分の enclave だけをシンボリックリンクを実体にして置く（-L）
: > "$KS_OUT/env.sh"
for c in $CONTAINERS; do
  dst="$KS_OUT/containers/$c"
  mkdir -p "$dst"
  for f in cert.pem key.pem identity_ca.cert.pem permissions_ca.cert.pem governance.p7s permissions.p7s crl.pem; do
    [ -f "ks/enclaves$(enclave_of "$c")/$f" ] && cp -L "ks/enclaves$(enclave_of "$c")/$f" "$dst/$f"
  done
  chmod 0600 "$dst/key.pem"
  echo "PLACE_AT=$(enclave_of "$c")" > "$dst/enclave.env"
  var="SROS2_ENCLAVE_$(echo "$c" | tr 'a-z-' 'A-Z_')"
  echo "export $var=$(enclave_of "$c")" >> "$KS_OUT/env.sh"
done

# CA の秘密鍵と台帳は、コンテナに渡さない場所へ
cp -r ks/private "$CA_OUT/private"
[ -d ca ] && cp -r ca "$CA_OUT/ca"
[ -f crl.pem ] && cp crl.pem "$CA_OUT/crl.pem"
chmod -R go-rwx "$CA_OUT"

echo "生成した: $KS_OUT（コンテナ用）と $CA_OUT（CA の秘密鍵。コンテナには渡さない）"
