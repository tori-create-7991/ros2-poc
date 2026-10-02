"""公式 crane_x7_with_table.launch.py（無改変）の起動後に、仮想カメラと物体を後付けする。

  ros2 launch ros2_poc_sim sim_camera.launch.py profile:=realsense_d435 placement:=fixed_near_top

Gazebo 側（Sensors システム・モデル）は何度実行しても二重にならない。bridge / adapter は
ROS ノードなので、同じプロファイルを動かしたまま再実行しないこと（先に Ctrl-C する）。
別のプロファイルは同時に動かせる（ノード名・TF・トピックはプロファイルごとに分かれる）。
"""
import os
import stat
import sys

from launch import LaunchDescription
from launch.actions import (DeclareLaunchArgument, EmitEvent, ExecuteProcess, LogInfo,
                            OpaqueFunction, RegisterEventHandler)
from launch.event_handlers import OnProcessExit
from launch.events import Shutdown
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

from ros2_poc_sim import bridge_config as B
from ros2_poc_sim import profile as P
from ros2_poc_sim import sdf as S


def _truthy(v: str) -> bool:
    return str(v).lower() in ('1', 'true', 'yes', 'on')


def _private_dir(name: str, base: str = None) -> str:
    """生成物（SDF・ブリッジ YAML）の置き場。他ユーザーに差し替えられないよう 0700・実体ディレクトリ・自分の所有に限る。"""
    base = base or f'/tmp/ros2_poc_sim_{os.getuid()}'
    os.makedirs(base, mode=0o700, exist_ok=True)
    path = os.path.join(base, name)
    os.makedirs(path, mode=0o700, exist_ok=True)
    for d in (base, path):
        st = os.lstat(d)
        if stat.S_ISLNK(st.st_mode) or st.st_uid != os.getuid():
            raise RuntimeError(f'{d} がシンボリックリンクか他人の所有: 作り直してください')
        os.chmod(d, 0o700)
    return path


def _setup(context):
    profile_arg = LaunchConfiguration('profile').perform(context)
    prof = P.load_profile(profile_arg)
    pl = P.load_placement(LaunchConfiguration('placement').perform(context))
    pointcloud = _truthy(LaunchConfiguration('pointcloud').perform(context))
    compressed = _truthy(LaunchConfiguration('compressed').perform(context))
    spawn_object = _truthy(LaunchConfiguration('spawn_object').perform(context))
    ns_mode = LaunchConfiguration('ns_mode').perform(context)
    world = LaunchConfiguration('world').perform(context)
    name = prof['name']

    out_dir = _private_dir(name)
    cam_sdf, obj_sdf, br_yaml = (f'{out_dir}/camera.sdf', f'{out_dir}/object.sdf', f'{out_dir}/bridge.yaml')
    for path, text in ((cam_sdf, S.camera_model_sdf(prof, pl)), (obj_sdf, S.object_model_sdf(pl)),
                       (br_yaml, B.bridge_yaml(prof, pointcloud))):
        with open(path, 'w', encoding='utf-8') as f:
            f.write(text)

    sim_time = {'use_sim_time': True}
    setup_cmd = [sys.executable, '-m', 'ros2_poc_sim.gz_setup', '--camera-sdf', cam_sdf,
                 '--camera-name', S.camera_model_name(prof), '--world', world,
                 '--wait-sec', LaunchConfiguration('wait_sec').perform(context),
                 '--spawn-timeout', LaunchConfiguration('spawn_timeout').perform(context),
                 '--camera-pose', *[repr(v) for v in S.camera_pose6(prof, pl)]]
    if spawn_object:
        setup_cmd += ['--object-sdf', obj_sdf, '--object-name', pl['object']['name'],
                      '--object-pose', *[repr(v) for v in S.object_pose6(pl)]]
    gz_setup = ExecuteProcess(cmd=setup_cmd, output='screen', name=f'gz_setup_{name}')

    def _on_setup_exit(event, _context):
        if event.returncode != 0:
            return [LogInfo(msg=f'[ros2_poc_sim] gz_setup が失敗した（rc={event.returncode}）。'
                                'bridge / adapter を止める。ログを確認して再実行する'),
                    EmitEvent(event=Shutdown(reason='gz_setup failed'))]
        return []

    actions = [gz_setup,
               RegisterEventHandler(OnProcessExit(target_action=gz_setup, on_exit=_on_setup_exit))]

    actions.append(Node(
        package='ros_gz_bridge', executable='parameter_bridge', name=f'sim_camera_bridge_{name}',
        parameters=[{'config_file': br_yaml}, sim_time], output='screen'))

    # adapter にはユーザーが渡した値（名前またはパス）をそのまま渡す。名前だけだと別パスのプロファイルを再解決できない。
    actions.append(Node(
        package='ros2_poc_sim', executable='camera_adapter', name=f'camera_adapter_{name}',
        parameters=[{'profile': profile_arg, 'placement': pl['name'], 'pointcloud': pointcloud,
                     'ns_mode': ns_mode}, sim_time], output='screen'))

    if compressed:
        for sid in prof.get('compressed', []):
            topic = P.topic_name(prof, sid, ns_mode or None)
            # Jazzy の republish は位置引数が効かない。圧縮出力の remap は out/compressed 単位。
            actions.append(Node(
                package='image_transport', executable='republish', name=f'rep_{name}_{sid}',
                parameters=[{'in_transport': 'raw', 'out_transport': 'compressed'}, sim_time],
                remappings=[('in', topic), ('out/compressed', f'{topic}/compressed')],
                output='screen'))
    return actions


LAUNCH_ARGS = {
    'profile': 'realsense_d435',
    'placement': 'fixed_near_top',
    'pointcloud': 'false',
    'compressed': 'true',
    'ns_mode': '',
    'spawn_object': 'true',
    'world': 'default',
    'wait_sec': '180',
    'spawn_timeout': '240',
}


def generate_launch_description():
    return LaunchDescription(
        [DeclareLaunchArgument(k, default_value=v) for k, v in LAUNCH_ARGS.items()]
        + [OpaqueFunction(function=_setup)])
