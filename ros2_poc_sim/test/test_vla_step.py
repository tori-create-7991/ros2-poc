import math

import pytest

from ros2_poc_sim import motion_judge as M
from ros2_poc_sim import scenario as S
from ros2_poc_sim import vla_action as A
from ros2_poc_sim import vla_step as T

HOME = [0.0, 0.0, 0.0, -1.0, 0.0, -0.5, 0.0]
ID_QUAT = (0.0, 0.0, 0.0, 1.0)


class FakeIO:
    """T.StepRunner が使う外部との口。呼ばれた引数を記録し、返り値を差し替えられる。"""

    def __init__(self):
        self.pose = ((0.1, 0.0, 0.4), ID_QUAT)
        self.joints = dict(zip(S.ARM_JOINTS, HOME))
        self.ik = list(HOME)
        self.gripper_ok = True
        self.settled = True
        self.calls = []

    def current_pose(self):
        return self.pose

    def current_joints(self):
        return self.joints

    def solve_ik(self, pos, quat, seed):
        self.calls.append(('ik', pos, quat))
        return self.ik

    def send_arm(self, positions, duration):
        self.calls.append(('arm', list(positions), duration))

    def send_gripper(self, angle):
        self.calls.append(('gripper', angle))
        return self.gripper_ok

    def wait_settled(self, expected, duration, t_sent):
        self.calls.append(('wait', list(expected), duration))
        return self.settled


def _run(io, delta=(0.01, 0, 0, 0, 0, 0), gripper=1.0, seq=1, **kw):
    runner = kw.pop('runner', None) or T.StepRunner(io, clock=lambda: 100.0, **kw)
    return runner, runner.run(A.Action(seq, tuple(delta), gripper))


def test_ok_path_sends_ik_result_and_waits():
    io = FakeIO()
    io.ik = [0.0, 0.1, 0.0, -1.0, 0.0, -0.5, 0.0]
    _, (status, reason) = _run(io)
    assert status == 'ok'
    kinds = [c[0] for c in io.calls]
    assert kinds == ['ik', 'arm', 'gripper', 'wait']
    ik_pos = io.calls[0][1]
    assert ik_pos == pytest.approx((0.11, 0.0, 0.4))
    arm = io.calls[1]
    assert arm[1] == io.ik and arm[2] == pytest.approx(max(T.MIN_DURATION, 0.1 / T.MAX_JOINT_SPEED))
    assert io.calls[3][1] == io.ik


def test_delta_and_workspace_are_clamped():
    io = FakeIO()
    io.pose = ((0.49, 0.0, 0.69), ID_QUAT)
    _, (status, reason) = _run(io, delta=(3.0, 0, 4.0, 0, 0, 0))
    assert status == 'ok' and 'クランプ' in reason
    pos = io.calls[0][1]
    assert math.isclose(pos[0] - 0.49, 0.03 * 0.6, abs_tol=1e-9) or pos[0] == A.WORKSPACE['x'][1]
    assert pos[0] <= A.WORKSPACE['x'][1] and pos[2] <= A.WORKSPACE['z'][1]


@pytest.mark.parametrize('attr, value, expect', [
    ('pose', None, 'TF'),
    ('joints', None, '/joint_states'),
])
def test_missing_state_is_rejected(attr, value, expect):
    io = FakeIO()
    setattr(io, attr, value)
    _, (status, reason) = _run(io)
    assert status == 'rejected' and expect in reason
    assert not any(c[0] in ('arm', 'gripper') for c in io.calls)


def test_ik_failure():
    io = FakeIO()
    io.ik = None
    _, (status, reason) = _run(io)
    assert status == 'ik_failed' and 'IK' in reason
    assert not any(c[0] in ('arm', 'gripper') for c in io.calls)


