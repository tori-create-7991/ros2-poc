import math

import pytest

from ros2_poc_sim import narration as N
from ros2_poc_sim import scenario as S

J = S.ARM_JOINTS
HOME = [0.0] * 7


def _steps(yaml_dict):
    return S.parse_scenario(yaml_dict)


def test_arm_gripper_waypoint_and_random_steps_are_described_in_japanese():
    steps = _steps({'steps': [
        {'name': 'pose_a', 'positions': HOME, 'time_from_start': 3},
        {'name': 'wave', 'waypoints': [{'positions': HOME, 'time_from_start': 2},
                                       {'positions': HOME, 'time_from_start': 4}]},
        {'name': 'g1', 'gripper': 'open'}, {'name': 'g2', 'gripper': 'close'}, {'name': 'g3', 'gripper': 0.5},
        {'name': 'rnd', 'random': {'n': 1, 'seed': 1}}]})
    texts = [N.describe_step(s) for s in steps]
    assert texts[0] == '姿勢「pose_a」へ動かす（7 関節、3 秒）'
    assert texts[1] == '経由 2 点を順に通る「wave」（4 秒）'
    assert texts[2:5] == ['グリッパを開く', 'グリッパを閉じる', f'グリッパを {math.degrees(0.5):.0f} 度にする']
    assert texts[5].startswith('ランダムな姿勢「rnd_1」へ動かす')


def test_description_overrides_and_is_cleaned():
    s = _steps({'steps': [{'name': 'a', 'positions': HOME, 'description': '姿勢 A へ動かす'}]})[0]
    assert N.describe_step(s) == '姿勢 A へ動かす'
    s.description = 'x' * 200 + '\n'
    assert len(N.describe_step(s)) == N.MAX_LINE and N.describe_step(s).endswith('…')


def test_narrate_links_next_and_ends_with_the_end_text():
    steps = _steps({'steps': [{'name': 'a', 'gripper': 'open'}, {'name': 'b', 'gripper': 'close'}]})
    n = N.narrate(steps, 'タイトル', lambda s: f'cmd {s.name}')
    assert n['title'] == 'タイトル'
    assert [x['now'] for x in n['steps']] == ['グリッパを開く', 'グリッパを閉じる']
    assert [x['next'] for x in n['steps']] == ['グリッパを閉じる', N.END_TEXT]
    assert [x['command'] for x in n['steps']] == ['cmd a', 'cmd b']


def test_every_judge_code_has_a_japanese_text():
    codes = ['no_joint_states', 'joint_err 0.500>0.100', 'joint_missing', 'no_frame', 'no_motion 0.10%',
             'unexpected_motion 3.00%', 'no_ee_projection', 'ee_outside_change', 'no_settle_frame',
             'not_settled 1.20%', 'send_failed rc=1', 'joints_not_still', 'ee_delta_mismatch 12.3mm>10.0mm',
             'ee_delta_unknown']
    for c in codes:
        t = N.code_text(c)
        assert t != c and not t.isascii(), c
    assert N.code_text('joint_err 0.500>0.100') == '関節の誤差が大きい（0.500 rad > 許容 0.100 rad）'
    assert N.code_text('ee_delta_mismatch 12.3mm>10.0mm') == '手先が指令の位置から外れた（誤差 12.3mm > 許容 10.0mm）'
    assert N.code_text('something_new x') == 'something_new x'


def test_verdict_text():
    ok, t = N.verdict_text({'verdict': 'PASS', 'joint_err': 0.0012, 'changed_ratio': 0.0213, 'ee_error': 0.0042})
    assert ok and t == '合格 — 関節の誤差 0.001 rad・映像の変化 2.1%・手先の誤差 4.2 mm'
    ok, t = N.verdict_text({'verdict': 'FAIL', 'codes': ['no_motion 0.10%', 'not_settled 1.20%']})
    assert not ok and t == '不合格 — 映像に動きが無い（変化 0.10%）、判定の時点でまだ動いていた（変化 1.20%）'
    ok, t = N.verdict_text({'verdict': 'FAIL'})
    assert not ok and t.startswith('不合格 — ')
    assert N.verdict_text({'verdict': 'PASS'}) == (True, '合格')


def test_clean_strips_control_characters():
    assert N.clean('a\nb\x00c d') == 'a b c d'
    with pytest.raises(ValueError):
        S.parse_scenario({'steps': [{'positions': HOME, 'description': 'a\nb'}]})
