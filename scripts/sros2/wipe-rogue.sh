#!/usr/bin/env bash
# 診断用の不正証明書（sros2/rogue/）を削除する。試験が終わったら実行する。
#   bash scripts/sros2/wipe-rogue.sh
set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -d sros2/rogue ]; then
  rm -rf sros2/rogue
  echo "sros2/rogue を削除した。"
else
  echo "sros2/rogue は無い。"
fi
