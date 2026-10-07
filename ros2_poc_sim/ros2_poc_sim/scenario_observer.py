"""シナリオ実行中の記録ノード（判定はしない。判定は終了後に scenario_cli judge で行う）。

  ros2 run ros2_poc_sim scenario_observer --out /workspace/runs/<日時>
出力:
  camera.mp4          /camera/color/image_raw（rgb8）を ffmpeg で符号化（受信順、可変フレームレート）
  camera_frames.csv   n,t（n 番目のフレームの受信時刻。壁時計 [s]）
  camera_info.json    最初の CameraInfo（k, width, height, frame_id）
  joints.jsonl        /joint_states（間引き）
  ee.jsonl            カメラ光学座標での手先位置（TF、間引き）
  clock.csv           wall,sim（/clock の sim time と壁時計。約 1Hz。RTF の算出用、無くても判定は動く）
時刻はすべてコンテナの壁時計（time.time()）。ros2lab の `date` と同じ Colima VM の時計を共有する。
"""
import argparse
import json
import subprocess
import time
from pathlib import Path

import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from rclpy.time import Time
from rosgraph_msgs.msg import Clock
from sensor_msgs.msg import CameraInfo, Image, JointState
from tf2_ros import Buffer, TransformException, TransformListener

EE_LINK = 'crane_x7_gripper_base_link'
from ros2_poc_sim.motion_judge import INCOMPLETE_MARK, MAX_FPS, MAX_PIXELS, valid_camera_info


