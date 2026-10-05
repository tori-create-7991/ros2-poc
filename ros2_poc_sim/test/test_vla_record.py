import json

import pytest

from ros2_poc_sim import motion_judge as M
from ros2_poc_sim import scenario as S
from ros2_poc_sim import scenario_cli as CLI
from ros2_poc_sim import vla_record as R

Q = [0.0, 0.1, 0.0, -1.0, 0.0, -0.5, 0.0]


def _detail(**over):
    d = {'target_joints': Q, 'duration': 1.5, 't_sent': 101.0, 'ee_before': [0.1, 0.0, 0.40],
         'ee_cmd': [0.1, 0.0, 0.38], 'ee_after': [0.1, 0.0, 0.381], 'ee_error': 0.001, 'graph': None,
         'clamped': False}
    d.update(over)
    return d


def _rec(index, status='ok', detail=None, **over):
    r = {'index': index, 'seq': 10 + index, 'instruction': 'move down', 'vector': [0, 0, -0.02, 0, 0, 0, 1.0],
         'status': status, 'reason': '', 'detail': _detail() if detail is None else detail,
         't_start': 100.0 + index * 10}
    r.update(over)
    return r


def _write(tmp_path, recs):
    p = tmp_path / 'vla_steps.jsonl'
    p.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in recs), encoding='utf-8')
    return p


def test_prepare_builds_steps_events_and_narration(tmp_path):
    out = R.prepare(_write(tmp_path, [_rec(0), _rec(1)]))
    s0, s1 = out['steps']
    assert s0['name'] == 'vla_001' and s0['kind'] == 'arm' and s0['joints'] == S.ARM_JOINTS
    assert s0['points'] == [{'positions': Q, 'time_from_start': 1.5}] and s0['expect'] == Q
    assert s0['tolerance'] == S.ARM_TOLERANCE and s0['duration'] == 1.5
    assert s0['ee_error'] == 0.001 and s0['ee_tolerance'] == R.EE_TOLERANCE == 0.01
    assert S.Step.from_dict(s0).name == 'vla_001'                   # compose が読める形
    assert out['events'][1] == {'index': 1, 'name': 'vla_002', 'target': 'sim', 't_start': 110.0,
                                't_sent': 101.0, 'rc': 0}
    n = out['narration']
    assert n['title'] == 'VLA への指示: 「move down」（2 ステップ）'
    assert n['steps'][0]['now'] == 'VLA の出力で手先を動かす: 下へ 2.0cm'
    assert n['steps'][0]['next'] == n['steps'][1]['now'] and n['steps'][1]['next'] == '終了（判定を表示します）'
    assert n['steps'][0]['command'] == 'VLA "move down" -> [0.0000 0.0000 -0.0200 0.0000 0.0000 0.0000 1.0000]'


def test_failed_steps_are_not_sent_and_timeout_is_judged(tmp_path):
    out = R.prepare(_write(tmp_path, [
        _rec(0, 'timeout', _detail(ee_after=None, ee_error=None)),
        _rec(1, 'ik_failed', {'ee_before': [0.1, 0, 0.4], 'ee_cmd': [0.1, 0, 0.3], 'clamped': True},
             reason='IK の解がない')]))
    assert [e['rc'] for e in out['events']] == [0, 1]
    assert out['events'][1]['t_sent'] == out['events'][1]['t_start']       # 送っていないので開始時刻で埋める
    s1 = out['steps'][1]
    assert s1['points'][0]['positions'] == R.FALLBACK_JOINTS and s1['ee_error'] is None
    assert 'ik_failed: IK の解がない' in out['narration']['steps'][1]['now']
    assert '上限にクランプ' in out['narration']['steps'][1]['now']


def test_move_text():
    assert R.move_text((0, 0, 0), (0.0, 0.0, -0.02)) == '下へ 2.0cm'
    assert R.move_text((0, 0, 0), (0.03, -0.01, 0.0)) == '前へ 3.0cm、右へ 1.0cm'
    assert R.move_text((0, 0, 0), (0.0001, 0, 0)) == 'ほぼ動かさない'


def test_gripper_change_is_narrated(tmp_path):
    out = R.prepare(_write(tmp_path, [_rec(0, detail=_detail(graph='open'))]))
    assert out['narration']['steps'][0]['now'].endswith('グリッパを開く')


@pytest.mark.parametrize('mut', [
    lambda r: r.update(index=3), lambda r: r.update(status='bogus'), lambda r: r.update(vector=[0] * 6),
    lambda r: r.update(vector=[float('nan')] * 7), lambda r: r.update(t_start='x'),
    lambda r: r.update(detail={'ee_error': -1}), lambda r: r.update(seq=True),
])
def test_bad_records_are_rejected(tmp_path, mut):
    r = _rec(0)
    mut(r)
    with pytest.raises(ValueError):
        R.read_records(_write(tmp_path, [r]))


def test_empty_garbage_and_too_many_records_are_rejected(tmp_path):
    p = tmp_path / 'vla_steps.jsonl'
    p.write_text('')
    with pytest.raises(ValueError, match='1 つも'):
        R.read_records(p)
    p.write_text('{"index": 0}\nnot json\n')
    with pytest.raises(ValueError):
        R.read_records(p)
    with pytest.raises(ValueError, match='多すぎる'):
        R.read_records(_write(tmp_path, [_rec(i) for i in range(R.MAX_RECORDS + 1)]))


def test_instruction_with_control_characters_is_cleaned(tmp_path):
    out = R.prepare(_write(tmp_path, [_rec(0, instruction='move\ndown')]))
    assert 'move down' in out['narration']['title']


def test_record_path_validation(tmp_path):
    ok = str(tmp_path / 'vla_steps.jsonl')
    assert R.validate_record_path(ok) == ok
    for bad in ['rel/x.jsonl', '/a/../x.jsonl', ok[:-6] + '.txt', '/no/such/dir/x.jsonl', '/a b/x.jsonl',
                ok + ';id', '', None]:
        with pytest.raises(ValueError):
            R.validate_record_path(bad)


def test_make_and_append_record_roundtrip(tmp_path):
    p = tmp_path / 'vla_steps.jsonl'
    info = {'index': 0, 'seq': 5, 'vector': [0.0] * 7, 'action': object(), 'status': 'ok', 'reason': '',
            'detail': _detail(), 't_start': 100.0}
    R.append_record(str(p), R.make_record(info, 'move down'))
    R.append_record(str(p), R.make_record({**info, 'index': 1, 'seq': 6}, 'move down'))
    assert [r['index'] for r in R.read_records(p)] == [0, 1]


def test_vla_prepare_then_judge_flags_end_effector_error(tmp_path, capsys):
    """vla-prepare の出力を motion_judge がそのまま読める（手先の誤差が許容を超えたら FAIL になる）。"""
    d = tmp_path
    _write(d, [_rec(0, detail=_detail(ee_error=0.02))])
    assert CLI.main(['vla-prepare', str(d)]) == 0
    assert 'VLA ステップ 1 個' in capsys.readouterr().out
    steps = json.loads((d / 'steps.json').read_text(encoding='utf-8'))
    assert steps[0]['ee_error'] == 0.02
    assert len(M.read_jsonl(d / 'events.jsonl')) == 1
    assert json.loads((d / 'narration.json').read_text(encoding='utf-8'))['steps'][0]['now']


def test_vla_prepare_returns_2_without_records(tmp_path, capsys):
    assert CLI.main(['vla-prepare', str(tmp_path)]) == 2
    assert 'vla_steps.jsonl' in capsys.readouterr().err
