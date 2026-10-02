"""camera_adapter の結合テスト（rclpy が必要。無ければ skip）。
gz の代わりに生トピックへ合成メッセージを流し、出力のエンコーディング・frame・stamp を確認する。"""
import os
import time

import numpy as np
import pytest

rclpy = pytest.importorskip('rclpy')

from rclpy.executors import SingleThreadedExecutor  # noqa: E402
from rclpy.parameter import Parameter  # noqa: E402
from rclpy.qos import QoSProfile, QoSReliabilityPolicy  # noqa: E402
from sensor_msgs.msg import CameraInfo, Image, PointCloud2, PointField  # noqa: E402
from builtin_interfaces.msg import Time  # noqa: E402
from tf2_msgs.msg import TFMessage  # noqa: E402
from rclpy.qos import QoSDurabilityPolicy  # noqa: E402

from ros2_poc_sim.camera_adapter import CameraAdapter  # noqa: E402

RAW = '/sim_camera/realsense_d435/raw'
STAMP = Time(sec=12, nanosec=300000000)
REL = QoSProfile(depth=10, reliability=QoSReliabilityPolicy.RELIABLE)


@pytest.fixture(scope='module')
def ctx():
    os.environ.setdefault('ROS_DOMAIN_ID', '97')
    os.environ['ROS_AUTOMATIC_DISCOVERY_RANGE'] = 'LOCALHOST'
    rclpy.init()
    yield
    rclpy.shutdown()


def _spin(ex, sec=1.5, until=None):
    end = time.time() + sec
    while time.time() < end:
        ex.spin_once(timeout_sec=0.05)
        if until and until():
            return


def _raw_image(w=4, h=3, enc='rgb8'):
    m = Image()
    m.header.stamp = STAMP
    m.header.frame_id = 'cam/link/sensor'
    m.height, m.width, m.encoding, m.step = h, w, enc, w * 3
    m.data = bytes(range(w * h * 3))
    return m


def test_outputs(ctx):
    adapter = CameraAdapter(parameter_overrides=[Parameter('pointcloud', value=True)])
    helper = rclpy.create_node('helper')
    ex = SingleThreadedExecutor()
    ex.add_node(adapter)
    ex.add_node(helper)

    got = {}

    def keep(name):
        return lambda m: got.__setitem__(name, m)

    helper.create_subscription(Image, '/camera/color/image_raw', keep('color'), REL)
    helper.create_subscription(CameraInfo, '/camera/color/camera_info', keep('cinfo'), REL)
    helper.create_subscription(Image, '/camera/aligned_depth_to_color/image_raw', keep('depth'), REL)
    helper.create_subscription(CameraInfo, '/camera/aligned_depth_to_color/camera_info', keep('dinfo'), REL)
    pub_color = helper.create_publisher(Image, f'{RAW}/image', REL)
    pub_depth = helper.create_publisher(Image, f'{RAW}/depth_image', REL)
    pub_info = helper.create_publisher(CameraInfo, f'{RAW}/camera_info', REL)

    # lazy: 購読者が付くと adapter が生トピックを購読し始めるまで待つ
    _spin(ex, 2.0, until=lambda: len(adapter.subs) >= 3)
    assert {'color', 'depth', 'info'} <= set(adapter.subs)
    assert 'points' not in adapter.subs  # 点群の購読者がいない間は購読しない

    depth = Image()
    depth.header.stamp = STAMP
    depth.header.frame_id = 'cam/link/sensor'
    depth.height, depth.width, depth.encoding, depth.step = 1, 3, '32FC1', 12
    depth.data = np.array([0.5, float('inf'), 1.0], dtype='<f4').tobytes()
    info = CameraInfo()
    info.header.stamp = STAMP
    info.header.frame_id = 'cam/link/sensor'

    for _ in range(20):
        pub_color.publish(_raw_image())
        pub_depth.publish(depth)
        pub_info.publish(info)
        _spin(ex, 0.1, until=lambda: len(got) == 4)
        if len(got) == 4:
            break

    c, d = got['color'], got['depth']
    assert c.encoding == 'rgb8' and c.header.frame_id == 'camera_color_optical_frame'
    assert d.encoding == '16UC1' and d.step == 6
    assert np.frombuffer(bytes(d.data), '<u2').tolist() == [500, 0, 1000]
    assert d.header.frame_id == 'camera_color_optical_frame'
    # 3 本が同一 stamp（ExactTime 同期の前提）
    assert (c.header.stamp == d.header.stamp == got['cinfo'].header.stamp == got['dinfo'].header.stamp)
    ci = got['cinfo']
    assert list(ci.k)[0] == pytest.approx(462.14) and ci.width == 640 and ci.height == 480
    assert ci.header.frame_id == 'camera_color_optical_frame'
    adapter.destroy_node()
    helper.destroy_node()


def test_points_are_rotated_and_framed(ctx):
    adapter = CameraAdapter(parameter_overrides=[Parameter('pointcloud', value=True)])
    helper = rclpy.create_node('helper2')
    ex = SingleThreadedExecutor()
    ex.add_node(adapter)
    ex.add_node(helper)
    got = {}
    helper.create_subscription(PointCloud2, '/camera/depth/color/points',
                               lambda m: got.__setitem__('p', m), REL)
    pub = helper.create_publisher(PointCloud2, f'{RAW}/points', REL)
    _spin(ex, 2.0, until=lambda: 'points' in adapter.subs)
    assert 'points' in adapter.subs

    m = PointCloud2()
    m.header.stamp = STAMP
    m.header.frame_id = 'cam/link/sensor'
    m.height, m.width, m.point_step, m.row_step = 1, 1, 24, 24
    m.fields = [PointField(name=n, offset=o, datatype=7, count=1)
                for n, o in (('x', 0), ('y', 4), ('z', 8), ('rgb', 16))]
    raw = np.zeros(24, np.uint8)
    raw[0:12] = np.array([2.0, 0.5, 0.25], dtype='<f4').view(np.uint8)
    m.data = raw.tobytes()
    for _ in range(20):
        pub.publish(m)
        _spin(ex, 0.1, until=lambda: 'p' in got)
        if 'p' in got:
            break
    out = got['p']
    xyz = np.frombuffer(bytes(out.data), '<f4')[:3]
    assert xyz.tolist() == [-0.5, -0.25, 2.0]
    assert out.header.frame_id == 'camera_color_optical_frame'
    adapter.destroy_node()
    helper.destroy_node()


def test_static_tf_published(ctx):
    helper = rclpy.create_node('helper3')
    frames = []
    helper.create_subscription(
        TFMessage, '/tf_static',
        lambda m: frames.extend((x.header.frame_id, x.child_frame_id) for x in m.transforms),
        QoSProfile(depth=100, reliability=QoSReliabilityPolicy.RELIABLE,
                   durability=QoSDurabilityPolicy.TRANSIENT_LOCAL))
    adapter = CameraAdapter(parameter_overrides=[Parameter('placement', value='fixed_near_top')])
    ex = SingleThreadedExecutor()
    ex.add_node(helper)
    ex.add_node(adapter)
    _spin(ex, 2.0, until=lambda: ('camera_color_frame', 'camera_color_optical_frame') in frames)
    assert ('base_link', 'camera_link') in frames
    assert ('camera_link', 'camera_color_frame') in frames
    assert ('camera_color_frame', 'camera_color_optical_frame') in frames
    adapter.destroy_node()
    helper.destroy_node()
