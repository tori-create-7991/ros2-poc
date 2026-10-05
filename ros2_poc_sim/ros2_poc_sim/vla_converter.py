#!/usr/bin/env python3
"""/vla/action（手先差分）を受け、arm 層の標準インターフェースでアームを動かす ROS 2 ノード（ros2server 用）。

arm 層（ros2arm → 将来 ros2real）には手を入れず、DDS 越しに次を使う:
  TF base_link → crane_x7_gripper_base_link（現在の手先）、/compute_ik（moveit_msgs/srv/GetPositionIK）、
  /crane_x7_arm_controller/joint_trajectory、/crane_x7_gripper_controller/gripper_cmd（ParallelGripperCommand）、
  /joint_states（静止の判定）
手順の本体は vla_step.StepRunner（ROS 非依存）。ここは ROS との口（RosIO）と、/vla/action の購読・/vla/ack の発行だけ。
同時に処理するステップは 1 つ。処理中に届いた指令は busy で拒否する。入力は信頼しない（vla_action.parse_action）。

  ros2 run ros2_poc_sim vla_converter
"""
import collections
import threading
import time

import rclpy
from builtin_interfaces.msg import Duration
from control_msgs.action import ParallelGripperCommand
from geometry_msgs.msg import Pose
from moveit_msgs.srv import GetPositionIK
from rclpy.action import ActionClient
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.duration import Duration as RclDuration
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from rclpy.time import Time
from sensor_msgs.msg import JointState
from std_msgs.msg import String
from tf2_ros import Buffer, TransformListener
from trajectory_msgs.msg import JointTrajectory, JointTrajectoryPoint

from ros2_poc_sim import scenario as S
from ros2_poc_sim import vla_action as A
from ros2_poc_sim import vla_step as T

ARM_TOPIC = S.ARM_TOPIC
GRIPPER_ACTION = S.GRIPPER_ACTION
IK_SERVICE = '/compute_ik'
IK_TIMEOUT_SEC = 3.0
GRIPPER_TIMEOUT_SEC = 15.0
SUBSCRIBER_WAIT_SEC = 10.0
POSE_WAIT_SEC = 15.0
RECORD_LIMIT = 2000


def _duration(sec):
    whole = int(sec)
    return Duration(sec=whole, nanosec=int(round((sec - whole) * 1e9)))


