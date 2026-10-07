import json

import numpy as np
import pytest

from ros2_poc_sim import motion_judge as M
from ros2_poc_sim import scenario as S

K = [400.0, 0, 320.0, 0, 400.0, 240.0, 0, 0, 1]
J = S.ARM_JOINTS
HOME = [0.0] * 7
POSE_A = [0.5, 0.3, 0.0, -1.2, 0.0, -0.5, 0.0]


def _bg():
    return np.full((480, 640, 3), 100, np.uint8)


def _with_box(x0, y0, x1, y1):
    img = _bg()
    img[y0:y1, x0:x1] = 220
    return img


def _ok_inputs(**over):
    kw = dict(joints=J, expected=POSE_A, tolerance=0.05,
              actual_before=dict(zip(J, HOME)), actual_after=dict(zip(J, POSE_A)),
              frame_before=_bg(), frame_after=_with_box(300, 200, 360, 280),
              frame_settled=_with_box(300, 200, 360, 280),
              ee_xyz=[0.0, 0.0, 1.0], K=K, motion_min=M.MOTION_MIN)
    kw.update(over)
    return kw


def test_diff_mask_ratio_and_bbox():
    m = M.diff_mask(_bg(), _with_box(10, 20, 30, 60))
    assert m.sum() == 20 * 40
    assert M.changed_ratio(m) == pytest.approx(800 / (480 * 640))
    assert M.bbox(m, margin=5) == (5, 15, 34, 64)
    assert M.bbox(m, margin=100) == (0, 0, 129, 159)
    assert M.bbox(np.zeros((4, 4), bool)) is None


def test_diff_mask_ignores_small_noise_and_rejects_shape_mismatch():
    noisy = _bg() + np.uint8(M.DIFF_THRESH)   # ちょうどしきい値は変化とみなさない
    assert not M.diff_mask(_bg(), noisy).any()
    with pytest.raises(ValueError):
        M.diff_mask(_bg(), np.zeros((10, 10, 3), np.uint8))


def test_project():
    assert M.project([0.1, -0.05, 1.0], K) == pytest.approx((360.0, 220.0))
    assert M.project([0.0, 0.0, -1.0], K) is None


def test_joint_error_max_abs_and_missing():
    assert M.joint_error(dict(zip(J, POSE_A)), J, HOME) == pytest.approx(1.2)
    with pytest.raises(ValueError):
        M.joint_error({}, J, HOME)


def test_judge_pass_when_all_conditions_hold():
    r = M.judge_step(**_ok_inputs())
    assert r['verdict'] == 'PASS', r['reasons']
    assert r['ee_px'] == pytest.approx((320.0, 240.0)) and r['expect_motion'] is True


@pytest.mark.parametrize('over, needle', [
    ({'actual_after': dict(zip(J, HOME))}, '関節誤差'),
    ({'frame_after': _bg(), 'frame_settled': _bg()}, '映像に動きが無い'),
    ({'ee_xyz': [0.5, 0.5, 1.0]}, '変化領域'),
    ({'ee_xyz': None}, '投影できない'),
    ({'frame_settled': _with_box(100, 100, 200, 200)}, 'まだ動いている'),
    ({'actual_after': None}, '/joint_states が無い'),
    ({'frame_before': None}, 'フレームが無い'),
])
def test_each_condition_fails_alone(over, needle):
    r = M.judge_step(**_ok_inputs(**over))
    assert r['verdict'] == 'FAIL'
    assert any(needle in x for x in r['reasons']), r['reasons']
    assert len(r['codes']) == len(r['reasons']) and all(c.isascii() for c in r['codes'])


def test_no_motion_expected_inverts_visual_check():
    still = dict(expected=HOME, actual_before=dict(zip(J, HOME)), actual_after=dict(zip(J, HOME)),
                 frame_after=_bg(), frame_settled=_bg(), ee_xyz=None)
    assert M.judge_step(**_ok_inputs(**still))['verdict'] == 'PASS'
    moved = M.judge_step(**_ok_inputs(**{**still, 'frame_after': _with_box(0, 0, 200, 200),
                                         'frame_settled': _with_box(0, 0, 200, 200)}))
    assert moved['verdict'] == 'FAIL' and '動かないはず' in moved['reasons'][0]


