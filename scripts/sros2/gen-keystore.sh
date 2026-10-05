#!/usr/bin/env bash
# 環境 B / C の SROS2 keystore を生成する。
#   bash scripts/sros2/gen-keystore.sh b|c
# 生成先（いずれも gitignore。コミットしない）:
#   sros2/keystores/<env>/containers/<コンテナ名>/   コンテナに渡す自分の enclave だけ（ro mount 用）
#   sros2/keystores/<env>/env.sh                      up-env.sh が読むコンテナごとの enclave 名
#   sros2/ca-private/<env>/                           CA の秘密鍵と発行台帳。コンテナには渡さない
# ホストに ros2 CLI と openssl があればそのまま、無ければ ros2lab:jazzy の使い捨てコンテナで実行する。
# 実処理は lib/gen-keystore-inner.sh（Docker に依存しない。ros2 CLI と openssl だけを使う）。
set -euo pipefail

cd "$(dirname "$0")/../.."

usage() {
  echo "Usage: bash scripts/sros2/gen-keystore.sh b|c" >&2
  exit 2
}

[ "$#" -eq 1 ] || usage
case "$1" in
  b | c) ;;
  *) usage ;;
esac

# 同じ環境のコンテナが起動中なら、再生成すると古い鍵のまま動き続ける（作り直すまで検証も通らない）
if command -v docker >/dev/null 2>&1 && [ -n "$(docker ps --filter "label=ros2poc.env=$1" --format '{{.Names}}' 2>/dev/null)" ]; then
  echo "警告: 環境 $1 のコンテナが起動中。再生成した鍵を使うには 'bash scripts/up-env.sh $1' で作り直すこと。" >&2
fi

if command -v ros2 >/dev/null 2>&1 && command -v openssl >/dev/null 2>&1; then
  exec bash scripts/sros2/lib/gen-keystore-inner.sh "$1" "$PWD/sros2" "$PWD/workspace"
fi

if ! docker image inspect ros2lab:jazzy >/dev/null 2>&1; then
  echo "ros2lab:jazzy イメージが無い。先に 'docker compose build' すること。" >&2
  exit 1
fi

# ros2 CLI が使うホームを書き込める場所にする（コンテナ内は呼び出したユーザーの uid で動かす）
exec docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp -e SROS2_TEST_FAIL_SWAP \
  -v "$PWD/sros2:/w" -v "$PWD/workspace:/ws" -v "$PWD/scripts/sros2/lib:/lib-sros2:ro" \
  ros2lab:jazzy bash /lib-sros2/gen-keystore-inner.sh "$1" /w /ws
