"""camera_adapter: プロファイル YAML に従い、gz ブリッジの生トピックを実カメラドライバ形に変換して出す。

- 生トピック（/sim_camera/<id>/raw/*）は購読者がいる出力に必要なものだけ購読する（lazy）。
- CameraInfo はプロファイルの内部パラメータから作り、stamp は入力（gz）のものを使う。
  これで color / depth / info が同一 stamp になり、公式 color_detection の ExactTime 同期が成立する。
- 静的 TF（placement の base_link→camera_link とプロファイルの tf.links）もここで出す。
"""
import time

import numpy as np
import rclpy
from rclpy.clock import Clock, ClockType
from geometry_msgs.msg import TransformStamped
from rclpy.node import Node
from rclpy.qos import QoSProfile, QoSReliabilityPolicy
from sensor_msgs.msg import CameraInfo, Image, PointCloud2
from tf2_ros.static_transform_broadcaster import StaticTransformBroadcaster

from ros2_poc_sim import bridge_config as B
from ros2_poc_sim import geometry as G
from ros2_poc_sim import profile as P
from ros2_poc_sim import transforms as T

_QOS_KIND = {'color': 'image', 'depth': 'image', 'points': 'points',
             'info_color': 'info', 'info_depth': 'info'}
_MSG = {'sensor_msgs/msg/Image': Image, 'sensor_msgs/msg/CameraInfo': CameraInfo,
        'sensor_msgs/msg/PointCloud2': PointCloud2}
_RAW_LEAF = {'color': 'image', 'depth': 'depth_image', 'points': 'points', 'info': 'camera_info'}
_RAW_MSG = {'color': Image, 'depth': Image, 'points': PointCloud2, 'info': CameraInfo}


def make_qos(profile: dict, source: str) -> QoSProfile:
    q = profile['qos'][_QOS_KIND[source]]
    rel = (QoSReliabilityPolicy.RELIABLE if q.get('reliability', 'reliable') == 'reliable'
           else QoSReliabilityPolicy.BEST_EFFORT)
    return QoSProfile(depth=int(q.get('depth', 10)), reliability=rel)


def to_ros_time_msg(stamp, header):
    header.stamp = stamp
    return header


