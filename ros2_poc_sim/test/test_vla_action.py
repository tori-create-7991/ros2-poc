import json
import math

import pytest

from ros2_poc_sim import scenario as S
from ros2_poc_sim import vla_action as A

ID_QUAT = (0.0, 0.0, 0.0, 1.0)


def _msg(**kw):
    d = {'seq': 1, 'delta': [0.01, 0, 0, 0, 0, 0], 'gripper': 1.0}
    d.update(kw)
    return json.dumps(d)


def test_parse_action_ok():
    a = A.parse_action(_msg(seq=7, delta=[0.01, -0.02, 0.0, 0.0, 0.0, 0.05], gripper=0.25))
    assert a == A.Action(7, (0.01, -0.02, 0.0, 0.0, 0.0, 0.05), 0.25)


@pytest.mark.parametrize('raw', [
    'not json', '[]', '"x"', _msg(seq=True), _msg(seq=-1), _msg(seq=1.5), _msg(seq='1'),
    _msg(delta=[0] * 5), _msg(delta=[0] * 7), _msg(delta='abcdef'), _msg(delta=[0, 0, 0, 0, 0, 'x']),
    _msg(delta=[0, 0, 0, 0, 0, True]), _msg(gripper='1'), _msg(gripper=None),
    '{"seq":1,"delta":[0,0,0,0,0,NaN],"gripper":1}', '{"seq":1,"delta":[0,0,0,0,0,0],"gripper":Infinity}',
    _msg(delta=[1e9, 0, 0, 0, 0, 0]),
])
def test_parse_action_rejects(raw):
    with pytest.raises(ValueError):
        A.parse_action(raw)


def test_parse_action_rejects_oversized_message():
    with pytest.raises(ValueError, match='大きすぎ'):
        A.parse_action(_msg() + ' ' * A.MAX_MESSAGE_CHARS)


def test_parse_action_clamps_gripper_to_unit_range():
    assert A.parse_action(_msg(gripper=1.4)).gripper == 1.0
    assert A.parse_action(_msg(gripper=-0.2)).gripper == 0.0


def test_action_from_vector():
    a = A.action_from_vector(3, [0.1, 0, 0, 0, 0, 0, 0.9])
    assert a == A.Action(3, (0.1, 0, 0, 0, 0, 0), 0.9)
    with pytest.raises(ValueError, match='7'):
        A.action_from_vector(3, [0] * 6)
    with pytest.raises(ValueError, match='有限'):
        A.action_from_vector(3, [0, 0, 0, 0, 0, float('inf'), 0])


def test_format_roundtrip_and_ack():
    a = A.Action(2, (0.01, 0, 0, 0, 0, 0), 0.0)
    assert A.parse_action(A.format_action(a)) == a
    ack = json.loads(A.format_ack(2, 'ok'))
    assert ack == {'seq': 2, 'status': 'ok', 'reason': ''}
    assert A.parse_ack(A.format_ack(5, 'ik_failed', '解なし')) == (5, 'ik_failed', '解なし')
    with pytest.raises(ValueError):
        A.format_ack(1, 'unknown_status')
    with pytest.raises(ValueError):
        A.parse_ack('{"seq":1,"status":"bogus","reason":""}')


def test_clamp_delta_keeps_direction_and_limits_norm():
    d, clamped = A.clamp_delta((0.03, 0.04, 0, 0, 0, 0))
    assert clamped
    assert math.isclose(math.hypot(d[0], d[1]), A.MAX_LINEAR)
    assert math.isclose(d[1] / d[0], 4 / 3)
    d, clamped = A.clamp_delta((0, 0, 0, 0.3, 0, 0.4))
    assert clamped and math.isclose(math.hypot(d[3], d[5]), A.MAX_ANGULAR)
    d, clamped = A.clamp_delta((0.01, 0, 0, 0.01, 0, 0))
    assert not clamped and d == (0.01, 0, 0, 0.01, 0, 0)


def test_apply_delta_translation_in_base_frame():
    pos, q = A.apply_delta((0.1, 0.2, 0.3), ID_QUAT, (0.01, -0.02, 0.03, 0, 0, 0))
    assert pos == pytest.approx((0.11, 0.18, 0.33)) and q == pytest.approx(ID_QUAT)


def test_apply_delta_yaw_rotates_about_base_z():
    _pos, q = A.apply_delta((0, 0, 0), ID_QUAT, (0, 0, 0, 0, 0, math.pi / 2))
    assert q == pytest.approx((0, 0, math.sin(math.pi / 4), math.cos(math.pi / 4)))
    # ベース基準の増分: 先に 90 度ロールしている姿勢に、ベースの z 回りの 90 度を左から掛ける
    q0 = (math.sin(math.pi / 4), 0, 0, math.cos(math.pi / 4))
    _pos, q2 = A.apply_delta((0, 0, 0), q0, (0, 0, 0, 0, 0, math.pi / 2))
    assert math.isclose(sum(v * v for v in q2), 1.0)
    assert q2 == pytest.approx((0.5, 0.5, 0.5, 0.5))


def test_clamp_to_workspace():
    p, clamped = A.clamp_to_workspace((0.1, 0.1, 0.3))
    assert p == (0.1, 0.1, 0.3) and not clamped
    p, clamped = A.clamp_to_workspace((9.0, -9.0, -1.0))
    assert clamped and p == (A.WORKSPACE['x'][1], A.WORKSPACE['y'][0], A.WORKSPACE['z'][0])


def test_gripper_target_and_angle():
    assert A.gripper_target(0.9) == 'open' and A.gripper_target(0.1) == 'close'
    assert A.gripper_target(0.5) == 'open'   # 閾値ちょうどは開く側
    assert A.gripper_target(0.9, open_when_high=False) == 'close'
    assert A.gripper_target(0.1, open_when_high=False) == 'open'
    assert A.gripper_angle('open') == S.GRIPPER_OPEN and A.gripper_angle('close') == S.GRIPPER_CLOSE
    with pytest.raises(ValueError):
        A.gripper_angle('half')
