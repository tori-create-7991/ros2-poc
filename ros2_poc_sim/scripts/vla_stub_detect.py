#!/usr/bin/env python3
"""VLA の代わりの簡易スクリプト: 青い立方体を検出して TF `target_0` を配信する（アームは動かさない）。

公式 crane_x7_examples の color_detection と同じ規約（TF target_0、親 = 画像の frame_id、
青の HSV 範囲、物体までの距離 0.2〜0.5 m）にしてあるので、後段の pick_and_place_tf や
本物の VLA ノードと差し替えられる。ros2lab（ros-base）で動くよう stdlib + numpy + rclpy + tf2_ros のみ。

  python3 vla_stub_detect.py
購読: /camera/color/image_raw (rgb8), /camera/aligned_depth_to_color/image_raw (16UC1, mm),
      /camera/color/camera_info
"""
import sys

import numpy as np

# 公式 color_detection の HSV（OpenCV スケール: H 0-180, S/V 0-255）
LOW_H, HIGH_H = 100, 125
LOW_S, HIGH_S = 100, 255
LOW_V, HIGH_V = 30, 255
DEPTH_MIN, DEPTH_MAX = 0.2, 0.5   # 公式 color_detection と同じ有効距離 [m]
DEPTH_OFFSET = 0.015              # 物体表面の少し奥（公式と同じ）
MIN_PIXELS = 30
EXPECTED_FRAME_ID = 'camera_color_optical_frame'
MAX_LATERAL = 1.0   # 光軸に直交する方向の妥当な上限 [m]（有効距離 0.5m の画角内に収まる）


def decode_color(encoding, height, width, data):
    if encoding != 'rgb8':
        raise ValueError(f'色画像は rgb8 のみ対応（受信: {encoding}）')
    return np.frombuffer(data, np.uint8).reshape(height, width, 3)


def decode_depth(encoding, height, width, data):
    if encoding != '16UC1':
        raise ValueError(f'深度は 16UC1 (mm) のみ対応（受信: {encoding}）')
    return np.frombuffer(data, '<u2').reshape(height, width)


def rgb_to_hsv_cv(img):
    """RGB uint8 → OpenCV 流の HSV (H 0-180, S 0-255, V 0-255)。"""
    f = img.astype(np.float32) / 255.0
    r, g, b = f[..., 0], f[..., 1], f[..., 2]
    mx, mn = f.max(axis=-1), f.min(axis=-1)
    d = mx - mn
    v = mx
    s = np.where(mx > 0, d / np.where(mx > 0, mx, 1), 0.0)
    safe = np.where(d > 0, d, 1)
    h = np.zeros_like(mx)
    h = np.where(mx == r, 60.0 * (g - b) / safe, h)
    h = np.where(mx == g, 120.0 + 60.0 * (b - r) / safe, h)
    h = np.where(mx == b, 240.0 + 60.0 * (r - g) / safe, h)
    h = np.where(d > 0, h, 0.0) % 360.0
    return np.stack([h / 2.0, s * 255.0, v * 255.0], axis=-1)


def blue_mask(img):
    hsv = rgb_to_hsv_cv(img)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    return ((h >= LOW_H) & (h <= HIGH_H) & (s >= LOW_S) & (s <= HIGH_S)
            & (v >= LOW_V) & (v <= HIGH_V))


def centroid(mask, min_pixels=MIN_PIXELS):
    n = int(mask.sum())
    if n < min_pixels:
        return None
    ys, xs = np.nonzero(mask)
    return float(xs.mean()), float(ys.mean())


def depth_at(depth_mm, mask):
    """マスク内の有効な深度（0 は無効）の中央値 [m]。無ければ None。"""
    vals = depth_mm[mask]
    vals = vals[vals > 0]
    if vals.size == 0:
        return None
    return float(np.median(vals)) / 1000.0


def backproject(u, v, z_depth, k, offset=DEPTH_OFFSET):
    """ピクセル (u, v) と光軸方向の深度 z から光学座標 (x右, y下, z前) [m]。"""
    fx, cx, fy, cy = k[0], k[2], k[4], k[5]
    z = z_depth + offset
    return ((u - cx) / fx * z, (v - cy) / fy * z, z)


def intrinsics_ok(k):
    """fx, fy が正の有限値で、主点が有限であること。"""
    import math
    return (len(k) >= 6 and all(math.isfinite(v) for v in (k[0], k[2], k[4], k[5]))
            and k[0] > 0 and k[4] > 0)


def in_range(z, lo=DEPTH_MIN, hi=DEPTH_MAX):
    return lo <= z <= hi


