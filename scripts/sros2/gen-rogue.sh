#!/usr/bin/env bash
# 診断用の不正証明書（試験用）を生成する。閉域の診断コンテナ（ros2diag）だけに渡し、コミットしない。
#   bash scripts/sros2/gen-rogue.sh b|c valid|wrongca|selfsigned|expired|all [期限までの秒数]
# 失効済み（revoked）は gen-keystore.sh が keystore と一緒に作る（CRL に載せる必要があるため）。
# 流出防止: 生成先 sros2/rogue/ は gitignore 済みで、ros2lab-a/b・ros2arm には mount しない。
# 不要になったら `bash scripts/sros2/wipe-rogue.sh` で削除する。
set -euo pipefail

cd "$(dirname "$0")/../.."

usage() {
  echo "Usage: bash scripts/sros2/gen-rogue.sh b|c valid|wrongca|selfsigned|expired|all [seconds]" >&2
  exit 2
}

[ "$#" -ge 2 ] && [ "$#" -le 3 ] || usage
case "$1" in b | c) ;; *) usage ;; esac
case "$2" in valid | wrongca | selfsigned | expired | all) ;; *) usage ;; esac

# ros2 CLI があるホスト（= OpenSSL 3 系の ROS 環境）ならそのまま、無ければ使い捨てコンテナで実行する。
# macOS の openssl は LibreSSL で挙動が違うため、ホストの openssl だけを理由に直接実行しない。
if command -v ros2 >/dev/null 2>&1 && command -v openssl >/dev/null 2>&1 && command -v python3 >/dev/null 2>&1; then
  exec bash scripts/sros2/lib/gen-rogue-inner.sh "$1" "$2" "$PWD/sros2" "${3:-45}"
fi

exec docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp \
  -v "$PWD/sros2:/w" -v "$PWD/scripts/sros2/lib:/lib-sros2:ro" \
  ros2lab:jazzy bash /lib-sros2/gen-rogue-inner.sh "$1" "$2" /w "${3:-45}"
