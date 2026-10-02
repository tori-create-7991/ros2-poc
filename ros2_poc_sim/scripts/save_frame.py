#!/usr/bin/env python3
"""画像トピックの 1 枚を PNG で保存する（stdlib + numpy + rclpy のみ）。

ros2lab には PIL / cv_bridge / rqt_image_view が無いので、画像の中身を確認する用途に使う。
  python3 save_frame.py /camera/color/image_raw /workspace/color.png
対応: rgb8 / bgr8 / mono8 / 16UC1 / 32FC1（深度は見やすいよう 0〜最大値で正規化）。
"""
import struct
import sys
import zlib

import numpy as np


def encode_png(arr: np.ndarray) -> bytes:
    """arr: (H, W) uint8 グレー、または (H, W, 3) uint8 RGB。"""
    if arr.ndim == 2:
        color_type, raw = 0, arr
    else:
        color_type, raw = 2, arr
    h, w = arr.shape[:2]
    rows = b''.join(b'\x00' + raw[y].tobytes() for y in range(h))

    def chunk(tag, data):
        c = struct.pack('>I', len(data)) + tag + data
        return c + struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff)

    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, color_type, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(rows, 6))
            + chunk(b'IEND', b''))


def image_to_array(encoding: str, height: int, width: int, data: bytes) -> np.ndarray:
    if encoding == 'rgb8':
        return np.frombuffer(data, np.uint8).reshape(height, width, 3)
    if encoding == 'bgr8':
        return np.frombuffer(data, np.uint8).reshape(height, width, 3)[:, :, ::-1]
    if encoding == 'mono8':
        return np.frombuffer(data, np.uint8).reshape(height, width)
    if encoding in ('16UC1', '32FC1'):
        dt = '<u2' if encoding == '16UC1' else '<f4'
        d = np.frombuffer(data, dt).reshape(height, width).astype(np.float64)
        d[~np.isfinite(d)] = 0
        m = d.max()
        return (d / m * 255).astype(np.uint8) if m > 0 else d.astype(np.uint8)
    raise ValueError(f'未対応のエンコーディング: {encoding}')


def main(argv=None):
    import rclpy
    from rclpy.qos import qos_profile_sensor_data
    from sensor_msgs.msg import Image

    argv = sys.argv[1:] if argv is None else argv
    topic = argv[0] if argv else '/camera/color/image_raw'
    out = argv[1] if len(argv) > 1 else '/workspace/frame.png'
    timeout = float(argv[2]) if len(argv) > 2 else 20.0

    rclpy.init()
    node = rclpy.create_node('save_frame')
    got = []
    node.create_subscription(Image, topic, got.append, qos_profile_sensor_data)
    end = node.get_clock().now().nanoseconds + int(timeout * 1e9)
    while not got and node.get_clock().now().nanoseconds < end:
        rclpy.spin_once(node, timeout_sec=0.2)
    node.destroy_node()
    rclpy.shutdown()
    if not got:
        print(f'ERROR: {timeout}s 以内に {topic} を受信できない', file=sys.stderr)
        return 1
    m = got[0]
    arr = image_to_array(m.encoding, m.height, m.width, bytes(m.data))
    with open(out, 'wb') as f:
        f.write(encode_png(arr))
    print(f'saved {out}: {m.width}x{m.height} {m.encoding} frame_id={m.header.frame_id}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