def test_gripper_uses_lower_motion_threshold():
    g = S.GRIPPER_JOINT
    kw = _ok_inputs(joints=[g], expected=[0.0], tolerance=0.1,
                    actual_before={g: 1.0}, actual_after={g: 0.02},
                    frame_after=_with_box(310, 230, 330, 250), frame_settled=_with_box(310, 230, 330, 250))
    assert M.judge_step(**kw)['verdict'] == 'FAIL'   # アームのしきい値では小さすぎる
    assert M.judge_step(**{**kw, 'motion_min': M.GRIPPER_MOTION_MIN})['verdict'] == 'PASS'


def test_series_nearest_and_before_respect_gap():
    s = M.Series([{'t': 1.0}, {'t': 2.0}, {'t': 3.0}])
    assert s.nearest(2.2)['t'] == 2.0 and s.nearest(2.6)['t'] == 3.0
    assert s.nearest(10.0) is None and s.before(2.4, max_gap=0.5)['t'] == 2.0
    assert s.before(2.9, max_gap=0.5) is None and s.before(0.5) is None
    assert M.Series([]).nearest(1.0) is None


def test_read_jsonl_skips_truncated_last_line(tmp_path):
    p = tmp_path / 'x.jsonl'
    p.write_text('{"t": 1}\n{"t": 2}\n{"t": 3', encoding='utf-8')
    assert [r['t'] for r in M.read_jsonl(p)] == [1, 2]
    assert M.read_jsonl(tmp_path / 'none.jsonl') == []


def test_frame_at_decodes_rawvideo_and_rejects_short_output():
    calls = []

    class R:
        def __init__(self, out):
            self.stdout = out

    def run(cmd, **kw):
        calls.append(cmd)
        return R(bytes(range(12)))

    f = M.frame_at('v.mp4', 7, 2, 2, run=run)
    assert f.shape == (2, 2, 3) and f[1, 1, 2] == 11
    assert 'select=eq(n\\,7)' in calls[0]
    assert M.frame_at('v.mp4', 7, 4, 4, run=run) is None


