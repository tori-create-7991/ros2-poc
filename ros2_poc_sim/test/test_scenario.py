from pathlib import Path

import pytest

from ros2_poc_sim import scenario as S

SCENARIOS = Path(__file__).resolve().parents[1] / 'config' / 'scenarios'
POSE_A = [0.5, 0.3, 0.0, -1.2, 0.0, -0.5, 0.0]


def _one(step):
    return S.parse_scenario({'steps': [step]})


def test_bundled_scenarios_load():
    for name in ('default', 'fail_demo', 'examples'):
        assert S.load_scenario(SCENARIOS / f'{name}.yaml')


def test_positions_step_defaults():
    (s,) = _one({'name': 'a', 'positions': POSE_A, 'time_from_start': 2.5})
    assert s.kind == S.ARM and s.joints == S.ARM_JOINTS
    assert s.expect == tuple(POSE_A) and s.tolerance == S.ARM_TOLERANCE and s.duration == 2.5


@pytest.mark.parametrize('step', [
    {'positions': POSE_A[:6]},                                      # 要素数
    {'positions': [3.0] + POSE_A[1:]},                              # リミット外
    {'positions': ['x'] + POSE_A[1:]},                              # 数値でない
    {'positions': POSE_A, 'time_from_start': 0},                    # 時間 0
    {'positions': POSE_A, 'gripper': 'open'},                       # 種別が 2 つ
    {'name': 'bad name', 'positions': POSE_A},                      # 名前
    {'positions': POSE_A, 'speed': 1},                              # 不明なキー
    {'gripper': 2.0},                                               # グリッパのリミット外
    {'waypoints': [{'positions': POSE_A, 'time_from_start': 2},
                   {'positions': POSE_A, 'time_from_start': 2}]},   # 単調増加でない
    {'random': {'n': 0}},
])
def test_invalid_steps_raise(step):
    with pytest.raises(ValueError):
        _one(step)


def test_empty_or_bad_repeat_raise():
    with pytest.raises(ValueError):
        S.parse_scenario({'steps': []})
    with pytest.raises(ValueError):
        S.parse_scenario({'steps': [{'positions': POSE_A}], 'repeat': 0})


def test_waypoints_judged_at_last_point():
    (s,) = _one({'waypoints': [{'positions': [0.0] * 7, 'time_from_start': 2},
                               {'positions': POSE_A, 'time_from_start': 4}]})
    assert len(s.points) == 2 and s.expect == tuple(POSE_A) and s.duration == 4


def test_gripper_open_close_and_tolerance():
    c, o = S.parse_scenario({'steps': [{'gripper': 'close'}, {'gripper': 'open'}]})
    assert c.kind == S.GRIPPER and c.expect == (S.GRIPPER_CLOSE,) and c.duration == 0
    assert o.expect == pytest.approx((1.0471975512,)) and o.tolerance == S.GRIPPER_TOLERANCE
    assert c.joints == [S.GRIPPER_JOINT]


def test_expect_overrides_only_expectation():
    (s,) = _one({'positions': POSE_A, 'expect': [0.0] * 7})
    assert s.points[0][0] == tuple(POSE_A) and s.expect == (0.0,) * 7


def test_random_is_reproducible_and_within_scaled_limits():
    step = {'name': 'r', 'random': {'n': 5, 'seed': 3, 'time_from_start': 2}}
    a, b = _one(step), _one(step)
    assert [s.points for s in a] == [s.points for s in b]
    assert [s.name for s in a] == ['r_1', 'r_2', 'r_3', 'r_4', 'r_5']
    assert _one({'random': {'n': 5, 'seed': 4}})[0].points != a[0].points
    for s in a:
        for x, joint in zip(s.points[0][0], S.ARM_JOINTS):
            lo, hi = S.ARM_LIMITS[joint]
            mid, half = (lo + hi) / 2, (hi - lo) / 4
            assert mid - half - 1e-4 <= x <= mid + half + 1e-4


def test_repeat_suffixes_names_and_cli_overrides_yaml():
    data = {'repeat': 2, 'steps': [{'name': 'a', 'positions': POSE_A}, {'name': 'b', 'gripper': 'open'}]}
    assert [s.name for s in S.parse_scenario(data)] == ['a#1', 'b#1', 'a#2', 'b#2']
    assert [s.name for s in S.parse_scenario(data, repeat=1)] == ['a', 'b']


def test_arm_command_matches_readme_form():
    (s,) = _one({'positions': POSE_A, 'time_from_start': 3})
    target, cmd = S.to_command(s)
    assert target == 'ros2lab-a'
    assert cmd == (
        'ros2 topic pub --once -w 1 /crane_x7_arm_controller/joint_trajectory '
        'trajectory_msgs/msg/JointTrajectory "{joint_names: [crane_x7_shoulder_fixed_part_pan_joint, '
        'crane_x7_shoulder_revolute_part_tilt_joint, crane_x7_upper_arm_revolute_part_twist_joint, '
        'crane_x7_upper_arm_revolute_part_rotate_joint, crane_x7_lower_arm_fixed_part_joint, '
        'crane_x7_lower_arm_revolute_part_joint, crane_x7_wrist_joint], points: [{positions: '
        '[0.5, 0.3, 0, -1.2, 0, -0.5, 0], time_from_start: {sec: 3, nanosec: 0}}]}"')


def test_waypoint_command_has_all_points_with_nanosec():
    (s,) = _one({'waypoints': [{'positions': [0.0] * 7, 'time_from_start': 1.5},
                               {'positions': POSE_A, 'time_from_start': 3}]})
    cmd = S.to_command(s)[1]
    assert cmd.count('positions:') == 2 and '{sec: 1, nanosec: 500000000}' in cmd


def test_gripper_command_goes_to_ros2arm():
    (s,) = _one({'gripper': 'open'})
    target, cmd = S.to_command(s)
    assert target == 'ros2arm'
    assert cmd == ('ros2 action send_goal /crane_x7_gripper_controller/gripper_cmd '
                   'control_msgs/action/ParallelGripperCommand '
                   '"{command: {name: [crane_x7_gripper_finger_a_joint], position: [1.047198]}}"')


def test_commands_have_no_shell_metacharacters_besides_quotes():
    for s in S.load_scenario(SCENARIOS / 'examples.yaml', repeat=2):
        cmd = S.to_command(s)[1]
        assert not set(cmd) & set('$`\\;&|<>\'\n\t')


def test_wait_after_send_covers_motion_and_settle():
    (s,) = _one({'positions': POSE_A, 'time_from_start': 3})
    assert S.wait_after_send(s) >= 3 + S.SETTLE_SEC + S.SETTLE_WINDOW_SEC
