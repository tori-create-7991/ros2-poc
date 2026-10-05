#!/usr/bin/env bash
# 診断用の不正証明書を生成する本体。ros2 CLI は使わず openssl と python3 だけに依存する。
#   gen-rogue-inner.sh <b|c> <valid|wrongca|selfsigned|expired|all> <sros2 ディレクトリ> [期限までの秒数]
# 生成先: <sros2>/rogue/<env>/<種別>/（keystore と同じ構造。診断コンテナだけに渡す。コミットしない）
#   valid      対照用: 正規 CA が発行した有効な証明書（参加できて当たり前。試験が成立している確認に使う）
#   wrongca    想定外の CA が署名した証明書
#   selfsigned 自己署名の証明書
#   expired    期限切れの証明書。起動時に自分で検証に失敗しないよう、期限の N 秒前に起動して、
#              期限後に検証側を起動する使い方をする（既定 45 秒。生成してすぐ使うこと）
#   all        valid / wrongca / selfsigned（expired は時間に依存するので含めない）
# 失効済み（revoked）は CRL に載せる必要があるため、gen-keystore.sh が keystore と一緒に作る。
set -euo pipefail

ENV_NAME="${1:?usage: gen-rogue-inner.sh <b|c> <case> <sros2-dir> [seconds]}"
CASE="${2:?usage: gen-rogue-inner.sh <b|c> <case> <sros2-dir> [seconds]}"
BASE="${3:?usage: gen-rogue-inner.sh <b|c> <case> <sros2-dir> [seconds]}"
VALID_SECONDS="${4:-45}"
case "$VALID_SECONDS" in *[!0-9]* | "") echo "秒数は数字だけ: $VALID_SECONDS" >&2; exit 2 ;; esac
case "$ENV_NAME" in b | c) ;; *) echo "環境は b か c: $ENV_NAME" >&2; exit 2 ;; esac

# shellcheck source=scripts/sros2/lib/common.sh
. "$(dirname "${BASH_SOURCE[0]}")/common.sh"

CA_DIR="$BASE/ca-private/$ENV_NAME"
KS_DIR="$BASE/keystores/$ENV_NAME"
ROGUE_DIR="$BASE/rogue/$ENV_NAME"
[ -d "$CA_DIR" ] && [ -d "$KS_DIR" ] || { echo "環境 $ENV_NAME の keystore が無い。先に gen-keystore.sh $ENV_NAME を実行すること。" >&2; exit 1; }

# 雛形: コンテナ ros2lab-a の enclave（subject と permissions を合わせる）
TMPL="$KS_DIR/containers/ros2lab-a"
# shellcheck disable=SC1091
. "$TMPL/enclave.env"
ENCLAVE="$PLACE_AT"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$ROGUE_DIR"

REAL_CA="$CA_DIR/ks/public/identity_ca.cert.pem"

gen_valid() {
  new_key "$WORK/valid.key"
  ( cd "$CA_DIR" && issue_cert "$WORK/valid.key" "$ENCLAVE" "$WORK/valid.pem" -days 2 )
  make_rogue_dir "$ROGUE_DIR/valid" "$ENCLAVE" "$TMPL" "$WORK/valid.pem" "$WORK/valid.key" "$REAL_CA"
}

gen_wrongca() {
  new_key "$WORK/other_ca.key"
  openssl req -x509 -new -key "$WORK/other_ca.key" -subj "/CN=other-ca" -days 30 -out "$WORK/other_ca.pem"
  new_key "$WORK/wrongca.key"
  openssl req -new -key "$WORK/wrongca.key" -subj "$(subj_for "$ENCLAVE")" -out "$WORK/wrongca.csr"
  openssl x509 -req -in "$WORK/wrongca.csr" -CA "$WORK/other_ca.pem" -CAkey "$WORK/other_ca.key" \
    -CAcreateserial -days 30 -out "$WORK/wrongca.pem" >/dev/null 2>&1
  make_rogue_dir "$ROGUE_DIR/wrongca" "$ENCLAVE" "$TMPL" "$WORK/wrongca.pem" "$WORK/wrongca.key" "$WORK/other_ca.pem"
}

gen_selfsigned() {
  new_key "$WORK/self.key"
  openssl req -x509 -new -key "$WORK/self.key" -subj "$(subj_for "$ENCLAVE")" -days 30 -out "$WORK/self.pem"
  make_rogue_dir "$ROGUE_DIR/selfsigned" "$ENCLAVE" "$TMPL" "$WORK/self.pem" "$WORK/self.key" "$WORK/self.pem"
}

gen_expired() {
  new_key "$WORK/expired.key"
  local start end
  start="$(python3 -c 'import datetime as d; print((d.datetime.now(d.timezone.utc)-d.timedelta(hours=1)).strftime("%Y%m%d%H%M%SZ"))')"
  end="$(python3 -c "import datetime as d; print((d.datetime.now(d.timezone.utc)+d.timedelta(seconds=$VALID_SECONDS)).strftime('%Y%m%d%H%M%SZ'))")"
  ( cd "$CA_DIR" && issue_cert "$WORK/expired.key" "$ENCLAVE" "$WORK/expired.pem" -startdate "$start" -enddate "$end" )
  make_rogue_dir "$ROGUE_DIR/expired" "$ENCLAVE" "$TMPL" "$WORK/expired.pem" "$WORK/expired.key" "$REAL_CA"
  # 期限の時刻（UNIX 秒）。試験側はこの時刻を過ぎるまで待つ（固定の sleep に頼らない）
  echo "EXPIRES_EPOCH=$(python3 -c "import time; print(int(time.time()) + $VALID_SECONDS)")" >> "$ROGUE_DIR/expired/meta.env"
  echo "expired: ${VALID_SECONDS} 秒後（$end）に期限が切れる。それまでに提示側を起動し、期限後に検証側を起動すること。"
}

case "$CASE" in
  valid) gen_valid ;;
  wrongca) gen_wrongca ;;
  selfsigned) gen_selfsigned ;;
  expired) gen_expired ;;
  all) gen_valid; gen_wrongca; gen_selfsigned ;;
  *) echo "不明な種別: $CASE" >&2; exit 2 ;;
esac
echo "生成した: $ROGUE_DIR（$CASE）"