def _write_run(tmp_path, steps, events, joints, frames, ee, info=True):
    (tmp_path / 'steps.json').write_text(json.dumps([s.to_dict() for s in steps]))
    (tmp_path / 'events.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in events))
    (tmp_path / 'joints.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in joints))
    (tmp_path / 'ee.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in ee))
    (tmp_path / 'camera_frames.csv').write_text('n,t\n' + ''.join(f'{n},{t}\n' for n, t in frames))
    if info:
        (tmp_path / 'camera_info.json').write_text(json.dumps({'k': K, 'width': 640, 'height': 480}))


def test_judge_run_end_to_end_with_fake_video(tmp_path):
    steps = S.parse_scenario({'steps': [{'name': 'a', 'positions': POSE_A, 'time_from_start': 2},
                                        {'name': 'b', 'positions': HOME, 'time_from_start': 2}]})
    # a: t_start=10, t_sent=11 → t_end=14。b は命令失敗（rc=1）
    events = [{'index': 0, 't_start': 10.0, 't_sent': 11.0, 'rc': 0},
              {'index': 1, 't_start': 20.0, 't_sent': 21.0, 'rc': 1}]
    joints = _traj(11.0, 12.0, HOME, POSE_A, until=16.0, dt=0.1)
    frames = [(n, 9.0 + n * 0.2) for n in range(40)]
    ee = [{'t': t, 'xyz': [0.0, 0.0, 1.0]} for t in np.arange(9, 16, 0.1)]
    _write_run(tmp_path, steps, events, joints, frames, ee)
    moved = _with_box(300, 200, 360, 280).tobytes()

    class R:
        def __init__(self, out):
            self.stdout = out

    def run(cmd, **kw):
        n = int(cmd[cmd.index('-vf') + 1].split('\\,')[1].rstrip(')'))
        return R(moved if 9.0 + n * 0.2 >= 12 else _bg().tobytes())

    res = M.judge_run(tmp_path, run=run)
    assert res['total'] == 2 and res['passed'] == 1 and res['verdict'] == 'FAIL'
    a, b = res['steps']
    assert a['verdict'] == 'PASS', a['reasons']
    assert a['t_end'] == pytest.approx(12.0 + M.SETTLE_SEC)   # 期待値に届いて静止した時刻 + SETTLE
    assert b['verdict'] == 'FAIL' and 'rc=1' in b['reasons'][0]
    perf = res['performance']   # clock.csv が無い run でも判定は動き、RTF だけが null になる
    assert perf['rtf'] is None and perf['wall_sec'] == pytest.approx(a['t_end'] - 9.0)
    assert perf['camera_fps'] == pytest.approx(5.0) and [x['index'] for x in perf['steps']] == [0]


def test_judge_run_reports_rtf_from_clock_csv(tmp_path):
    steps = S.parse_scenario({'steps': [{'positions': POSE_A, 'time_from_start': 1}]})
    _write_run(tmp_path, steps, [{'index': 0, 't_start': 1.0, 't_sent': 1.5, 'rc': 0}],
               _traj(1.5, 2.5, HOME, POSE_A, until=6.0), [], [], info=False)
    (tmp_path / 'clock.csv').write_text('wall,sim\n0,0\n10,5\n', encoding='utf-8')
    assert M.judge_run(tmp_path, run=None)['performance']['rtf'] == pytest.approx(0.5)


def test_judge_run_without_camera_info_fails_visual_conditions(tmp_path):
    steps = S.parse_scenario({'steps': [{'positions': POSE_A, 'time_from_start': 1}]})
    _write_run(tmp_path, steps, [{'index': 0, 't_start': 1.0, 't_sent': 1.5, 'rc': 0}],
               _traj(1.5, 2.5, HOME, POSE_A, until=6.0), [], [], info=False)
    (r,) = M.judge_run(tmp_path, run=None)['steps']
    assert r['verdict'] == 'FAIL' and any('フレームが無い' in x for x in r['reasons'])


def _traj(t0, t1, p0, p1, until, dt=0.25):
    """t0〜t1 で p0 → p1 に線形に動き、until まで止まっている /joint_states 記録。"""
    out, t = [], t0 - 1.0
    while t <= until:
        a = min(max((t - t0) / (t1 - t0), 0.0), 1.0)
        out.append({'t': t, 'name': J, 'position': [x + (y - x) * a for x, y in zip(p0, p1)]})
        t += dt
    return out


def test_settle_time_waits_for_slow_motion_to_stop():
    # 3 秒の指令が RTF 0.4 で 7.5 秒かかる
    recs = _traj(10.0, 17.5, HOME, POSE_A, until=25.0)
    t = M.settle_time(recs, J, POSE_A, 0.05, t_sent=10.0, duration=3.0)
    assert 17.0 <= t <= 17.75


def test_settle_time_ignores_stillness_before_motion_starts():
    recs = _traj(12.0, 15.0, HOME, POSE_A, until=20.0)   # 送信 10.0、動き出しが 2 秒遅れる
    t = M.settle_time(recs, J, POSE_A, 0.05, t_sent=10.0, duration=3.0)
    assert t >= 14.5


def test_settle_time_none_while_moving_or_without_window():
    recs = _traj(10.0, 30.0, HOME, POSE_A, until=20.0)
    assert M.settle_time(recs, J, POSE_A, 0.05, t_sent=10.0, duration=3.0) is None


def test_settle_time_for_wrong_target_after_duration():
    recs = _traj(10.0, 13.0, HOME, POSE_A, until=20.0)   # 期待は HOME のまま（fail_demo）
    t = M.settle_time(recs, J, HOME, 0.05, t_sent=10.0, duration=3.0)
    assert 13.0 <= t <= 13.5


def test_judge_run_fails_when_joints_never_settle(tmp_path):
    steps = S.parse_scenario({'steps': [{'positions': POSE_A, 'time_from_start': 1}]})
    joints = _traj(1.0, 100.0, HOME, POSE_A, until=30.0)
    _write_run(tmp_path, steps, [{'index': 0, 't_start': 0.5, 't_sent': 1.0, 'rc': 0}],
               joints, [], [], info=False)
    (r,) = M.judge_run(tmp_path, run=None)['steps']
    assert r['verdict'] == 'FAIL' and 'joints_not_still' in r['codes']
    assert r['t_end'] == pytest.approx(M.settle_deadline(1.0, 1.0))


def test_settle_time_skips_gaps_in_records():
    recs = [r for r in _traj(10.0, 20.0, HOME, POSE_A, until=26.0) if not 14.0 < r['t'] < 15.2]
    t = M.settle_time(recs, J, POSE_A, 0.05, t_sent=10.0, duration=3.0)
    assert t is not None and 19.5 <= t <= 20.25


def test_judge_frames_uses_frames_after_judge_time_even_when_sparse():
    frames = M.Series([{'n': i, 't': t} for i, t in enumerate([1.0, 4.0, 9.5, 16.0, 17.0, 30.0])])
    before, after, settled = M.judge_frames(frames, t_start=5.0, t_end=10.0)
    assert (before['n'], after['n'], settled['n']) == (1, 3, 4)   # 9.5 は判定時刻より前なので使わない
    assert M.judge_frames(frames, t_start=5.0, t_end=17.5)[2] is None   # 17.5 以後は 30.0 だけ（間隔超過）
    assert M.judge_frames(M.Series([]), 1.0, 2.0) == (None, None, None)


def test_settle_time_ignores_stillness_after_deadline():
    # 上限（1 + 1*5 + 10 = 16）を過ぎて 40 秒に止まっても、次のステップの区間なので静止とみなさない
    recs = _traj(1.0, 40.0, HOME, POSE_A, until=50.0)
    assert M.settle_time(recs, J, POSE_A, 0.05, t_sent=1.0, duration=1.0) is None
    t_end, settled = M.judge_time(recs, {'joints': J, 'expect': POSE_A, 'tolerance': 0.05, 'duration': 1.0},
                                  {'t_sent': 1.0})
    assert not settled and t_end == pytest.approx(M.settle_deadline(1.0, 1.0))


def test_judge_time_undecided_while_waiting_then_decided():
    st = {'joints': J, 'expect': POSE_A, 'tolerance': 0.05, 'duration': 3.0}
    moving = _traj(10.0, 30.0, HOME, POSE_A, until=14.0)
    assert M.judge_time(moving, st, {'t_sent': 10.0}, now=14.0) is None           # 上限前: まだ決めない
    assert M.judge_time(moving, st, {'t_sent': 10.0}, now=100.0)[1] is False       # 上限後: 止まらず
    done = _traj(10.0, 12.0, HOME, POSE_A, until=16.0)
    t_end, settled = M.judge_time(done, st, {'t_sent': 10.0}, now=16.0)
    assert settled and t_end == pytest.approx(12.0 + M.SETTLE_SEC, abs=0.3)   # 12 秒で届いて静止


def test_settle_time_holds_while_window_still_open():
    recs = _traj(10.0, 12.0, HOME, POSE_A, until=12.6)   # 静止区間が 0.6 秒ぶんしかない
    assert M.settle_time(recs, J, POSE_A, 0.05, 10.0, 3.0, now=12.6) is None   # 実行中は区間が終わるまで保留


def test_ee_error_is_judged_only_when_a_tolerance_is_given():
    plain = M.judge_step(**_ok_inputs())
    assert plain['verdict'] == 'PASS' and 'ee_error' not in plain          # シナリオの挙動は変えない
    ok = M.judge_step(**_ok_inputs(ee_error=0.004, ee_tol=0.01))
    assert ok['verdict'] == 'PASS' and ok['ee_error'] == 0.004
    bad = M.judge_step(**_ok_inputs(ee_error=0.0123, ee_tol=0.01))
    assert bad['verdict'] == 'FAIL' and bad['codes'] == ['ee_delta_mismatch 12.3mm>10.0mm']
    assert '12.3 mm' in bad['reasons'][0]
    for missing in (None, float('nan')):
        r = M.judge_step(**_ok_inputs(ee_error=missing, ee_tol=0.01))
        assert r['verdict'] == 'FAIL' and r['codes'] == ['ee_delta_unknown']
    assert M.judge_step(**_ok_inputs(ee_error=0.5))['verdict'] == 'PASS'   # 許容を渡さなければ見ない


def test_judge_run_passes_ee_fields_from_steps_json(tmp_path):
    steps = S.parse_scenario({'steps': [{'name': 'a', 'positions': POSE_A, 'time_from_start': 2}]})
    events = [{'index': 0, 't_start': 10.0, 't_sent': 11.0, 'rc': 0}]
    _write_run(tmp_path, steps, events, _traj(11.0, 12.0, HOME, POSE_A, until=16.0, dt=0.1),
               [(n, 9.0 + n * 0.2) for n in range(40)],
               [{'t': t, 'xyz': [0.0, 0.0, 1.0]} for t in np.arange(9, 16, 0.1)])
    d = json.loads((tmp_path / 'steps.json').read_text())
    d[0].update(ee_error=0.02, ee_tolerance=0.01)
    (tmp_path / 'steps.json').write_text(json.dumps(d))
    moved = _with_box(300, 200, 360, 280).tobytes()

    class R:
        def __init__(self, out):
            self.stdout = out

    def run(cmd, **kw):
        n = int(cmd[cmd.index('-vf') + 1].split('\\,')[1].rstrip(')'))
        return R(moved if 9.0 + n * 0.2 >= 12 else _bg().tobytes())

    (r,) = M.judge_run(tmp_path, run=run)['steps']
    assert r['verdict'] == 'FAIL' and r['codes'] == ['ee_delta_mismatch 20.0mm>10.0mm']
