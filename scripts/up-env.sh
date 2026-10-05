#!/usr/bin/env bash
# SROS2 の環境 A / B / C を切り替えて ros2lab-a/b を起動し直す。
#   a: SROS2 未適用（現行の compose そのまま）
#   b: SROS2 適用済み + 意図的な誤設定（台帳 sros2/ledger/ledger.yaml の不備）
#   c: 推奨どおりの SROS2（Enforce）
# b / c は先に `bash scripts/sros2/gen-keystore.sh <b|c>` で keystore を生成しておく。
# 切替はコンテナの作り直し（環境変数と mount が変わるため）。
# 直接 `docker compose up` を使うと環境ラベルと keystore の mount が付かないため、
# SROS2 を使うときは必ずこのスクリプト経由で起動すること。
#   bash scripts/up-env.sh a|b|c [--arm]
# --arm は環境 a のときだけ使える（ros2arm はまだ SROS2 化していないため、
# b / c と一緒に起動すると ros2arm だけ環境 A のまま混在する）。
set -euo pipefail

cd "$(dirname "$0")/.."

usage() {
  echo "Usage: bash scripts/up-env.sh a|b|c [--arm]" >&2
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
for arg in "$@"; do
  case "$arg" in
    --arm) ARM=1 ;;
    *) usage ;;
  esac
done

if [ "$ENV_NAME" != "a" ] && [ "$ARM" -eq 1 ]; then
  echo "環境 $ENV_NAME では --arm を使えない。ros2arm はまだ SROS2 化していないため、ros2arm だけ環境 A のまま混在する。" >&2
  exit 2
fi

# SROS2_KEYSTORE_ROOT はテスト用の上書き
KS_ROOT="${SROS2_KEYSTORE_ROOT:-sros2/keystores}"
case "$KS_ROOT" in
  /*) ;;
  *) KS_ROOT="$PWD/$KS_ROOT" ;;
esac

export SROS2_ENV="$ENV_NAME"
export SROS2_KEYSTORE_ROOT="$KS_ROOT"

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
    rm -rf "$WS_DIR/sros2-keystore"
  fi
}

if [ "$ENV_NAME" = "a" ]; then
  if [ "$ARM" -eq 1 ]; then
    docker compose --profile arm up -d --build --force-recreate
  else
    docker compose up -d --build --force-recreate
  fi
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

if [ -n "$(docker ps --filter 'name=^ros2arm$' --filter 'status=running' -q)" ]; then
  echo "警告: ros2arm が起動中。ros2arm は SROS2 化していない（環境 A のまま）ため、ros2lab とは通信できない。" >&2
fi

# gen-keystore.sh が書く、コンテナごとの enclave 名の対応（SROS2_ENCLAVE_*）を読む
if [ -f "$KS_ROOT/$ENV_NAME/env.sh" ]; then
  # shellcheck source=/dev/null
  . "$KS_ROOT/$ENV_NAME/env.sh"
fi

case "$ENV_NAME" in
  b) export SROS2_STRATEGY=Permissive ;;
  c) export SROS2_STRATEGY=Enforce ;;
esac

docker compose -f docker-compose.yml -f docker-compose.sros2.yml up -d --build --force-recreate
if [ "$ENV_NAME" = "c" ]; then
  cleanup_leaked_keys
fi
