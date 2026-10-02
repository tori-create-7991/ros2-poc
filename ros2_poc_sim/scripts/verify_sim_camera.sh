#!/usr/bin/env bash
# カメラプロファイルの検証（V1〜V7b）をホストから docker exec で回す。
# 前提: ros2arm と ros2lab-a が起動済みで、ros2arm で次を起動済み（別端末で実行したままにする）:
#   ros2 launch ros2_poc_sim arm_with_camera.launch.py profile:=realsense_d435
# アームが動く操作（camera_example.launch.py / pick_and_place_tf）は含めない。
# 使い方: bash ros2_poc_sim/scripts/verify_sim_camera.sh [realsense_d435|usb_cam]
set -uo pipefail

cd "$(dirname "$0")/../.."
PROFILE="${1:-realsense_d435}"
ARM="${ARM:-ros2arm}"
LAB="${LAB:-ros2lab-a}"
SHARE=/opt/ros2_poc_ws/install/ros2_poc_sim/share/ros2_poc_sim
OUT_DIR="${OUT_DIR:-workspace}"
# 物体の期待位置（base_link, 立方体の中心）。config/placements の object と一致させる
EXPECT="${EXPECT:-0.20 0.10 0.02}"
TOL="${TOL:-0.015}"
ROS_ENV='source /opt/ros/jazzy/setup.bash; source /opt/crane_ws/install/setup.bash; source /opt/ros2_poc_ws/install/setup.bash'

pass=0; fail=0
report() { # name status detail
  if [ "$2" = ok ]; then pass=$((pass+1)); else fail=$((fail+1)); fi
  printf '%-4s %-34s %s\n' "$2" "$1" "$3"
}

case "$PROFILE" in
  realsense_d435) COLOR=/camera/color/image_raw; CONTRACT=realsense_d435.yaml ;;
  usb_cam)        COLOR=/image_raw;              CONTRACT=usb_cam.yaml ;;
  *) echo "未知のプロファイル: $PROFILE" >&2; exit 2 ;;
esac

for c in "$ARM" "$LAB"; do
  [ -n "$(docker ps --filter "name=^${c}$" --filter status=running -q)" ] || { echo "$c が起動していない" >&2; exit 2; }
done

# V1: コンテナ内の単体・結合テスト
if out=$(docker exec -u ubuntu "$ARM" bash -c "$ROS_ENV; cd /opt/ros2_poc_ws/src/ros2_poc_sim && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider test 2>&1 | tail -1"); then
  case "$out" in *passed*) report V1-pytest ok "$out" ;; *) report V1-pytest FAIL "$out" ;; esac
else
  report V1-pytest FAIL "実行できない"
fi

# V3: 仮想カメラのトピックが出ている（購読者なしで一覧に出る）
topics=$(docker exec "$LAB" bash -lc 'ros2 topic list 2>/dev/null' || true)
if printf '%s\n' "$topics" | grep -qx "$COLOR"; then
  report V3-topic-visible-from-lab ok "$COLOR"
else
  report V3-topic-visible-from-lab FAIL "$COLOR が ros2lab から見えない（arm_with_camera.launch.py を起動したか。Discovery に 10〜20 秒かかる）"
fi

# V4/V5/V6/V8: 出力契約（型・エンコーディング・frame_id・Hz・同一 stamp・TF）を ros2lab 側から検査
docker cp ros2_poc_sim/scripts/contract_check.py "$LAB":/tmp/contract_check.py >/dev/null
docker cp "ros2_poc_sim/config/contracts/$CONTRACT" "$LAB":/tmp/contract.yaml >/dev/null
if out=$(docker exec "$LAB" bash -lc 'python3 /tmp/contract_check.py /tmp/contract.yaml --seconds 15 2>&1'); then
  report V4-V6-contract ok "$(printf '%s' "$out" | tail -1)"
else
  report V4-V6-contract FAIL "$(printf '%s' "$out" | grep -E 'FAIL|-' | head -3 | tr '\n' ' ')"
fi

# V5: 画像を ros2lab で保存し、中身を目で確認できるようにする
docker cp ros2_poc_sim/scripts/save_frame.py "$LAB":/tmp/save_frame.py >/dev/null
mkdir -p "$OUT_DIR"
if out=$(docker exec "$LAB" bash -lc "python3 /tmp/save_frame.py $COLOR /tmp/sim_camera_color.png 90 2>&1 | grep -E '^(saved|ERROR)' | tail -1") \
   && docker cp "$LAB":/tmp/sim_camera_color.png "$OUT_DIR/sim_camera_color.png" >/dev/null 2>&1; then
  report V5-save-frame ok "$OUT_DIR/sim_camera_color.png ($out)"
else
  report V5-save-frame FAIL "$out"
fi

# V7b: 簡易検出スクリプト（realsense_d435 のみ。深度が要る）
if [ "$PROFILE" = realsense_d435 ]; then
  docker cp ros2_poc_sim/scripts/vla_stub_detect.py "$LAB":/tmp/vla_stub_detect.py >/dev/null
  log=$(docker exec "$LAB" bash -lc 'timeout -s INT 45 python3 /tmp/vla_stub_detect.py 2>&1' || true)
  line=$(printf '%s\n' "$log" | grep 'base_link=' | tail -1)
  if [ -z "$line" ]; then
    report V7b-stub-detect FAIL "base_link 基準の target_0 が出ない"
  else
    res=$(python3 - "$line" "$EXPECT" "$TOL" <<'PY'
import re, sys
m = re.search(r'base_link=\(([-\d.]+),([-\d.]+),([-\d.]+)\)', sys.argv[1])
got = [float(v) for v in m.groups()]
exp = [float(v) for v in sys.argv[2].split()]
tol = float(sys.argv[3])
err = [abs(a - b) for a, b in zip(got, exp)]
print(('ok' if max(err) <= tol else 'FAIL'), 'got=%s exp=%s maxerr=%.3f tol=%.3f' % (got, exp, max(err), tol))
PY
)
    report V7b-stub-detect "${res%% *}" "${res#* }"
  fi
fi

echo "---- pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
