#!/usr/bin/env bash
# シナリオ E2E（シミュ起動 → 指令 → 仮想カメラ判定 → 録画）を順に回し、期待どおりの終了コード・判定になったかを検査する。
# CI（.github/workflows/scenario-e2e.yml）用だが、ros2arm / ros2lab-a が起動済みならローカルでも使える。
#   bash scripts/ci-scenario-e2e.sh [シナリオ名...]    既定: default fail_demo examples
# 期待: fail_demo は意図的に wrong_expect だけが FAIL（終了コード 1）、それ以外は全 PASS（終了コード 0）。
# 1 つ失敗しても残りは回し、最後にまとめて非 0 で終わる。
set -uo pipefail

cd "$(dirname "$0")/.." || exit 2

if [ "$#" -gt 0 ]; then scenarios=("$@"); else scenarios=(default fail_demo examples); fi

summary() { if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then printf '%s\n' "$1" >> "$GITHUB_STEP_SUMMARY"; fi; }

# ros2arm のデスクトップ（x11grab の録画元）が立ち上がるまで待つ
deadline=$((SECONDS + 300))
until docker exec -u ubuntu -e DISPLAY=:1 ros2arm xdpyinfo -display :1 > /dev/null 2>&1; do
  if [ "$SECONDS" -ge "$deadline" ]; then
    echo "ros2arm のデスクトップ (DISPLAY=:1) が 300 秒たっても起動しない" >&2
    exit 2
  fi
  sleep 5
done

summary '| scenario | exit | 期待 | 結果 |'
summary '|---|---|---|---|'

failed=0
for sc in "${scenarios[@]}"; do
  case "$sc" in
    fail_demo) want=1 ;;
    *) want=0 ;;
  esac
  echo "::group::scenario $sc (期待する終了コード $want)"
  rc=0
  bash scripts/run-scenario.sh --start-sim --scenario "$sc" || rc=$?
  echo "::endgroup::"

  # 日時名のディレクトリのうち最新（sim-*.log は対象外）
  run_dir="$(find workspace/runs -mindepth 1 -maxdepth 1 -type d -name '20*' | sort | tail -1)"
  detail=ok
  if [ "$rc" -ne "$want" ]; then
    detail="終了コード $rc（期待 $want）"
  elif [ ! -f "$run_dir/result.json" ] || [ ! -s "$run_dir/scenario.mp4" ]; then
    detail="result.json か scenario.mp4 が無い（$run_dir）"
  elif [ "$sc" = fail_demo ]; then
    # FAIL を出せることの確認: 期待値を差し替えた wrong_expect だけが FAIL であること
    detail="$(python3 - "$run_dir/result.json" <<'PY'
import json, sys
steps = json.load(open(sys.argv[1], encoding='utf-8'))['steps']
failed = sorted(s['name'] for s in steps if s['verdict'] != 'PASS')
print('ok' if failed == ['wrong_expect'] else f'FAIL したステップが想定外: {failed}')
PY
)"
  fi

  if [ "$detail" = ok ]; then
    summary "| $sc | $rc | $want | ✅ |"
  else
    echo "::error::scenario $sc: $detail" >&2
    summary "| $sc | $rc | $want | ❌ $detail |"
    failed=1
  fi
done

exit "$failed"
