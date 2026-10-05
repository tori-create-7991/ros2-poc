import json

import pytest

from ros2_poc_sim import motion_judge as M
from ros2_poc_sim import scenario as S
from ros2_poc_sim import scenario_cli as CLI

J = S.ARM_JOINTS
HOME = [0.0] * 7
POSE_A = [0.5, 0.3, 0.0, -1.2, 0.0, -0.5, 0.0]


class Clock:
    """time.time / time.sleep の差し替え。sleep で時刻が進み、そのたびに記録を書き足せる。"""

    def __init__(self, t, on_sleep=None):
        self.t = t
        self.on_sleep = on_sleep
        self.slept = 0.0

    def now(self):
        return self.t

    def sleep(self, dt):
        self.t += dt
        self.slept += dt
        if self.on_sleep:
            self.on_sleep(self.t)


def _run_dir(tmp_path, duration=2.0):
    steps = S.parse_scenario({'steps': [{'positions': POSE_A, 'time_from_start': duration}]})
    (tmp_path / 'steps.json').write_text(json.dumps([s.to_dict() for s in steps]))
    (tmp_path / 'events.jsonl').write_text(json.dumps({'index': 0, 't_start': 9.0, 't_sent': 10.0, 'rc': 0}) + '\n')
    return tmp_path


def _writer(tmp_path, stop_at, frame_every=0.5):
    """時刻 t までの /joint_states（stop_at で POSE_A に到達）とカメラフレームを書く。"""
    def write(t):
        js, fr, k = [], ['n,t'], 0
        x = 9.0
        while x <= t:
            a = min(max((x - 10.0) / (stop_at - 10.0), 0.0), 1.0)
            js.append(json.dumps({'t': x, 'name': J, 'position': [h + (p - h) * a for h, p in zip(HOME, POSE_A)]}))
            x += 0.1
        y = 8.0
        while y <= t:
            fr.append(f'{k},{y}')
            k += 1
            y += frame_every
        (tmp_path / 'joints.jsonl').write_text('\n'.join(js) + '\n')
        (tmp_path / 'camera_frames.csv').write_text('\n'.join(fr) + '\n')
    return write


def _args(d):
    return type('A', (), {'run_dir': str(d), 'index': 0})()


def test_wait_returns_after_settle_and_judge_frames(tmp_path):
    d = _run_dir(tmp_path)
    w = _writer(d, stop_at=14.0)
    clock = Clock(10.0, w)
    w(10.0)
    assert CLI.cmd_wait(_args(d), now=clock.now, sleep=clock.sleep) == 0
    t_end = 14.0 + M.SETTLE_SEC
    # 判定時刻以後のフレームと、その SETTLE_WINDOW 以上あとのフレームが揃うまで待った
    assert clock.t >= t_end + M.SETTLE_WINDOW_SEC
    assert clock.t < t_end + M.SETTLE_WINDOW_SEC + 2.0


def test_wait_gives_up_at_deadline_when_never_still(tmp_path):
    d = _run_dir(tmp_path)
    w = _writer(d, stop_at=40.0)   # 上限（30 秒）を過ぎても動き続ける
    clock = Clock(10.0, w)
    w(10.0)
    assert CLI.cmd_wait(_args(d), now=clock.now, sleep=clock.sleep) == 0
    deadline = M.settle_deadline(10.0, 2.0)
    assert deadline + M.STILL_WINDOW <= clock.t < deadline + M.STILL_WINDOW + 3.0


def test_wait_times_out_when_frames_stop(tmp_path):
    d = _run_dir(tmp_path)
    w = _writer(d, stop_at=12.0)
    w(13.0)
    frames = (d / 'camera_frames.csv').read_text()   # この後フレームは増えない

    def on_sleep(t):
        w(t)
        (d / 'camera_frames.csv').write_text(frames)

    clock = Clock(10.0, on_sleep)
    assert CLI.cmd_wait(_args(d), now=clock.now, sleep=clock.sleep) == 0
    t_end = 12.0 + M.SETTLE_SEC
    assert clock.t > t_end + M.SETTLE_WINDOW_SEC + 2 * M.FRAME_MAX_GAP_SEC - 0.5


def test_wait_without_event_is_environment_error(tmp_path):
    d = _run_dir(tmp_path)
    (d / 'events.jsonl').write_text('')
    assert CLI.cmd_wait(_args(d), now=lambda: 0.0, sleep=lambda dt: None) == 2


def test_commands_outputs_logical_targets_and_rejects_bad_yaml(tmp_path, capsys):
    good = tmp_path / 'g.yaml'
    good.write_text('steps:\n  - {name: a, positions: [0, 0, 0, 0, 0, 0, 0]}\n  - {name: g, gripper: open}\n')
    assert CLI.main(['commands', str(good), '--steps-out', str(tmp_path / 's.json')]) == 0
    rows = [line.split('\t') for line in capsys.readouterr().out.splitlines()]
    assert [r[1] for r in rows] == [S.LAB, S.SIM]
    bad = tmp_path / 'b.yaml'
    bad.write_text('steps: [\n')
    assert CLI.main(['commands', str(bad)]) == 64


def test_unexpected_error_is_exit_2_not_fail(tmp_path, capsys):
    assert CLI.main(['judge', str(tmp_path)]) == 2   # steps.json が無い
    assert 'judge が失敗した' in capsys.readouterr().err


def test_budget_covers_every_step_deadline(tmp_path, capsys):
    d = _run_dir(tmp_path, duration=3.0)
    assert CLI.main(['budget', str(d)]) == 0
    assert int(capsys.readouterr().out) >= M.settle_deadline(0.0, 3.0) + CLI.BUDGET_MARGIN_SEC


def test_doctor_reports_missing_font(tmp_path, capsys):
    assert CLI.main(['doctor', '--font', str(tmp_path / 'none.ttf')]) == 2
    assert 'フォントが無い' in capsys.readouterr().err


@pytest.mark.parametrize('k, w, h, size, ok', [
    ([400, 0, 320, 0, 400, 240, 0, 0, 1], 640, 480, (640, 480), True),
    ([400, 0, 320, 0, 400, 240, 0, 0, 1], 640, 480, (320, 240), False),   # 画像と大きさが違う
    ([400, 0, 320, 0, 400, 240, 0, 0, 1], 640, 0, None, False),
    ([float('nan'), 0, 320, 0, 400, 240, 0, 0, 1], 640, 480, None, False),
    ([400, 0, 320], 640, 480, None, False),
    ([400, 0, 320, 0, 400, 240, 0, 0, 1], 4000, 4000, None, False),      # 上限超え
])
def test_valid_camera_info(k, w, h, size, ok):
    obs = pytest.importorskip('ros2_poc_sim.scenario_observer')
    assert obs.valid_camera_info(k, w, h, size) is ok
