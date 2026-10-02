"""公式 crane_x7_with_table.launch.py（無改変）の起動後に、仮想カメラと物体を後付けする。

  ros2 launch ros2_poc_sim sim_camera.launch.py profile:=realsense_d435 placement:=fixed_near_top
"""
import os
import sys

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess, OpaqueFunction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

from ros2_poc_sim import bridge_config as B
from ros2_poc_sim import profile as P
from ros2_poc_sim import sdf as S


def _truthy(v: str) -> bool:
    return str(v).lower() in ('1', 'true', 'yes', 'on')


def _setup(context):
    prof = P.load_profile(LaunchConfiguration('profile').perform(context))
    pl = P.load_placement(LaunchConfiguration('placement').perform(context))
    pointcloud = _truthy(LaunchConfiguration('pointcloud').perform(context))
    compressed = _truthy(LaunchConfiguration('compressed').perform(context))
    spawn_object = _truthy(LaunchConfiguration('spawn_object').perform(context))
    ns_mode = LaunchConfiguration('ns_mode').perform(context)
    world = LaunchConfiguration('world').perform(context)

    out_dir = f"/tmp/ros2_poc_sim/{prof['name']}"
    os.makedirs(out_dir, exist_ok=True)
    cam_sdf, obj_sdf, br_yaml = (f'{out_dir}/camera.sdf', f'{out_dir}/object.sdf', f'{out_dir}/bridge.yaml')
    with open(cam_sdf, 'w', encoding='utf-8') as f:
        f.write(S.camera_model_sdf(prof, pl))
    with open(obj_sdf, 'w', encoding='utf-8') as f:
        f.write(S.object_model_sdf(pl))
    with open(br_yaml, 'w', encoding='utf-8') as f:
        f.write(B.bridge_yaml(prof, pointcloud))

    sim_time = {'use_sim_time': True}
    actions = []
    setup_cmd = [sys.executable, '-m', 'ros2_poc_sim.gz_setup', '--camera-sdf', cam_sdf,
                 '--camera-name', f"sim_camera_{prof['name']}", '--world', world,
                 '--camera-pose', *[repr(v) for v in S.camera_pose6(prof, pl)]]
    if spawn_object:
        setup_cmd += ['--object-sdf', obj_sdf, '--object-name', pl['object']['name'],
                      '--object-pose', *[repr(v) for v in S.object_pose6(pl)]]
    actions.append(ExecuteProcess(cmd=setup_cmd, output='screen', name='gz_setup'))

    actions.append(Node(
        package='ros_gz_bridge', executable='parameter_bridge', name='sim_camera_bridge',
        parameters=[{'config_file': br_yaml}, sim_time], output='screen'))

    actions.append(Node(
        package='ros2_poc_sim', executable='camera_adapter', name='camera_adapter',
        parameters=[{'profile': prof['name'], 'placement': pl['name'], 'pointcloud': pointcloud,
                     'ns_mode': ns_mode}, sim_time], output='screen'))

    if compressed:
        for sid in prof.get('compressed', []):
            topic = P.topic_name(prof, sid, ns_mode or None)
            # Jazzy の republish は位置引数が効かない。圧縮出力の remap は out/compressed 単位。
            actions.append(Node(
                package='image_transport', executable='republish', name=f'rep_{sid}',
                parameters=[{'in_transport': 'raw', 'out_transport': 'compressed'}, sim_time],
                remappings=[('in', topic), ('out/compressed', f'{topic}/compressed')],
                output='screen'))
    return actions


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument('profile', default_value='realsense_d435'),
        DeclareLaunchArgument('placement', default_value='fixed_near_top'),
        DeclareLaunchArgument('pointcloud', default_value='false'),
        DeclareLaunchArgument('compressed', default_value='true'),
        DeclareLaunchArgument('ns_mode', default_value='',
                              description='flat | nested（空ならプロファイルの既定）'),
        DeclareLaunchArgument('spawn_object', default_value='true'),
        DeclareLaunchArgument('world', default_value='default'),
        OpaqueFunction(function=_setup),
    ])
