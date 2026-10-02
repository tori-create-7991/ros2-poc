"""ros_gz_bridge の YAML 設定生成（gz → ROS の片方向、全て lazy）。"""
import yaml

from ros2_poc_sim import profile as P

# source → (gz 側の末尾トピック, ROS 型, gz 型, 生トピック名の末尾)
_SOURCE = {
    'color': ('image', 'sensor_msgs/msg/Image', 'gz.msgs.Image', 'image'),
    'depth': ('depth_image', 'sensor_msgs/msg/Image', 'gz.msgs.Image', 'depth_image'),
    'points': ('points', 'sensor_msgs/msg/PointCloud2', 'gz.msgs.PointCloudPacked', 'points'),
    'info': ('camera_info', 'sensor_msgs/msg/CameraInfo', 'gz.msgs.CameraInfo', 'camera_info'),
}


# 生トピックの末尾名（camera_adapter も同じ対応を使う）
RAW_LEAF = {src: v[3] for src, v in _SOURCE.items()}


def raw_topic(profile: dict, leaf: str) -> str:
    return f"/sim_camera/{profile['name']}/raw/{leaf}"


def needed_sources(profile: dict, pointcloud: bool) -> list:
    srcs = set()
    for sid in P.enabled_streams(profile, pointcloud):
        s = profile['topics']['streams'][sid]['source']
        srcs.add('info' if s.startswith('info_') else s)
    # 深度ストリームの CameraInfo も gz の camera_info 1 本から作る
    return [k for k in ('color', 'depth', 'points', 'info') if k in srcs]


def _gz_topic(profile: dict, leaf: str) -> str:
    # sdf.gz_base_topic が決める <topic> の下に leaf が付く。rgbd_camera は <topic>/{image,depth_image,points,camera_info}、
    # camera は <topic> が画像で camera_info は同階層なので、どちらも /sim_camera/<name>/<leaf> になる。
    return f"/sim_camera/{profile['name']}/{leaf}"


def bridge_entries(profile: dict, pointcloud: bool = False) -> list:
    out = []
    for src in needed_sources(profile, pointcloud):
        gz_leaf, ros_t, gz_t, raw_leaf = _SOURCE[src]
        out.append({
            'ros_topic_name': raw_topic(profile, raw_leaf),
            'gz_topic_name': _gz_topic(profile, gz_leaf),
            'ros_type_name': ros_t,
            'gz_type_name': gz_t,
            'direction': 'GZ_TO_ROS',
            'lazy': True,
        })
    return out


def bridge_yaml(profile: dict, pointcloud: bool = False) -> str:
    return yaml.safe_dump(bridge_entries(profile, pointcloud), sort_keys=False, allow_unicode=True)