def main(argv=None):
    import rclpy
    from geometry_msgs.msg import TransformStamped
    from rclpy.node import Node
    from rclpy.qos import qos_profile_sensor_data
    from sensor_msgs.msg import CameraInfo, Image
    from tf2_ros import Buffer, TransformBroadcaster, TransformListener

    class Detector(Node):
        def __init__(self):
            super().__init__('vla_stub_detect')
            # 入力は信頼できない（DDS は無認証）。想定外の frame_id や内部パラメータは捨てる
            self.declare_parameter('expected_frame_id', EXPECTED_FRAME_ID)
            self.expected_frame = self.get_parameter('expected_frame_id').value
            self.colors = {}   # stamp(ns) -> Image（直近だけ保持）
            self.depths = {}
            self.info = None
            self.tf_broadcaster = TransformBroadcaster(self)
            self.tf_buffer = Buffer()
            self.tf_listener = TransformListener(self.tf_buffer, self)
            self.create_subscription(CameraInfo, '/camera/color/camera_info', self._on_info,
                                     qos_profile_sensor_data)
            self.create_subscription(Image, '/camera/aligned_depth_to_color/image_raw',
                                     self._on_depth, qos_profile_sensor_data)
            self.create_subscription(Image, '/camera/color/image_raw', self._on_color,
                                     qos_profile_sensor_data)
            self.create_timer(10.0, self._check_inputs)
            self.get_logger().info('vla_stub_detect: 青い立方体を検出して TF target_0 を配信します（アームは動かしません）')

        def _check_inputs(self):
            if not self.depths:
                self.get_logger().error(
                    '10 秒たっても深度 (/camera/aligned_depth_to_color/image_raw) が来ない。'
                    'usb_cam プロファイルなど深度の無いカメラでは動かない。realsense_d435 で起動する',
                    throttle_duration_sec=30.0)

        @staticmethod
        def _ns(stamp):
            return stamp.sec * 1_000_000_000 + stamp.nanosec

        @staticmethod
        def _trim(cache):
            while len(cache) > 10:
                cache.pop(min(cache))

        def _on_info(self, msg):
            self.info = msg

        def _on_depth(self, msg):
            key = self._ns(msg.header.stamp)
            self.depths[key] = msg
            self._trim(self.depths)
            if key in self.colors:   # 色が先に着いていた組もここで拾う
                self._process(self.colors.pop(key), msg)

        def _on_color(self, msg):
            key = self._ns(msg.header.stamp)
            depth_msg = self.depths.get(key)
            if depth_msg is None:
                self.colors[key] = msg
                self._trim(self.colors)
                return
            self._process(msg, depth_msg)

        def _process(self, msg, depth_msg):
            try:
                self._process_checked(msg, depth_msg)
            except Exception as exc:  # noqa: BLE001  入力は信頼しない。1 つの不正な組で落ちない
                self.get_logger().error(f'処理できず破棄: {exc!r}', throttle_duration_sec=5.0)

        def _process_checked(self, msg, depth_msg):
            if self.info is None:
                return  # 同一 stamp の組だけを使う（公式 color_detection の ExactTime と同じ）
            if msg.header.frame_id != self.expected_frame:
                self.get_logger().error(
                    f'frame_id が想定外: {msg.header.frame_id!r} (期待 {self.expected_frame!r})。破棄',
                    throttle_duration_sec=5.0)
                return
            k = list(self.info.k)
            if not intrinsics_ok(k):
                self.get_logger().error(f'camera_info の内部パラメータが不正: {k[:6]}。破棄',
                                        throttle_duration_sec=5.0)
                return
            try:
                img = decode_color(msg.encoding, msg.height, msg.width, bytes(msg.data))
                depth = decode_depth(depth_msg.encoding, depth_msg.height, depth_msg.width,
                                     bytes(depth_msg.data))
            except ValueError as exc:
                self.get_logger().error(str(exc), throttle_duration_sec=5.0)
                return
            if depth.shape != img.shape[:2]:
                self.get_logger().error(f'色 {img.shape[:2]} と深度 {depth.shape} の大きさが違う。破棄',
                                        throttle_duration_sec=5.0)
                return
            if depth_msg.header.frame_id != self.expected_frame:
                self.get_logger().error(f'深度の frame_id が想定外: {depth_msg.header.frame_id!r}。破棄',
                                        throttle_duration_sec=5.0)
                return
            mask = blue_mask(img)
            c = centroid(mask)
            if c is None:
                return
            z = depth_at(depth, mask)
            if z is None or not in_range(z + DEPTH_OFFSET):
                self.get_logger().info('深度が取れないか範囲外（0.2〜0.5m）。把持位置は出さない',
                                       throttle_duration_sec=5.0)
                return
            x, y, zz = backproject(c[0], c[1], z, k)
            if abs(x) > MAX_LATERAL or abs(y) > MAX_LATERAL:
                self.get_logger().error(f'位置が範囲外 ({x:.2f},{y:.2f})。内部パラメータが不正？ 破棄',
                                        throttle_duration_sec=5.0)
                return
            t = TransformStamped()
            t.header = msg.header
            t.child_frame_id = 'target_0'
            t.transform.translation.x, t.transform.translation.y, t.transform.translation.z = x, y, zz
            t.transform.rotation.w = 1.0
            self.tf_broadcaster.sendTransform(t)
            try:
                b = self.tf_buffer.lookup_transform('base_link', 'target_0', rclpy.time.Time())
                p = b.transform.translation
                self.get_logger().info(
                    f'target_0: camera=({x:.3f},{y:.3f},{zz:.3f}) base_link=({p.x:.3f},{p.y:.3f},{p.z:.3f})',
                    throttle_duration_sec=2.0)
            except Exception:  # noqa: BLE001  TF 未接続の間は camera 座標だけ出す
                self.get_logger().info(f'target_0: camera=({x:.3f},{y:.3f},{zz:.3f})',
                                       throttle_duration_sec=2.0)

    rclpy.init(args=argv)
    node = Detector()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    sys.exit(main())