@pytest.mark.parametrize('bad', [
    [0.0] * 6,
    [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, float('nan')],
    [9.0, 0.0, 0.0, -1.0, 0.0, -0.5, 0.0],         # リミット外
])
def test_ik_result_out_of_contract_is_ik_failed(bad):
    io = FakeIO()
    io.ik = bad
    _, (status, _reason) = _run(io)
    assert status == 'ik_failed'
    assert not any(c[0] == 'arm' for c in io.calls)


def test_large_joint_jump_is_rejected():
    io = FakeIO()
    io.ik = [2.0, 0.0, 0.0, -1.0, 0.0, -0.5, 0.0]    # 現在から 2 rad 飛ぶ（IK が別解へ反転した想定）
    _, (status, reason) = _run(io)
    assert status == 'rejected' and '関節' in reason
    assert not any(c[0] == 'arm' for c in io.calls)


def test_gripper_sent_only_on_change():
    io = FakeIO()
    runner, (status, _) = _run(io, gripper=1.0)
    assert status == 'ok' and ('gripper', S.GRIPPER_OPEN) in io.calls
    io.calls.clear()
    _run(io, gripper=0.9, runner=runner, seq=2)
    assert not any(c[0] == 'gripper' for c in io.calls)
    io.calls.clear()
    _run(io, gripper=0.1, runner=runner, seq=3)
    assert ('gripper', S.GRIPPER_CLOSE) in io.calls


def test_gripper_open_when_high_false_inverts():
    io = FakeIO()
    _run(io, gripper=1.0, open_when_high=False)
    assert ('gripper', S.GRIPPER_CLOSE) in io.calls


def test_gripper_failure_is_rejected_and_not_remembered():
    io = FakeIO()
    io.gripper_ok = False
    runner, (status, reason) = _run(io)
    assert status == 'rejected' and 'gripper' in reason
    io.gripper_ok = True
    io.calls.clear()
    _run(io, runner=runner, seq=2)
    assert ('gripper', S.GRIPPER_OPEN) in io.calls     # 失敗した変更は覚えず、次で再送する


def test_timeout_when_not_settled():
    io = FakeIO()
    io.settled = False
    _, (status, reason) = _run(io)
    assert status == 'timeout' and '静止' in reason


# --- wait_settled（/joint_states の静止待ち。motion_judge.settle_time を再利用）---

def _records(t0, n, joints, step=0.25):
    return [{'t': t0 + i * step, 'name': list(S.ARM_JOINTS), 'position': list(joints)} for i in range(n)]


def test_wait_settled_returns_true_when_still_at_target():
    target = [0.1] * 7
    recs = _records(100.0, 40, target)
    clock = {'now': 100.0}

    def now():
        return clock['now']

    def sleep(dt):
        clock['now'] += dt

    assert T.wait_settled(lambda: recs, target, 2.0, 100.0, now=now, sleep=sleep)


def test_wait_settled_times_out_when_never_reaches_target():
    recs = _records(100.0, 400, [0.0] * 7)
    clock = {'now': 100.0}

    def sleep(dt):
        clock['now'] += dt

    assert not T.wait_settled(lambda: recs, [0.5] * 7, 1.0, 100.0, now=lambda: clock['now'], sleep=sleep,
                              poll_sec=0.5)
    assert clock['now'] <= M.settle_deadline(100.0, 1.0) + M.STILL_WINDOW + 1.0


def test_gripper_failure_reason_says_the_arm_was_already_sent():
    io = FakeIO()
    io.gripper_ok = False
    _, (status, reason) = _run(io)
    assert status == 'rejected' and 'アーム' in reason and any(c[0] == 'arm' for c in io.calls)


def test_wait_settled_is_false_when_a_joint_is_missing_from_the_record():
    partial = [{'t': 100.0 + i * 0.25, 'name': list(S.ARM_JOINTS[:3]), 'position': [0.1] * 3} for i in range(40)]
    clock = {'now': 100.0}
    assert not T.wait_settled(lambda: partial, [0.1] * 7, 1.0, 100.0, now=lambda: clock['now'],
                              sleep=lambda dt: clock.__setitem__('now', clock['now'] + dt))
