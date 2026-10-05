#!/usr/bin/env bash
# SROS2 keystore の生成本体。ros2 CLI と openssl だけに依存する（Docker に依存しない）。
#   gen-keystore-inner.sh <b|c> <sros2 ディレクトリ> [<workspace ディレクトリ>]
# 環境 C: identity CA と permissions CA を分離、証明書 90 日、失効エントリ入りの CRL、RTPS ENCRYPT。
# 環境 B: 台帳（sros2/ledger/ledger.yaml）の不備を意図的に注入する。注入した ID を injected.txt に書く。
set -euo pipefail

ENV_NAME="${1:?usage: gen-keystore-inner.sh <b|c> <sros2-dir> [<workspace-dir>]}"
BASE="${2:?usage: gen-keystore-inner.sh <b|c> <sros2-dir> [<workspace-dir>]}"
WORKSPACE_DIR="${3:-}"   # 環境 B の B-AU-07（共有領域に鍵を置く）で使う

case "$ENV_NAME" in b | c) ;; *) echo "環境は b か c: $ENV_NAME" >&2; exit 2 ;; esac

# shellcheck source=scripts/sros2/lib/common.sh
. "$(dirname "${BASH_SOURCE[0]}")/common.sh"
# shellcheck source=scripts/sros2/lib/swap.sh
. "$(dirname "${BASH_SOURCE[0]}")/swap.sh"
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

