"""カメラモデル・物体の SDF 生成と、Sensors システムの実行時追加リクエスト。"""
from xml.sax.saxutils import escape

from ros2_poc_sim import geometry as G
from ros2_poc_sim import profile as P

CUBE_SIDE = 0.04
CUBE_MASS = 0.05
# OpenCV の HSV で H≈118, S≈241, V≈230（公式 color_detection の青: H100-125, S>=100, V>=30）
CUBE_DIFFUSE = (0.05, 0.1, 0.9, 1.0)


def gz_base_topic(profile: dict) -> str:
    """rgbd_camera は <topic> を基準に image/depth_image/points/camera_info が付く。
    camera は <topic> がそのまま画像トピックで、camera_info は同階層に付く。"""
    base = f"sim_camera/{profile['name']}"
    return base if profile['sensor']['gz_type'] == 'rgbd_camera' else f'{base}/image'


def _pose_str(pos, R) -> str:
    rpy = G.matrix_to_rpy(R)
    return ' '.join(f'{v:.9g}' for v in (*pos, *rpy))


def camera_pose6(profile: dict, placement: dict):
    """ワールド上のセンサ姿勢 (x, y, z, roll, pitch, yaw)。"""
    pos, R = G.sensor_pose_in_world(profile, placement)
    return (*[float(v) for v in pos], *G.matrix_to_rpy(R))


def object_pose6(placement: dict):
    obj = placement['object']
    base = G.world_to_base_offset(placement)
    return tuple(float(base[i] + obj['xyz_in_base_link'][i]) for i in range(3)) + (0.0, 0.0, 0.0)


def pose_args(pose6) -> list:
    """`ros_gz_sim create` は -x/-y/-z/-R/-P/-Y（既定 0）でモデル姿勢を上書きするので明示的に渡す。"""
    x, y, z, r, p, yw = pose6
    return ['-x', f'{x:.9g}', '-y', f'{y:.9g}', '-z', f'{z:.9g}',
            '-R', f'{r:.9g}', '-P', f'{p:.9g}', '-Y', f'{yw:.9g}']


def _color_frame_id(profile: dict) -> str:
    st = profile['topics']['streams']['color_image']
    return profile['frames'][st['frame']]


def camera_model_sdf(profile: dict, placement: dict) -> str:
    s = profile['sensor']
    fmt = '<format>R8G8B8</format>' if s['gz_type'] == 'camera' else ''
    pos, R = G.sensor_pose_in_world(profile, placement)
    return f"""<?xml version="1.0"?>
<sdf version="1.9">
  <model name="sim_camera_{escape(profile['name'])}">
    <static>true</static>
    <pose>{_pose_str(pos, R)}</pose>
    <link name="link">
      <visual name="body">
        <geometry><box><size>0.02 0.09 0.025</size></box></geometry>
        <material><diffuse>0.1 0.1 0.1 1</diffuse></material>
      </visual>
      <sensor name="sensor" type="{s['gz_type']}">
        <update_rate>{s['fps']}</update_rate>
        <topic>{gz_base_topic(profile)}</topic>
        <gz_frame_id>{escape(_color_frame_id(profile))}</gz_frame_id>
        <camera>
          <horizontal_fov>{P.horizontal_fov(profile):.9g}</horizontal_fov>
          <image><width>{s['width']}</width><height>{s['height']}</height>{fmt}</image>
          <clip><near>{s['clip']['near']}</near><far>{s['clip']['far']}</far></clip>
        </camera>
        <always_on>1</always_on>
        <visualize>false</visualize>
      </sensor>
    </link>
  </model>
</sdf>
"""


def object_model_sdf(placement: dict) -> str:
    obj = placement['object']
    base = G.world_to_base_offset(placement)
    x, y, z = (base[i] + obj['xyz_in_base_link'][i] for i in range(3))
    r, g, b, a = CUBE_DIFFUSE
    h = CUBE_SIDE
    inertia = CUBE_MASS * h * h / 6.0
    return f"""<?xml version="1.0"?>
<sdf version="1.9">
  <model name="{escape(obj['name'])}">
    <pose>{x:.6g} {y:.6g} {z:.6g} 0 0 0</pose>
    <link name="link">
      <inertial>
        <mass>{CUBE_MASS}</mass>
        <inertia><ixx>{inertia:.6g}</ixx><iyy>{inertia:.6g}</iyy><izz>{inertia:.6g}</izz><ixy>0</ixy><ixz>0</ixz><iyz>0</iyz></inertia>
      </inertial>
      <collision name="collision">
        <geometry><box><size>{h} {h} {h}</size></box></geometry>
        <surface><friction><ode><mu>1.0</mu><mu2>1.0</mu2></ode></friction></surface>
      </collision>
      <visual name="visual">
        <geometry><box><size>{h} {h} {h}</size></box></geometry>
        <material><ambient>{r} {g} {b} {a}</ambient><diffuse>{r} {g} {b} {a}</diffuse></material>
      </visual>
    </link>
  </model>
</sdf>
"""


def sensors_system_request(world_entity_id: int = 1, render_engine: str = 'ogre2') -> str:
    """`gz service -s /world/<w>/entity/system/add` の --req（gz.msgs.EntityPlugin_V）。"""
    return (
        f'entity {{id: {world_entity_id}}} '
        'plugins {filename: "gz-sim-sensors-system" '
        'name: "gz::sim::systems::Sensors" '
        f'innerxml: "<render_engine>{render_engine}</render_engine>"}}'
    )
