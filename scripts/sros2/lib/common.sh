#!/usr/bin/env bash
# gen-keystore-inner.sh / gen-rogue-inner.sh の共通処理（source して使う）。
# ros2 CLI と openssl だけに依存する（Docker に依存しない）。

# ros2 CLI が無ければ ROS の setup.bash を読む
need_ros2() {
  if ! command -v ros2 >/dev/null 2>&1 && [ -f /opt/ros/jazzy/setup.bash ]; then
    set +u
    # shellcheck disable=SC1091
    . /opt/ros/jazzy/setup.bash
    set -u
  fi
  command -v ros2 >/dev/null 2>&1 || { echo "ros2 CLI が見つからない" >&2; exit 1; }
}

# 発行台帳つきの CA 設定（カレントディレクトリ相対）を作る。
#   $1: identity CA の秘密鍵  $2: identity CA の証明書  $3: CRL の有効日数
# カレントに ca/ と openssl.cnf を作る。パスを相対にするのは、台帳ごと別の場所へ移しても使えるようにするため。
init_ca_db() {
  mkdir -p ca
  : > ca/index.txt
  echo 1000 > ca/serial
  echo 01 > ca/crlnumber
  cat > openssl.cnf <<CNF
[ca]
default_ca=CA_default
[CA_default]
database=./ca/index.txt
new_certs_dir=./ca
serial=./ca/serial
crlnumber=./ca/crlnumber
default_md=sha256
policy=pol
unique_subject=no
default_crl_days=$3
private_key=$1
certificate=$2
[pol]
commonName=supplied
[v3]
basicConstraints=CA:FALSE
keyUsage=digitalSignature
CNF
}

# enclave 名（例: /lab/ros2lab_a）を openssl の -subj 用にエスケープした CN にする
subj_for() {
  echo "/CN=${1//\//\\/}"
}

# ECDSA（prime256v1）の鍵を作る
new_key() {
  openssl ecparam -name prime256v1 -genkey -noout -out "$1"
}

# CA の台帳に従って発行する。  $1: 鍵  $2: enclave 名  $3: 出力  以降: openssl ca の追加引数（-days N など）
issue_cert() {
  local key="$1" enclave="$2" out="$3"
  shift 3
  local csr
  csr="$(mktemp)"
  openssl req -new -key "$key" -subj "$(subj_for "$enclave")" -out "$csr"
  openssl ca -config openssl.cnf -extensions v3 -batch -in "$csr" -out "$out" "$@" >/dev/null 2>&1
  rm -f "$csr"
}

# 不正証明書用のディレクトリを作る（keystore と同じ構造）。
#   $1: 出力ルート  $2: enclave 名  $3: enclave の雛形（governance / permissions を持つ）
#   $4: 提示する証明書  $5: その秘密鍵  $6: 提示側が信頼する identity CA の証明書
# governance / permissions / permissions CA は正規のものを使う（差し替えるのは identity だけ）。
make_rogue_dir() {
  local root="$1" enclave="$2" tmpl="$3" cert="$4" key="$5" idca="$6"
  local d="$root/enclaves$enclave"
  rm -rf "$root"
  mkdir -p "$d"
  cp "$tmpl/governance.p7s" "$tmpl/permissions.p7s" "$tmpl/permissions_ca.cert.pem" "$d/"
  cp "$cert" "$d/cert.pem"
  cp "$key" "$d/key.pem"
  cp "$idca" "$d/identity_ca.cert.pem"
  echo "ENCLAVE=$enclave" > "$root/meta.env"
}