# 相対パスだと、このあと cd したときに STAGE などの指す先が変わる。絶対パスだけを受け付ける
case "$BASE" in /*) ;; *) echo "sros2 ディレクトリは絶対パスで渡すこと: $BASE" >&2; exit 2 ;; esac
case "$WORKSPACE_DIR" in "" | /*) ;; *) echo "workspace ディレクトリは絶対パスで渡すこと: $WORKSPACE_DIR" >&2; exit 2 ;; esac

# 出力は同じファイルシステム上の一時ディレクトリ（STAGE）に作ってから入れ替える。中断しても残さない
STAGE="$BASE/.stage-$ENV_NAME"
OLD="$BASE/.old-$ENV_NAME"
LOCK="$BASE/.lock-$ENV_NAME"

# 事前検査（ロックを取る前に、何も変えずに中止できるものを先に済ませる）
# 環境 b は ./workspace に鍵のコピーを置く（B-AU-07）。共有領域（全コンテナから書き込める）なので、
#   - シンボリックリンク（切れたリンクを含む）が置かれていたら、辿らずに中止する
#   - 同名の別のディレクトリがあるなら、何もせず中止する
if [ -n "$WORKSPACE_DIR" ]; then
  if [ -L "$WORKSPACE_DIR/sros2-keystore" ]; then
    echo "$WORKSPACE_DIR/sros2-keystore がシンボリックリンク。辿らずに中止する。手で確認して削除すること。" >&2
    exit 1
  fi
  if [ "$ENV_NAME" = "b" ] && [ -e "$WORKSPACE_DIR/sros2-keystore" ] && [ ! -f "$WORKSPACE_DIR/sros2-keystore/.sros2-generated" ]; then
    echo "$WORKSPACE_DIR/sros2-keystore は既にあり、このスクリプトが作ったものではない。B-AU-07 の注入を中止する。" >&2
    exit 1
  fi
fi

if ! acquire_lock "$LOCK"; then
  echo "別の生成が実行中（$(shown "$LOCK") がある）。別の生成が動いていないことを確認して、$(shown "$LOCK") を削除してから再実行すること。" >&2
  exit 1
fi
# ロックを取ったあとの終了では、必ず後始末する（入れ替えの最中の中断は元に戻す）。INT / TERM / HUP も EXIT を通す
cleanup() {
  cleanup_swap
  rm -rf "${WORK:-}" "$STAGE" "$LOCK"
}
trap cleanup EXIT
trap 'exit 130' INT TERM HUP
WORK="$(mktemp -d)"

# 前回の入れ替えが途中で失敗していると（SIGKILL など）、旧データが OLD に残っている。黙って消さずに中止する。
# keystore は再生成できるので、旧い鍵を保つ必要がなければ OLD を削除して再実行すればよい
if ! old_is_clear "$OLD"; then
  echo "$(shown "$OLD") がある。前回の入れ替えが途中で終わっている。keystore は再生成できるので、$(shown "$OLD") を削除して再実行すること（旧い鍵を保ちたいときは、中身を手で確認する）。" >&2
  exit 1
fi
cd "$WORK"

# コンテナ名の一覧。
# 環境 C の enclave 名はコンテナ名の - を _ にしたもの（sros2 の enclave 名に - は使えない）。
# 環境 B は全コンテナで同じ enclave（B-AU-03）。policy の enclave path と一致させる。
CONTAINERS="ros2lab-a ros2lab-b ros2arm ros2server"
enclave_of() {
  if [ "$ENV_NAME" = "b" ]; then echo "/lab/shared"; else echo "/lab/${1//-/_}"; fi
}
# 重複を除いた enclave の一覧
ENCLAVES="$(for c in $CONTAINERS; do enclave_of "$c"; done | sort -u)"
INJECTED="$WORK/injected.txt"
: > "$INJECTED"
inject() { echo "$1" >> "$INJECTED"; }

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
  sed_inplace 's#<rtps_protection_kind>SIGN</rtps_protection_kind>#<rtps_protection_kind>ENCRYPT</rtps_protection_kind>#' \
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

# --- 環境 B: 誤設定（台帳の不備を注入する）---
setup_b() {
  # B-AU-06: identity CA と permissions CA が同一（sros2 の既定のまま）
  inject B-AU-06
  # B-AU-04: 証明書の有効期間が 3650 日（sros2 の既定のまま）
  inject B-AU-04
  # B-AU-05: CRL を置かない（失効しても受理される）。crl.pem を配らないことで注入する
  inject B-AU-05

  # governance を作り直す。
  #   B-AU-01: 未認証参加者の許可（Fast DDS は RTPS 保護が NONE でないと起動しない）
  #   B-CR-02: RTPS 保護が NONE（B-AU-01 と連動）
  #   B-CR-01: 制御指令トピック lab/cmd だけ保護対象外（先に書いたルールが優先される）
  cat > ks/enclaves/governance.xml <<GOV
<?xml version="1.0"?>
<dds xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:noNamespaceSchemaLocation="http://www.omg.org/spec/DDS-SECURITY/20170901/omg_shared_ca_governance.xsd">
  <domain_access_rules>
    <domain_rule>
      <domains><id>42</id></domains>
      <allow_unauthenticated_participants>true</allow_unauthenticated_participants>
      <enable_join_access_control>true</enable_join_access_control>
      <discovery_protection_kind>ENCRYPT</discovery_protection_kind>
      <liveliness_protection_kind>ENCRYPT</liveliness_protection_kind>
      <rtps_protection_kind>NONE</rtps_protection_kind>
      <topic_access_rules>
        <topic_rule>
          <topic_expression>rt/lab/cmd</topic_expression>
          <enable_discovery_protection>false</enable_discovery_protection>
          <enable_liveliness_protection>false</enable_liveliness_protection>
          <enable_read_access_control>false</enable_read_access_control>
          <enable_write_access_control>false</enable_write_access_control>
          <metadata_protection_kind>NONE</metadata_protection_kind>
          <data_protection_kind>NONE</data_protection_kind>
        </topic_rule>
        <topic_rule>
          <topic_expression>*</topic_expression>
          <enable_discovery_protection>true</enable_discovery_protection>
          <enable_liveliness_protection>true</enable_liveliness_protection>
          <enable_read_access_control>true</enable_read_access_control>
          <enable_write_access_control>true</enable_write_access_control>
          <metadata_protection_kind>ENCRYPT</metadata_protection_kind>
          <data_protection_kind>ENCRYPT</data_protection_kind>
        </topic_rule>
      </topic_access_rules>
    </domain_rule>
  </domain_access_rules>
</dds>
GOV
  sign_governance
  inject B-AU-01
  inject B-CR-02
  inject B-CR-01

  # B-AU-03: 全コンテナで同じ enclave（同じ証明書と鍵）を使い回す
  for e in $ENCLAVES; do ros2 security create_enclave ks "$e" >/dev/null; done
  inject B-AU-03

  # B-AC-01: permissions にワイルドカード（policy lab-b.xml の topic *）
  for e in $ENCLAVES; do ros2 security create_permission ks "$e" "$POLICY" >/dev/null; done
  inject B-AC-01

  # B-AC-02: permissions の default を ALLOW に書き換えて署名し直す
  for e in $ENCLAVES; do
    sed_inplace 's#<default>DENY</default>#<default>ALLOW</default>#' "ks/enclaves$e/permissions.xml"
    openssl smime -sign -text -in "ks/enclaves$e/permissions.xml" -out "ks/enclaves$e/permissions.p7s" \
      -signer ks/public/permissions_ca.cert.pem -inkey ks/private/permissions_ca.key.pem
  done
  inject B-AC-02

  # B-AU-07 は出力のとき（共有領域 ./workspace に鍵のコピーを置く）に注入する

  # sros2 既定の鍵ファイルは 0644 のまま（権限を絞らない）
  init_ca_db ./ks/private/identity_ca.key.pem ./ks/public/identity_ca.cert.pem "$CRL_DAYS"
}

case "$ENV_NAME" in
  c) setup_c ;;
  b) setup_b ;;
esac

# --- 失効済み証明書（不正証明書の一種）: 正規 CA で発行してから失効させる ---
# 雛形はコンテナ ros2lab-a の enclave。subject と permissions を合わせる。
FIRST="$(echo "$CONTAINERS" | cut -d' ' -f1)"
FIRST_E="$(enclave_of "$FIRST")"
new_key revoked.key
if [ "$ENV_NAME" = "c" ]; then revoked_days="$CERT_DAYS"; else revoked_days=3650; fi
issue_cert revoked.key "$FIRST_E" revoked.pem -days "$revoked_days"
openssl ca -config openssl.cnf -revoke revoked.pem >/dev/null 2>&1
make_rogue_dir "$WORK/rogue-revoked" "$FIRST_E" "ks/enclaves$FIRST_E" revoked.pem revoked.key ks/public/identity_ca.cert.pem

# --- CRL ---
# 環境 C: 失効エントリ入りの CRL を全 enclave に crl.pem として置く（rmw_fastrtps は enclave の crl.pem を読む）。
openssl ca -config openssl.cnf -gencrl -out crl.pem >/dev/null 2>&1
if [ "$ENV_NAME" = "c" ]; then
  for c in $CONTAINERS; do cp crl.pem "ks/enclaves$(enclave_of "$c")/crl.pem"; done
fi

# --- 出力 ---
# 途中で失敗しても、既存の keystore を壊さないように、同じファイルシステム上の一時ディレクトリへ作ってから入れ替える。
FINAL_KS="$KS_OUT"
FINAL_CA="$CA_OUT"
FINAL_ROGUE="$ROGUE_OUT"
rm -rf "$STAGE"
KS_OUT="$STAGE/keystores"
CA_OUT="$STAGE/ca-private"
ROGUE_OUT="$STAGE/rogue"
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

# 環境 B の残りの不備
if [ "$ENV_NAME" = "b" ]; then
  # B-AU-02: ros2lab-b の enclave 名を 1 文字間違え（shared → shred）、Permissive のままセキュリティなしに
  # フォールバックさせる（Enforce なら起動に失敗する）。ファイルは正しい場所（/lab/shared）に置かれる。
  sed_inplace 's#^export SROS2_ENCLAVE_ROS2LAB_B=.*#export SROS2_ENCLAVE_ROS2LAB_B=/lab/shred#' "$KS_OUT/env.sh"
  inject B-AU-02
  # B-AU-07: 共有領域（./workspace。全コンテナから書き込み可）に、秘密鍵ごと keystore のコピーを置く
  # （実際の配置は入れ替えのあとに行う）。鍵ファイルは誰でも読める 0644（sros2 の既定の権限のまま）。
  [ -z "$WORKSPACE_DIR" ] || inject B-AU-07
fi
sort -u "$INJECTED" > "$KS_OUT/injected.txt"

# CA の秘密鍵と発行台帳は、コンテナに渡さない場所へ（後から不正証明書を作るため相対パスのまま保存する）
mkdir -p "$CA_OUT/ks"
cp -r ks/private ks/public "$CA_OUT/ks/"
cp -r ca openssl.cnf crl.pem "$CA_OUT/"
chmod -R go-rwx "$CA_OUT"

# 失効済みの不正証明書（診断コンテナだけに渡す）
cp -r "$WORK/rogue-revoked" "$ROGUE_OUT/revoked"

# 入れ替え（ここまで来たら全部できている）。失敗・中断したら元に戻す（swap.sh）
swap_all || exit 1

# ./workspace の鍵のコピーは、入れ替えのあとに触る（途中で失敗しても keystore と食い違わないように）。
# 目印ファイルがあるもの（このスクリプトが作ったもの）だけを消す・作り直す
if [ -n "$WORKSPACE_DIR" ]; then
  ws="$WORKSPACE_DIR/sros2-keystore"
  # 共有領域（全コンテナから書き込める）なので、シンボリックリンクを置かれていたら辿らずに中止する
  if [ -L "$ws" ]; then
    echo "$ws がシンボリックリンク。辿らずに中止する。手で確認して削除すること。" >&2
    exit 1
  fi
  if [ "$ENV_NAME" = "c" ] && [ -f "$ws/.sros2-generated" ]; then
    # 環境 c では、環境 b が置いた鍵のコピーが全コンテナから読めてしまうため消す
    rm -rf "$ws" || { echo "$ws を削除できなかった。手で削除すること（環境 c では全コンテナから読めてしまう）。" >&2; exit 1; }
  elif [ "$ENV_NAME" = "b" ]; then
    rm -rf "$ws" || { echo "$ws を削除できなかった。手で削除してから再実行すること。" >&2; exit 1; }
    mkdir -p "$ws"
    # 目印を先に置く。コピーの途中で失敗しても、up-env.sh と環境 c の生成が消せるように
    : > "$ws/.sros2-generated"
    cp -r "$FINAL_KS/containers/ros2lab-a/." "$ws/"
    chmod 0755 "$ws"
    chmod 0644 "$ws"/*
  fi
fi

echo "生成した: $FINAL_KS（コンテナ用）、$FINAL_CA（CA の秘密鍵。コンテナには渡さない）、$FINAL_ROGUE/revoked（失効済みの不正証明書）"
