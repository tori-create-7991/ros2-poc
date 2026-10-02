"""verify_sim_camera.sh の期待値が config/placements と食い違わないことを検査する。"""
import re
from pathlib import Path

import pytest

from ros2_poc_sim import profile as P

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'verify_sim_camera.sh'


def test_expected_object_position_matches_placement():
    text = SCRIPT.read_text(encoding='utf-8')
    m = re.search(r'EXPECT="\$\{EXPECT:-([-\d. ]+)\}"', text)
    assert m, 'EXPECT の既定値が見つからない'
    x, y, z = (float(v) for v in m.group(1).split())
    obj = P.load_placement('fixed_near_top')['object']['xyz_in_base_link']
    assert (x, y) == pytest.approx((obj[0], obj[1]))
    assert z == pytest.approx(0.02)       # 立方体の半辺（机上面が base_link の z=0）


def test_v1_judged_by_exit_code_not_substring():
    text = SCRIPT.read_text(encoding='utf-8')
    assert 'PYTEST_RC' in text
    assert '*passed*)' not in text         # "1 failed, 69 passed" を ok にしてしまう判定は使わない
    assert 'ROS_DOMAIN_ID=97' in text      # 稼働中のドメインに混ざらない


def test_script_never_runs_arm_moving_examples():   # pick_and_place_tf は検出のみ（実行はしない）
    text = SCRIPT.read_text(encoding='utf-8')
    code = '\n'.join(l for l in text.splitlines() if not l.lstrip().startswith('#'))
    assert 'camera_example.launch' not in code and 'ros2 run crane_x7_examples pick' not in code


def _run_with_fake_docker(tmp_path, pytest_out, stale=False):
    """PATH 先頭の偽 docker で verify_sim_camera.sh を実行し、V1 の行を返す。"""
    import os
    import stat
    import subprocess
    fake = tmp_path / 'docker'
    out_file = tmp_path / 'pytest_out.txt'
    out_file.write_text(pytest_out + '\n')
    fake.write_text(f"""#!/usr/bin/env bash
case "$1" in
  ps) echo cid; exit 0;;
  exec)
    all="$*"
    if echo "$all" | grep -q 'sha1sum'; then
      if [ "{int(stale)}" = 1 ]; then echo deadbeef; else
        (cd "$REPO/ros2_poc_sim" && find ros2_poc_sim test config launch scripts -type f ! -name '*.pyc' ! -path '*__pycache__*' | LC_ALL=C sort | xargs shasum | shasum | cut -d' ' -f1)
      fi; exit 0
    fi
    if echo "$all" | grep -q 'pytest'; then cat "{out_file}"; exit 0; fi
    exit 1;;
  *) exit 0;;
esac
""")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    repo = SCRIPT.parents[2]
    env = dict(os.environ, PATH=f'{tmp_path}:{os.environ["PATH"]}', REPO=str(repo))
    r = subprocess.run(['bash', str(SCRIPT), 'usb_cam'], capture_output=True, text=True, env=env,
                       cwd=str(repo), timeout=120)
    line = next((l for l in r.stdout.splitlines() if 'V1-pytest' in l), '')
    return line


@pytest.mark.skipif(__import__('shutil').which('shasum') is None, reason='shasum が無い')
def test_v1_fails_on_nonzero_rc_even_if_output_says_passed(tmp_path):
    line = _run_with_fake_docker(tmp_path, '=== 1 failed, 69 passed ===\nPYTEST_RC=1')
    assert line.startswith('FAIL'), line


@pytest.mark.skipif(__import__('shutil').which('shasum') is None, reason='shasum が無い')
def test_v1_ok_on_rc0(tmp_path):
    line = _run_with_fake_docker(tmp_path, '70 passed in 1s\nPYTEST_RC=0')
    assert line.startswith('ok'), line


@pytest.mark.skipif(__import__('shutil').which('shasum') is None, reason='shasum が無い')
def test_v1_fails_when_image_is_stale(tmp_path):
    line = _run_with_fake_docker(tmp_path, '70 passed in 1s\nPYTEST_RC=0', stale=True)
    assert line.startswith('FAIL') and '古い' in line, line


def test_script_guards_against_arm_moving_node():
    assert 'pick_and_place_tf' in SCRIPT.read_text(encoding='utf-8')
