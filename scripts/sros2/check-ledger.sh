#!/usr/bin/env bash
# 環境 B の台帳と期待結果を検査する。
#   bash scripts/sros2/check-ledger.sh             検査
#   bash scripts/sros2/check-ledger.sh --render    docs/sros2/defect-ledger.md を台帳から再生成
#   bash scripts/sros2/check-ledger.sh --list      台帳の「ID<TAB>probe 名<TAB>題名」を表示（verify-env.sh が使う）
#   bash scripts/sros2/check-ledger.sh --selftest  検査が壊れた台帳をちゃんと落とすかを確認する
# PyYAML が無いホストでは ros2lab:jazzy の使い捨てコンテナで実行する。
set -euo pipefail

cd "$(dirname "$0")/../.."

mode=check
case "${1:-}" in
  "") ;;
  --render) mode=render ;;
  --list) mode=list ;;
  --selftest) mode=selftest ;;
  *)
    echo "Usage: bash scripts/sros2/check-ledger.sh [--render|--list|--selftest]" >&2
    exit 2
    ;;
esac

run() {
  if python3 -c 'import yaml' >/dev/null 2>&1; then
    python3 scripts/sros2/lib/ledger.py "$1" "$PWD"
  else
    docker run --rm -u "$(id -u):$(id -g)" -v "$PWD:/repo:ro" ros2lab:jazzy \
      python3 /repo/scripts/sros2/lib/ledger.py "$1" /repo
  fi
}

case "$mode" in
  render)
    run render > docs/sros2/defect-ledger.md
    echo "docs/sros2/defect-ledger.md を再生成した。"
    ;;
  list) run list 2>/dev/null ;;
  selftest)
    if python3 -c 'import yaml' >/dev/null 2>&1; then
      python3 scripts/sros2/lib/test_ledger.py "$PWD"
    else
      docker run --rm -u "$(id -u):$(id -g)" -v "$PWD:/repo:ro" ros2lab:jazzy python3 /repo/scripts/sros2/lib/test_ledger.py /repo
    fi
    ;;
  *) run check ;;
esac
