#!/usr/bin/env bash
# SROS2 の環境 A / B / C を切り替えて ros2lab-a/b（と、指定すれば ros2arm・ros2server）を起動し直す。
#   a: SROS2 未適用（現行の compose そのまま）
#   b: SROS2 適用済み + 意図的な誤設定（台帳 sros2/ledger/ledger.yaml の不備）
#   c: 推奨どおりの SROS2（Enforce）
# b / c は先に `bash scripts/sros2/gen-keystore.sh <b|c>` で keystore を生成しておく。
# 切替はコンテナの作り直し（環境変数と mount が変わるため）。
# 直接 `docker compose up` を使うと環境ラベルと keystore の mount が付かないため、
# SROS2 を使うときは必ずこのスクリプト経由で起動すること。
#   bash scripts/up-env.sh a|b|c [--arm] [--vla]
#   --arm: ros2arm（アームシミュ）も同じ環境で起動し直す（profile arm）。
#   --vla: ros2server と vla-server（VLA 連携）も同じ環境で起動し直す（profile vla。vla-server は ROS を使わないので環境 a/b/c の対象外）。
# ros2arm / ros2server は ros2lab-a/b と同じ環境で起動しないと DDS で通信できない（環境が混在する）。
# 環境を切り替えたら、動かしているシミュ・ノードは起動し直しになる。
set -euo pipefail

cd "$(dirname "$0")/.."

usage() {
  echo "Usage: bash scripts/up-env.sh a|b|c [--arm] [--vla]" >&2
  exit 2
}

[ "$#" -ge 1 ] || usage
ENV_NAME="$1"
shift
case "$ENV_NAME" in
  a | b | c) ;;
  *) usage ;;
esac

ARM=0
VLA=0
for arg in "$@"; do
  case "$arg" in
    --arm) ARM=1 ;;
    --vla) VLA=1 ;;
    *) usage ;;
  esac
done
# 起動し直す profile（指定した分だけ）。指定しなかったコンテナは触らない。
# 空の配列を set -u で展開すると macOS 標準の bash 3.2 が失敗するので、${PROFILES[@]+"${PROFILES[@]}"} の形で展開する
PROFILES=()
[ "$ARM" -eq 1 ] && PROFILES+=(--profile arm)
[ "$VLA" -eq 1 ] && PROFILES+=(--profile vla)

# SROS2_KEYSTORE_ROOT はテスト用の上書き
KS_ROOT="${SROS2_KEYSTORE_ROOT:-sros2/keystores}"
case "$KS_ROOT" in
  /*) ;;
  *) KS_ROOT="$PWD/$KS_ROOT" ;;
esac

export SROS2_ENV="$ENV_NAME"
export SROS2_KEYSTORE_ROOT="$KS_ROOT"
# 呼び出し元の環境に残っていても、環境 b に crl.pem の要求が渡らないようにする（c のときだけ下で設定する）
unset SROS2_REQUIRE_CRL

# 診断コンテナが mount する場所。無いと docker が root 所有の空ディレクトリを作ってしまう
mkdir -p sros2/rogue

# 環境 b の不備（B-AU-07）として ./workspace に置いた鍵のコピーは、環境 a / c では不要で、
# c では全コンテナから読めてしまい「鍵は自分の enclave だけ」を壊す。a / c に切り替えるときに削除する。
# 目印ファイル（gen-keystore.sh が置く）があるときだけ消す。同じ名前の別のディレクトリは触らない。
# compose が成功してから消す（失敗したときは環境 b が残るので、コピーも残す）。
WS_DIR="${SROS2_WORKSPACE_DIR:-workspace}"
cleanup_leaked_keys() {
  if [ -f "$WS_DIR/sros2-keystore/.sros2-generated" ]; then
    echo "環境 b の不備として $WS_DIR に置いた鍵のコピー（sros2-keystore）を削除する。" >&2
    # 切替は成功しているので、消せなくても失敗にはしない（コンテナが root で作ったファイルなど）
    rm -rf "$WS_DIR/sros2-keystore" || echo "警告: $WS_DIR/sros2-keystore を削除できなかった。手で削除すること。" >&2
  fi
}

if [ "$ENV_NAME" = "a" ]; then
  docker compose ${PROFILES[@]+"${PROFILES[@]}"} up -d --build --force-recreate
  cleanup_leaked_keys
  exit 0
fi

# --- b / c ---

# SROS2 未適用の実機と混ぜない
if [ -n "$(docker ps --filter 'name=^ros2real$' --filter 'status=running' -q)" ]; then
  echo "ros2real が起動中。SROS2 未適用の実機と環境 $ENV_NAME を混ぜないため、先に 'bash scripts/down-real.sh' すること。" >&2
  exit 1
fi

if [ ! -d "$KS_ROOT/$ENV_NAME" ]; then
  echo "環境 $ENV_NAME の keystore が無い。先に 'bash scripts/sros2/gen-keystore.sh $ENV_NAME' を実行すること。" >&2
  exit 1
fi

# 指定しなかった ros2arm / ros2server が動いていると、前の環境のまま混在して ros2lab・ros2arm・ros2server が通信できない
for pair in "ros2arm:--arm:$ARM" "ros2server:--vla:$VLA"; do
  c="${pair%%:*}"
  rest="${pair#*:}"
  flag="${rest%%:*}"
  want="${rest##*:}"
  if [ "$want" -eq 0 ] && [ -n "$(docker ps --filter "name=^$c\$" --filter 'status=running' -q)" ]; then
    echo "警告: $c が起動中。$flag を付けていないので環境 $ENV_NAME に切り替わらず、前の環境のまま混在して ros2lab と通信できない。" >&2
  fi
done

# gen-keystore.sh が書く、コンテナごとの enclave 名の対応（SROS2_ENCLAVE_*）を読む。
# 無いと既定の enclave 名で起動してしまい、環境 b の意図（enclave 名の不一致）を壊すので、無ければ中断する。
# この中身は export 行だけ。keystore の置き場（SROS2_KEYSTORE_ROOT）は信頼できる場所だけを指すこと。
if [ ! -f "$KS_ROOT/$ENV_NAME/env.sh" ]; then
  echo "環境 $ENV_NAME の env.sh が無い。'bash scripts/sros2/gen-keystore.sh $ENV_NAME' で作り直すこと。" >&2
  exit 1
fi
# shellcheck source=/dev/null
. "$KS_ROOT/$ENV_NAME/env.sh"

case "$ENV_NAME" in
  b) export SROS2_STRATEGY=Permissive ;;
  c)
    export SROS2_STRATEGY=Enforce
    export SROS2_REQUIRE_CRL=true # 環境 c は crl.pem が無ければコンテナを起動しない
    ;;
esac

docker compose -f docker-compose.yml -f docker-compose.sros2.yml ${PROFILES[@]+"${PROFILES[@]}"} up -d --build --force-recreate
if [ "$ENV_NAME" = "c" ]; then
  cleanup_leaked_keys
fi