class Observer(Node):
    def __init__(self, out, image_topic, info_topic, ee_link, period, max_duration):
        super().__init__('scenario_observer')
        self.out = Path(out)
        self.ee_link = ee_link
        self.period = period
        self.ffmpeg = None
        self.size = None
        self.n = 0
        self.frame_id = None
        self.last_joint_t = 0.0
        self.last_frame_t = 0.0
        self.last_clock_t = 0.0
        self.deadline = time.time() + max_duration
        # 行バッファ: 実行中に run-scenario.sh / scenario_cli wait が読むので、書いたらすぐ見えるようにする
        self.frames_csv = (self.out / 'camera_frames.csv').open('w', encoding='utf-8', buffering=1)
        self.frames_csv.write('n,t\n')
        self.joints = (self.out / 'joints.jsonl').open('w', encoding='utf-8', buffering=1)
        self.ee = (self.out / 'ee.jsonl').open('w', encoding='utf-8', buffering=1)
        self.clock_csv = (self.out / 'clock.csv').open('w', encoding='utf-8', buffering=1)
        self.clock_csv.write('wall,sim\n')
        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self)
        self.create_subscription(Image, image_topic, self.on_image, qos_profile_sensor_data)
        self.create_subscription(CameraInfo, info_topic, self.on_info, qos_profile_sensor_data)
        self.create_subscription(JointState, '/joint_states', self.on_joints, 50)
        # best-effort: publisher が reliable / best-effort のどちらでも繋がる（reliable 購読は best-effort の /clock と繋がらず黙って空になる）
        self.create_subscription(Clock, '/clock', self.on_clock, qos_profile_sensor_data)
        self.create_timer(period, self.on_timer)
        self.get_logger().info(f'記録開始: {self.out}')

    def expired(self):
        return time.time() > self.deadline

    def on_image(self, msg):
        t = time.time()
        if t - self.last_frame_t < 1.0 / MAX_FPS:
            return
        if msg.encoding != 'rgb8' or msg.width * msg.height > MAX_PIXELS:
            self.get_logger().warn(f'未対応の画像（{msg.encoding} {msg.width}x{msg.height}）は捨てる',
                                   throttle_duration_sec=5.0)
            return
        if len(msg.data) != msg.width * msg.height * 3 or msg.step != msg.width * 3:
            return
        if self.ffmpeg is None:
            self.size = (msg.width, msg.height)
            self.frame_id = msg.header.frame_id
            self.ffmpeg = subprocess.Popen(
                ['ffmpeg', '-y', '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
                 '-s', f'{msg.width}x{msg.height}', '-use_wallclock_as_timestamps', '1', '-i', '-',
                 '-fps_mode', 'passthrough', '-c:v', 'libx264', '-preset', 'ultrafast',
                 '-pix_fmt', 'yuv420p', str(self.out / 'camera.mp4')],
                stdin=subprocess.PIPE, stderr=(self.out / 'camera_ffmpeg.log').open('w'))
        elif (msg.width, msg.height) != self.size:
            return
        try:
            self.ffmpeg.stdin.write(bytes(msg.data))
        except BrokenPipeError:
            self.get_logger().error('ffmpeg が終了した（camera_ffmpeg.log を確認）', throttle_duration_sec=5.0)
            return
        self.frames_csv.write(f'{self.n},{t:.6f}\n')
        self.n += 1
        self.last_frame_t = t

    def on_info(self, msg):
        p = self.out / 'camera_info.json'
        if p.exists() or self.size is None:
            return   # 最初の画像を受けてから、その大きさに合う CameraInfo だけを保存する
        if not valid_camera_info(list(msg.k), msg.width, msg.height, self.size):
            self.get_logger().warn('画像と合わない・不正な CameraInfo は捨てる', throttle_duration_sec=5.0)
            return
        p.write_text(json.dumps({'k': list(msg.k), 'width': msg.width, 'height': msg.height,
                                 'frame_id': msg.header.frame_id}), encoding='utf-8')

    def on_joints(self, msg):
        t = time.time()
        if t - self.last_joint_t < self.period:
            return
        self.last_joint_t = t
        self.joints.write(json.dumps({'t': t, 'name': list(msg.name),
                                      'position': [float(x) for x in msg.position]}) + '\n')

    def on_clock(self, msg):
        t = time.time()
        if t - self.last_clock_t < 1.0:
            return
        self.last_clock_t = t
        self.clock_csv.write(f'{t:.6f},{msg.clock.sec + msg.clock.nanosec * 1e-9:.6f}\n')

    def on_timer(self):
        if not self.frame_id:
            return
        try:
            tf = self.tf_buffer.lookup_transform(self.frame_id, self.ee_link, Time())
        except TransformException:
            return
        v = tf.transform.translation
        self.ee.write(json.dumps({'t': time.time(), 'xyz': [v.x, v.y, v.z]}) + '\n')

    def close(self):
        for f in (self.frames_csv, self.joints, self.ee, self.clock_csv):
            f.close()
        if self.ffmpeg is not None:
            self.ffmpeg.stdin.close()
            try:
                self.ffmpeg.wait(timeout=60)
            except subprocess.TimeoutExpired:
                # 書き出しが終わらない: 止めて、判定が未完の camera.mp4 を FAIL と取り違えないよう目印を残す
                self.ffmpeg.kill()
                (self.out / INCOMPLETE_MARK).write_text('ffmpeg did not finish within 60s\n')
                self.get_logger().error('camera.mp4 の書き出しが 60 秒で終わらなかった')
        self.get_logger().info(f'記録終了: カメラ {self.n} フレーム')


def main(argv=None):
    p = argparse.ArgumentParser(prog='scenario_observer')
    p.add_argument('--out', required=True)
    p.add_argument('--image-topic', default='/camera/color/image_raw')
    p.add_argument('--info-topic', default='/camera/color/camera_info')
    p.add_argument('--ee-link', default=EE_LINK)
    p.add_argument('--period', type=float, default=0.1)
    p.add_argument('--max-duration', type=float, default=3600.0,
                   help='この秒数で記録を止める（停止の指示が届かなかったときの安全弁）')
    a, ros_args = p.parse_known_args(argv)
    Path(a.out).mkdir(parents=True, exist_ok=True)
    rclpy.init(args=ros_args)
    node = Observer(a.out, a.image_topic, a.info_topic, a.ee_link, a.period, a.max_duration)
    try:
        while rclpy.ok() and not node.expired():
            rclpy.spin_once(node, timeout_sec=0.2)
        if node.expired():
            node.get_logger().warn(f'{a.max_duration:.0f} 秒たったので記録を止める')
    except KeyboardInterrupt:
        pass
    finally:
        node.close()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
