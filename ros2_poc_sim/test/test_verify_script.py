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


def test_script_never_runs_arm_moving_examples():
    text = SCRIPT.read_text(encoding='utf-8')
    code = '\n'.join(l for l in text.splitlines() if not l.lstrip().startswith('#'))
    assert 'camera_example.launch' not in code and 'pick_and_place' not in code
