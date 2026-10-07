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


@pytest.mark.parametrize('raw, expected', [
    ('{"seq": 7, "delta": "bad"}', 7),
    ('{"seq": 0}', 0),
    ('{"seq": true}', None), ('{"seq": -1}', None), ('{"seq": "1"}', None), ('{"delta": []}', None),
    ('not json', None), ('[]', None), (None, None), ('x' * 2000, None),
])
def test_peek_seq_reads_seq_even_when_the_message_is_invalid(raw, expected):
    assert A.peek_seq(raw) == expected


def test_reject_ack_for_answers_invalid_messages_that_have_a_seq():
    ack = A.reject_ack_for('{"seq": 7, "delta": "bad"}', ValueError('delta が不正'))
    assert A.parse_ack(ack) == (7, 'rejected', '不正な指令: delta が不正')
    long = A.reject_ack_for('{"seq": 1}', ValueError('x' * 1000))
    assert len(A.parse_ack(long)[2]) <= 200
    assert A.reject_ack_for('not json', ValueError('x')) is None      # seq が読めなければ返せない
    assert A.reject_ack_for('{"seq": true}', ValueError('x')) is None


def test_ack_detail_roundtrip_and_old_parse_ack_still_works():
    detail = {'target_joints': [0.1, 0.1, 0.1, -0.5, 0.1, 0.1, 0.1], 'duration': 1.5, 't_sent': 1.7e9, 'ee_before': [0.1, 0.0, 0.4],
              'ee_cmd': [0.1, 0.0, 0.38], 'ee_after': [0.1, 0.0, 0.381], 'ee_error': 0.001, 'gripper': None,
              'clamped': False}
    raw = A.format_ack(3, 'ok', '', detail)
    assert len(raw) <= A.MAX_ACK_CHARS
    assert A.parse_ack_full(raw) == (3, 'ok', '', detail)
    assert A.parse_ack(raw) == (3, 'ok', '')
    assert A.parse_ack_full(A.format_ack(3, 'ok'))[3] is None
    assert A.parse_ack_full(A.format_ack(3, 'ik_failed', 'x', {'ee_before': [0, 0, 0]}))[3] == {'ee_before': [0.0] * 3}


@pytest.mark.parametrize('detail', [
    {'target_joints': [0.0] * 6}, {'target_joints': [float('nan')] * 7}, {'ee_cmd': [0, 0]},
    {'ee_error': -1.0}, {'ee_error': float('inf')}, {'gripper': 'half'}, {'clamped': 1}, {'unknown': 1},
    {'t_sent': True}, {'t_sent': 0}, {'duration': 0}, {'duration': 1e9}, {'target_joints': [9.0] * 7}, 'x', [1],
])
def test_bad_ack_detail_is_rejected_when_sending_but_the_ack_itself_is_still_received(detail):
    with pytest.raises(ValueError):
        A.format_ack(1, 'ok', '', detail)
    raw = json.dumps({'seq': 1, 'status': 'ok', 'reason': 'r', 'detail': detail})
    # 受け取る側は、detail だけ不正でも seq・status・reason は受ける（版違いで 240 秒待たない）
    assert A.parse_ack_full(raw) == (1, 'ok', 'r', None)


def test_ack_size_limit_is_larger_than_action_limit():
    big = json.dumps({'seq': 1, 'status': 'ok', 'reason': 'x' * 2000})
    assert len(big) > A.MAX_MESSAGE_CHARS and A.parse_ack(big)[0] == 1
    with pytest.raises(ValueError):
        A.parse_ack(json.dumps({'seq': 1, 'status': 'ok', 'reason': 'x' * A.MAX_ACK_CHARS}))
    with pytest.raises(ValueError):
        A.parse_action('x' * (A.MAX_MESSAGE_CHARS + 1))
