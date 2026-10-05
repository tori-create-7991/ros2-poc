"""VLA の 1 ステップ（差分 → 現在姿勢に足す → IK → アームとグリッパへ送る → 静止を待つ）の手順。

ROS 非依存。TF・IK・送信・/joint_states は `io`（vla_converter.py が ROS で実装する）経由で呼ぶので、
差し替えて単体テストできる。io が持つべきメソッド:
  current_pose()                    -> ((x, y, z), (qx, qy, qz, qw)) | None   base_link から見た手先
  current_joints()                  -> {関節名: 角度} | None
  solve_ik(pos, quat, seed_joints)  -> [7 関節の角度 (ARM_JOINTS の順)] | None
  send_arm(positions, duration)     -> None   joint_trajectory を 1 点送る
  send_gripper(angle)               -> bool   gripper_cmd を送り、成功したか
  wait_settled(expected, duration, t_sent) -> bool   目標に届いて静止したか
"""
import math
import time

from ros2_poc_sim import motion_judge as M
from ros2_poc_sim import scenario as S
from ros2_poc_sim import vla_action as A

MIN_DURATION = 1.0        # 1 ステップの指令時間の下限 [s]
MAX_JOINT_SPEED = 0.5     # 指令時間を決める関節速度 [rad/s]
MAX_JOINT_STEP = 0.8      # 1 ステップで許す関節の最大変化 [rad]。超えたら IK が別の解へ飛んだとみなして拒否する


def _within_limits(positions):
    if len(positions) != len(S.ARM_JOINTS) or not all(
            isinstance(v, (int, float)) and math.isfinite(v) for v in positions):
        return False
    return all(S.ARM_LIMITS[j][0] <= v <= S.ARM_LIMITS[j][1] for j, v in zip(S.ARM_JOINTS, positions))


class StepRunner:
    def __init__(self, io, gripper_threshold=A.GRIPPER_THRESHOLD, open_when_high=True, clock=time.time):
        self.io = io
        self.gripper_threshold = gripper_threshold
        self.open_when_high = open_when_high
        self.clock = clock
        self._last_gripper = None     # 最後に送ることに成功した 'open' / 'close'
        # 直近の run() の記録（記録・判定用。vla_action.DETAIL_KEYS のキー。取れたものだけ入る）
        self.last_detail = {}

    def run(self, action):
        """1 ステップ実行して (status, reason) を返す。status は vla_action.STATUSES のどれか。

        実行の記録（目標関節・送信時刻・手先の前／指令／静止後の位置と誤差）は self.last_detail に残す。
        """
        self.last_detail = detail = {}
        notes = []
        delta, clamped = A.clamp_delta(action.delta)
        if clamped:
            notes.append('差分を 1 ステップの上限にクランプ')
        pose = self.io.current_pose()
        if pose is None:
            return 'rejected', '現在の手先姿勢（TF）が取れない'
        detail['ee_before'] = [float(v) for v in pose[0]]
        joints = self.io.current_joints()
        if joints is None or any(j not in joints for j in S.ARM_JOINTS):
            return 'rejected', '/joint_states が取れない'
        pos, quat = A.apply_delta(pose[0], pose[1], delta)
        pos, ws_clamped = A.clamp_to_workspace(pos)
        if ws_clamped:
            notes.append('作業空間にクランプ')
        detail['ee_cmd'] = [float(v) for v in pos]
        detail['clamped'] = bool(clamped or ws_clamped)
        q = self.io.solve_ik(pos, quat, joints)
        if q is None:
            return 'ik_failed', 'IK の解がない'
        q = list(q)
        if not _within_limits(q):
            return 'ik_failed', 'IK の解が不正（関節数・有限値・リミット）'
        jump = max(abs(a - joints[j]) for a, j in zip(q, S.ARM_JOINTS))
        if jump > MAX_JOINT_STEP:
            return 'rejected', f'関節の変化が大きすぎる（{jump:.2f} rad > {MAX_JOINT_STEP} rad。IK が別の解へ飛んだ可能性）'
        duration = max(MIN_DURATION, jump / MAX_JOINT_SPEED)
        t_sent = self.clock()
        detail.update(target_joints=[float(v) for v in q], duration=float(duration), t_sent=float(t_sent))
        self.io.send_arm(q, duration)
        target = A.gripper_target(action.gripper, self.gripper_threshold, self.open_when_high)
        detail['graph'] = target if target != self._last_gripper else None
        if target != self._last_gripper:
            if not self.io.send_gripper(A.gripper_angle(target)):
                return 'rejected', 'gripper_cmd が失敗した（アームの指令は送信済み）'
            self._last_gripper = target
        if not self.io.wait_settled(q, duration, t_sent):
            return 'timeout', '目標に届いて静止するまでに時間切れ'
        # 静止後の手先を TF で測り直し、指令した位置（IK に渡した位置）との距離を残す（動画の判定が使う）
        after = self.io.current_pose()
        if after is not None:
            detail['ee_after'] = [float(v) for v in after[0]]
            detail['ee_error'] = math.dist(detail['ee_after'], detail['ee_cmd'])
        return 'ok', '、'.join(notes)


def wait_settled(get_records, expected, duration, t_sent, tolerance=S.ARM_TOLERANCE,
                 now=time.time, sleep=time.sleep, poll_sec=0.2):
    """/joint_states の記録から、目標に届いて静止したかを待つ。上限は motion_judge.settle_deadline。

    静止の規則は scenario_cli wait と同じ（motion_judge.settle_time）。ただし settle_time は
    「指令時間が過ぎたら静止だけで可」とするので、ここでは目標に届いたこと（tolerance 以内）も確かめる。
    """
    deadline = M.settle_deadline(t_sent, duration)
    end = deadline + M.STILL_WINDOW
    while True:
        t = now()
        records = get_records()
        settled = M.settle_time(records, S.ARM_JOINTS, expected, tolerance, t_sent, duration, deadline, now=t)
        if settled is not None:
            at = next((r for r in records if r['t'] == settled), None)
            if at is not None:
                pos = dict(zip(at['name'], at['position']))
                return all(j in pos and abs(pos[j] - e) <= tolerance for j, e in zip(S.ARM_JOINTS, expected))
        if t >= end:
            return False
        sleep(poll_sec)