class RosIO:
    """vla_step.StepRunner が使う口の ROS 実装。"""

    def __init__(self, node, base_frame, ee_frame, ik_group, avoid_collisions):
        self.node = node
        self.base_frame, self.ee_frame = base_frame, ee_frame
        self.ik_group, self.avoid_collisions = ik_group, avoid_collisions
        cb = ReentrantCallbackGroup()
        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, node)
        self.ik_client = node.create_client(GetPositionIK, IK_SERVICE, callback_group=cb)
        self.gripper_client = ActionClient(node, ParallelGripperCommand, GRIPPER_ACTION, callback_group=cb)
        self.traj_pub = node.create_publisher(JointTrajectory, ARM_TOPIC, 10)
        self._lock = threading.Lock()
        self._records = collections.deque(maxlen=RECORD_LIMIT)
        self._joints = None
        node.create_subscription(JointState, '/joint_states', self._on_joint_state,
                                 QoSProfile(depth=50, reliability=ReliabilityPolicy.BEST_EFFORT),
                                 callback_group=cb)

    def _on_joint_state(self, msg):
        if len(msg.name) != len(msg.position):
            return
        with self._lock:
            self._joints = dict(zip(msg.name, msg.position))
            self._records.append({'t': time.time(), 'name': list(msg.name), 'position': list(msg.position)})

    def records(self):
        with self._lock:
            return list(self._records)

    def current_joints(self):
        # 起動直後は最初の /joint_states がまだ届いていない。POSE_WAIT_SEC まで待つ
        deadline = time.time() + POSE_WAIT_SEC
        while True:
            with self._lock:
                if self._joints:
                    return dict(self._joints)
            if time.time() >= deadline:
                return None
            time.sleep(0.1)

    def current_pose(self):
        # 起動直後は /tf の Discovery が終わっておらず、すぐには取れない。POSE_WAIT_SEC まで 1 秒ずつ待って再試行する
        deadline = time.time() + POSE_WAIT_SEC
        while True:
            try:
                t = self.tf_buffer.lookup_transform(self.base_frame, self.ee_frame, Time(),
                                                    timeout=RclDuration(seconds=1.0))
                break
            except Exception as e:   # tf2 の例外は多種。取れなければ rejected にする
                if time.time() >= deadline:
                    self.node.get_logger().warning(f'TF {self.base_frame}→{self.ee_frame} が取れない: {e}')
                    return None
        p, q = t.transform.translation, t.transform.rotation
        return (p.x, p.y, p.z), (q.x, q.y, q.z, q.w)

    def _wait(self, future, timeout):
        done = threading.Event()
        future.add_done_callback(lambda _f: done.set())
        return done.wait(timeout)

    def solve_ik(self, pos, quat, seed):
        if not self.ik_client.wait_for_service(timeout_sec=5.0):
            self.node.get_logger().warning(f'{IK_SERVICE} が見つからない')
            return None
        req = GetPositionIK.Request()
        ik = req.ik_request
        ik.group_name = self.ik_group
        ik.ik_link_name = self.ee_frame
        ik.avoid_collisions = self.avoid_collisions
        ik.timeout = _duration(0.5)
        ik.pose_stamped.header.frame_id = self.base_frame
        ik.pose_stamped.pose = Pose()
        ik.pose_stamped.pose.position.x, ik.pose_stamped.pose.position.y, ik.pose_stamped.pose.position.z = pos
        o = ik.pose_stamped.pose.orientation
        o.x, o.y, o.z, o.w = quat
        ik.robot_state.joint_state.name = list(seed)
        ik.robot_state.joint_state.position = [float(v) for v in seed.values()]
        future = self.ik_client.call_async(req)
        if not self._wait(future, IK_TIMEOUT_SEC) or future.result() is None:
            self.node.get_logger().warning('IK の応答がない')
            return None
        res = future.result()
        if res.error_code.val != 1:     # moveit_msgs/MoveItErrorCodes.SUCCESS
            self.node.get_logger().info(f'IK 失敗 error_code={res.error_code.val}')
            return None
        sol = dict(zip(res.solution.joint_state.name, res.solution.joint_state.position))
        if any(j not in sol for j in S.ARM_JOINTS):
            return None
        return [float(sol[j]) for j in S.ARM_JOINTS]

    def send_arm(self, positions, duration):
        deadline = time.time() + SUBSCRIBER_WAIT_SEC
        while self.traj_pub.get_subscription_count() == 0 and time.time() < deadline:
            time.sleep(0.1)    # 初回は Discovery を待つ（ros2 topic pub -w 1 と同じ理由）
        msg = JointTrajectory()
        msg.joint_names = list(S.ARM_JOINTS)
        point = JointTrajectoryPoint()
        point.positions = [float(v) for v in positions]
        point.time_from_start = _duration(duration)
        msg.points = [point]
        self.traj_pub.publish(msg)

    def send_gripper(self, angle):
        if not self.gripper_client.wait_for_server(timeout_sec=5.0):
            self.node.get_logger().warning(f'{GRIPPER_ACTION} が見つからない')
            return False
        goal = ParallelGripperCommand.Goal()
        goal.command.name = [S.GRIPPER_JOINT]
        goal.command.position = [float(angle)]
        send = self.gripper_client.send_goal_async(goal)
        if not self._wait(send, 5.0) or not send.result().accepted:
            return False
        result = send.result().get_result_async()
        if not self._wait(result, GRIPPER_TIMEOUT_SEC):
            return False
        r = result.result().result
        self.node.get_logger().info(f'gripper reached_goal={r.reached_goal} stalled={r.stalled}')
        return True    # 物を掴んで止まる（stalled）のも正常

    def wait_settled(self, expected, duration, t_sent):
        return T.wait_settled(self.records, expected, duration, t_sent)


class VlaConverter(Node):
    def __init__(self):
        super().__init__('vla_converter')
        self.declare_parameter('base_frame', 'base_link')
        self.declare_parameter('ee_frame', 'crane_x7_gripper_base_link')
        self.declare_parameter('ik_group', 'arm')
        self.declare_parameter('avoid_collisions', True)
        self.declare_parameter('gripper_threshold', A.GRIPPER_THRESHOLD)
        self.declare_parameter('gripper_open_when_high', True)
        p = self.get_parameter
        self.io = RosIO(self, p('base_frame').value, p('ee_frame').value, p('ik_group').value,
                        p('avoid_collisions').value)
        self.runner = T.StepRunner(self.io, p('gripper_threshold').value, p('gripper_open_when_high').value)
        self.ack_pub = self.create_publisher(String, A.ACK_TOPIC, 10)
        self._busy = threading.Lock()
        self.create_subscription(String, A.ACTION_TOPIC, self._on_action, 10,
                                 callback_group=ReentrantCallbackGroup())
        self.get_logger().info(f'{A.ACTION_TOPIC} を待つ（IK グループ {p("ik_group").value}）')

    def _ack(self, seq, status, reason=''):
        self.ack_pub.publish(String(data=A.format_ack(seq, status, reason)))
        self.get_logger().info(f'ack seq={seq} {status} {reason}')

    def _on_action(self, msg):
        try:
            action = A.parse_action(msg.data)
        except ValueError as e:
            self.get_logger().warning(f'不正な {A.ACTION_TOPIC} を破棄: {e}')
            return
        if not self._busy.acquire(blocking=False):
            self._ack(action.seq, 'rejected', 'busy（前のステップを処理中）')
            return
        threading.Thread(target=self._run, args=(action,), daemon=True).start()

    def _run(self, action):
        try:
            status, reason = self.runner.run(action)
        except Exception as e:    # ノードは落とさない。ack で知らせる
            self.get_logger().error(f'ステップの実行で例外: {e!r}')
            status, reason = 'rejected', f'内部エラー: {type(e).__name__}'
        finally:
            self._busy.release()
        self._ack(action.seq, status, reason)


def main(args=None):
    rclpy.init(args=args)
    node = VlaConverter()
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
