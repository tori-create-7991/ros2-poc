#!/usr/bin/env python3
"""VLA ノード（ros2server 用）: 仮想カメラ画像と指示を VLA サーバー（POST /act）へ送り、返った手先差分を
/vla/action に出して、変換ノード（vla_converter）の /vla/ack を待つ。--steps 回で終了する。

  ros2 run ros2_poc_sim vla_node --instruction "move up" --steps 3
  終了コード: 0 = 全ステップ ok / 1 = ok でないステップがあった / 2 = 環境の問題 / 64 = 引数の誤り

購読: /camera/color/image_raw（rgb8）、/vla/ack  発行: /vla/action
画像は 256x256 へ最近傍でリサイズして送る（OpenVLA の入力。以降の前処理はサーバー側）。
カメラ映像は /joint_states より 1〜1.5 秒遅れる（docs/sim-scenario-recording.md）ので、2 ステップ目以降は
SETTLE_SEC 待ってから新しいフレームを取る。実行中の ROS 2 ノードはこのファイル 1 つ（vla_converter と別プロセス）。
"""
import argparse
import os
import random
import sys
import threading
import time

import rclpy
from rclpy.executors import ExternalShutdownException, SingleThreadedExecutor
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from rclpy.utilities import remove_ros_args
from sensor_msgs.msg import Image
from std_msgs.msg import String

from ros2_poc_sim import vla_action as A
from ros2_poc_sim import vla_client as V
from ros2_poc_sim import vla_loop as L

DEFAULT_ENDPOINT = 'http://vla-server:8000/act'
IMAGE_TOPIC = '/camera/color/image_raw'
IMAGE_WAIT_SEC = 60.0
DISCOVERY_WAIT_SEC = 30.0
SETTLE_SEC = 1.5     # motion_judge.SETTLE_SEC と同じ（カメラは関節状態より遅れる）


class VlaNode(Node):
    def __init__(self, image_topic):
        super().__init__('vla_node')
        self.pub = self.create_publisher(String, A.ACTION_TOPIC, 10)
        self._cond = threading.Condition()
        self._image = None
        self._want_image = False    # fresh_image が待っている間だけ画像を処理する（CPU 描画のシミュへの負荷を避ける）
        self._acks = {}
        self.create_subscription(Image, image_topic, self._on_image,
                                 QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT))
        self.create_subscription(String, A.ACK_TOPIC, self._on_ack, 10)

    def _on_image(self, msg):
        if not self._want_image:
            return
        try:
            img = V.image_from_ros(msg.encoding, msg.height, msg.width, bytes(msg.data))
        except ValueError as e:
            self.get_logger().warning(f'画像を捨てた: {e}', throttle_duration_sec=10.0)
            return
        small = V.resize_nearest(img)
        with self._cond:
            if not self._want_image:    # デコード中に fresh_image が時間切れで戻っていたら捨てる
                return
            self._image = small
            self._want_image = False
            self._cond.notify_all()

    def _on_ack(self, msg):
        try:
            seq, status, reason = A.parse_ack(msg.data)
        except ValueError as e:
            self.get_logger().warning(f'不正な ack を破棄: {e}')
            return
        with self._cond:
            self._acks[seq] = (seq, status, reason)
            self._cond.notify_all()

    def wait_converter(self, timeout):
        deadline = time.time() + timeout

        def ready():    # 指令の購読者（変換ノード）と、ack の発行者（同じ変換ノード）の両方が見えるまで待つ
            return self.pub.get_subscription_count() > 0 and self.count_publishers(A.ACK_TOPIC) > 0

        while not ready() and time.time() < deadline:
            time.sleep(0.2)
        return ready()

    def fresh_image(self, delay=0.0, timeout=IMAGE_WAIT_SEC):
        """delay 秒待ってから、そのあとに届いた最初のフレーム（リサイズ済み）を返す。来なければ None。"""
        time.sleep(delay)
        with self._cond:
            self._image = None
            self._want_image = True
            self._cond.wait_for(lambda: self._image is not None, timeout)
            self._want_image = False
            return self._image

    def wait_ack(self, seq, timeout):
        with self._cond:
            self._cond.wait_for(lambda: seq in self._acks, timeout)
            return self._acks.get(seq)


def _spin(executor):
    try:
        executor.spin()
    except ExternalShutdownException:
        pass     # 終了処理の rclpy.try_shutdown() による正常な停止


def parse_args(argv):
    ap = argparse.ArgumentParser(description='VLA ノード: 画像 + 指示 → /act → /vla/action → /vla/ack')
    ap.add_argument('--instruction', required=True, help='VLA への指示文（例: "move up"）')
    ap.add_argument('--steps', type=int, default=3, help=f'実行するステップ数（1〜{L.MAX_STEPS}）')
    ap.add_argument('--endpoint', default=os.environ.get('VLA_ENDPOINT', DEFAULT_ENDPOINT),
                    help='POST /act の URL（既定: 環境変数 VLA_ENDPOINT、なければ %(default)s）')
    ap.add_argument('--unnorm-key', default=None, help='OpenVLA の unnorm_key（スタブは無視。CRANE-X7 向けは未検証）')
    ap.add_argument('--image-topic', default=IMAGE_TOPIC)
    ap.add_argument('--request-timeout', type=float, default=60.0, help='POST /act のタイムアウト [s]')
    ap.add_argument('--ack-timeout', type=float, default=240.0, help='1 ステップの ack を待つ上限 [s]（シミュは遅い）')
    return ap.parse_args(argv)


def main(argv=None):
    args = remove_ros_args(args=sys.argv if argv is None else argv)[1:]
    try:
        a = parse_args(args)
        V.validate_endpoint(a.endpoint)
        V.validate_request(a.instruction, a.unnorm_key)
        if not 1 <= a.steps <= L.MAX_STEPS:
            raise ValueError(f'--steps は 1〜{L.MAX_STEPS}')
    except ValueError as e:
        print(f'引数の誤り: {e}', file=sys.stderr)
        return 64
    rclpy.init()
    node = VlaNode(a.image_topic)
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    spinner = threading.Thread(target=_spin, args=(executor,), daemon=True)
    spinner.start()
    try:
        if not node.wait_converter(DISCOVERY_WAIT_SEC):
            print(f'{A.ACTION_TOPIC} を購読するノード（vla_converter）が見つからない。ros2arm 側ではなく同じ DDS ドメインで'
                  '起動しているか確認する。', file=sys.stderr)
            return L.EXIT_ENV
        calls = {'n': 0}

        def get_image():
            calls['n'] += 1
            return node.fresh_image(delay=0.0 if calls['n'] == 1 else SETTLE_SEC)

        code, _results = L.run_loop(
            a.steps, get_image,
            lambda img: V.post_act(a.endpoint, V.build_payload(img, a.instruction, a.unnorm_key), a.request_timeout),
            lambda action: node.pub.publish(String(data=A.format_action(action))),
            lambda seq: node.wait_ack(seq, a.ack_timeout),
            log=lambda m: print(m, flush=True),
            start_seq=random.randint(1, 10 ** 9))    # 実行ごとに変える（前回の遅い ack と取り違えない）
        return code
    except KeyboardInterrupt:
        return 130
    finally:
        # 順序が大事: 先に shutdown して spin を終わらせ、スレッドを待ってからノードを破棄する
        # （spin 中に破棄すると "terminate called without an active exception" で異常終了する）
        rclpy.try_shutdown()
        spinner.join(timeout=5.0)
        node.destroy_node()


if __name__ == '__main__':
    sys.exit(main())