class CameraAdapter(Node):
    def __init__(self, **node_kwargs):
        super().__init__('camera_adapter', **node_kwargs)
        self.declare_parameter('profile', 'realsense_d435')
        self.declare_parameter('placement', '')
        self.declare_parameter('pointcloud', False)
        self.declare_parameter('ns_mode', '')
        self.declare_parameter('lazy_poll_sec', 0.5)

        self.profile = P.load_profile(self.get_parameter('profile').value)
        placement_name = self.get_parameter('placement').value
        self.placement = P.load_placement(placement_name) if placement_name else None
        self.pointcloud = bool(self.get_parameter('pointcloud').value)
        ns_mode = self.get_parameter('ns_mode').value or None
        self.info_fields = P.camera_info_fields(self.profile)

        sensor = self.profile['sensor']
        self.near, self.far = sensor['clip']['near'], sensor['clip']['far']

        # 出力 publisher（source ごとに束ねる）
        self.pubs = {}       # stream_id -> publisher
        self.by_source = {}  # raw source('color'|'depth'|'points'|'info') -> [stream_id]
        for sid in P.enabled_streams(self.profile, self.pointcloud):
            st = self.profile['topics']['streams'][sid]
            topic = P.topic_name(self.profile, sid, ns_mode)
            self.pubs[sid] = self.create_publisher(
                _MSG[st['type']], topic, make_qos(self.profile, st['source']))
            raw = 'info' if st['source'].startswith('info_') else st['source']
            self.by_source.setdefault(raw, []).append(sid)
            self.get_logger().info(f'{sid}: {topic}')

        self.subs = {}  # raw source -> subscription
        self._sub_started = {}   # raw source -> 購読開始の壁時計
        self._got_frame = set()  # 1 枚でも受けた raw source
        self._warned = set()
        # use_sim_time でも /clock が来ない間に止まらないよう、購読者の監視は壁時計で回す
        self.create_timer(float(self.get_parameter('lazy_poll_sec').value), self._poll_subscribers,
                          clock=Clock(clock_type=ClockType.SYSTEM_TIME))
        self._publish_static_tf()

    # ---- lazy 購読 ----------------------------------------------------
    def _poll_subscribers(self):
        for raw, sids in self.by_source.items():
            wanted = any(self.pubs[s].get_subscription_count() > 0 for s in sids)
            if wanted and raw not in self.subs:
                topic = B.raw_topic(self.profile, _RAW_LEAF[raw])
                handler = getattr(self, f'_on_{raw}')
                cb = (lambda m, _r=raw, _h=handler: self._guard(_r, _h, m))
                self.subs[raw] = self.create_subscription(
                    _RAW_MSG[raw], topic, cb, QoSProfile(depth=5,
                                                         reliability=QoSReliabilityPolicy.RELIABLE))
                self._sub_started[raw] = time.monotonic()
                self._got_frame.discard(raw)
                self._warned.discard(raw)
                self.get_logger().info(f'購読開始: {topic}')
            elif not wanted and raw in self.subs:
                self.destroy_subscription(self.subs.pop(raw))
                self._sub_started.pop(raw, None)
                self.get_logger().info(f'購読停止: {raw}')

        # 購読したのに生トピックが来ない（gz→bridge の経路切れ・重負荷）ときは気づけるよう警告する
        now = time.monotonic()
        for raw, t0 in self._sub_started.items():
            if raw not in self._got_frame and raw not in self._warned and now - t0 > 10.0:
                self._warned.add(raw)
                self.get_logger().warning(
                    f'{B.raw_topic(self.profile, _RAW_LEAF[raw])} が 10 秒来ない。'
                    'gz のカメラ・bridge・RTF（gz topic -e -t /stats）を確認する')

    # ---- コールバック -------------------------------------------------
    def _guard(self, raw, fn, msg):
        """不正なメッセージ 1 つで spin を落とさない（ログして捨てる）。"""
        self._got_frame.add(raw)
        try:
            fn(msg)
        except Exception as exc:  # noqa: BLE001
            self.get_logger().error(f'{raw} を処理できず破棄: {exc!r}', throttle_duration_sec=5.0)

    def _frame(self, sid: str) -> str:
        st = self.profile['topics']['streams'][sid]
        return self.profile['frames'][st['frame']]

    def _on_color(self, msg: Image):
        for sid in self.by_source['color']:
            out = Image()
            out.header.stamp = msg.header.stamp
            out.header.frame_id = self._frame(sid)
            out.height, out.width = msg.height, msg.width
            out.encoding = self.profile['topics']['streams'][sid]['encoding']
            out.is_bigendian, out.step = msg.is_bigendian, msg.step
            out.data = T.passthrough(bytes(msg.data))
            self.pubs[sid].publish(out)

    def _on_depth(self, msg: Image):
        if msg.encoding != '32FC1':
            self.get_logger().error(f'深度のエンコーディングが想定外: {msg.encoding}', throttle_duration_sec=5.0)
            return
        depth = np.frombuffer(bytes(msg.data), dtype='<f4').reshape(msg.height, msg.width)
        mm = T.depth_to_16uc1_mm(depth, self.near, self.far)
        for sid in self.by_source['depth']:
            out = Image()
            out.header.stamp = msg.header.stamp
            out.header.frame_id = self._frame(sid)
            out.height, out.width = msg.height, msg.width
            out.encoding = '16UC1'
            out.is_bigendian = 0
            out.step = msg.width * 2
            out.data = mm.astype('<u2').tobytes()
            self.pubs[sid].publish(out)

    def _on_points(self, msg: PointCloud2):
        data = T.points_to_optical(bytes(msg.data), msg.point_step)
        for sid in self.by_source['points']:
            out = PointCloud2()
            out.header.stamp = msg.header.stamp
            out.header.frame_id = self._frame(sid)
            out.height, out.width = msg.height, msg.width
            out.fields = msg.fields
            out.is_bigendian = msg.is_bigendian
            out.point_step, out.row_step = msg.point_step, msg.row_step
            out.is_dense = False  # 無効点は NaN
            out.data = data
            self.pubs[sid].publish(out)

    def _on_info(self, msg: CameraInfo):
        f = self.info_fields
        for sid in self.by_source['info']:
            out = CameraInfo()
            out.header.stamp = msg.header.stamp
            out.header.frame_id = self._frame(sid)
            out.width, out.height = f['width'], f['height']
            out.distortion_model = f['distortion_model']
            out.d, out.k, out.r, out.p = f['d'], f['k'], f['r'], f['p']
            self.pubs[sid].publish(out)

    # ---- 静的 TF ------------------------------------------------------
    def _publish_static_tf(self):
        transforms = []
        if self.placement and self.placement.get('mode') == 'fixed':
            pos, R = G.camera_link_pose_in_base(self.placement)
            transforms.append(self._tf(self.placement['parent_frame'],
                                       self.profile['frames']['camera_link'], pos, R))
        if self.profile['tf'].get('publish', True):
            for ln in self.profile['tf']['links']:
                transforms.append(self._tf(ln['parent'], ln['child'], np.array(ln['xyz'], float),
                                           G.rpy_to_matrix(*ln['rpy'])))
        if transforms:
            self._tf_broadcaster = StaticTransformBroadcaster(self)
            self._tf_broadcaster.sendTransform(transforms)

    def _tf(self, parent, child, pos, R):
        t = TransformStamped()
        t.header.stamp = self.get_clock().now().to_msg()
        t.header.frame_id, t.child_frame_id = parent, child
        t.transform.translation.x, t.transform.translation.y, t.transform.translation.z = (
            float(pos[0]), float(pos[1]), float(pos[2]))
        q = G.matrix_to_quat(R)
        t.transform.rotation.x, t.transform.rotation.y = float(q[0]), float(q[1])
        t.transform.rotation.z, t.transform.rotation.w = float(q[2]), float(q[3])
        return t


def main(args=None):
    rclpy.init(args=args)
    node = CameraAdapter()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
