"""公式 crane_x7_with_table.launch.py（無改変）の起動後に、仮想カメラと物体を後付けする。

  ros2 launch ros2_poc_sim sim_camera.launch.py profile:=realsense_d435 placement:=fixed_near_top

Gazebo 側（Sensors システム・モデル）は何度実行しても二重にならない。bridge / adapter は
ROS ノードなので、同じプロファイルを動かしたまま再実行しないこと（先に Ctrl-C する）。
gz_setup が失敗したときは、**このプロファイルのノード（bridge / adapter / republish）だけを止める**。
公式の Gazebo / MoveIt は止めない（`fail_fast:=true` なら launch 全体を止める）。止めた後は
`ros2 launch ros2_poc_sim sim_camera.launch.py ...` を単独で再実行すればよい（二重にならない）。
別のプロファイルは同時に動かせる（ノード名・TF・トピックはプロファイルごとに分かれる）。
"""
import sys

from launch import LaunchDescription
from launch.actions import (DeclareLaunchArgument, EmitEvent, ExecuteProcess, LogInfo,
                            OpaqueFunction, RegisterEventHandler)
from launch.events import Shutdown
from launch.events.process import ShutdownProcess
from launch.event_handlers import OnProcessExit
from launch.events import matches_action
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

from ros2_poc_sim import bridge_config as B
from ros2_poc_sim import gz_setup as Z
from ros2_poc_sim import paths
from ros2_poc_sim import profile as P
from ros2_poc_sim import sdf as S


def _positive_float(v: str, label: str) -> float:
    try:
        x = float(v)
    except ValueError:
        x = float('nan')
    if not (x > 0 and x < float('inf')):
        raise RuntimeError(f'{label} は正の数: {v!r}')
    return x


def _truthy(v: str) -> bool:
    return str(v).lower() in ('1', 'true', 'yes', 'on')


def _setup(context):
    profile_arg = LaunchConfiguration('profile').perform(context)
    placement_arg = LaunchConfiguration('placement').perform(context)
    prof = P.load_profile(profile_arg)
    pl = P.load_placement(placement_arg)
    pointcloud = _truthy(LaunchConfiguration('pointcloud').perform(context))
    compressed = _truthy(LaunchConfiguration('compressed').perform(context))
    spawn_object = _truthy(LaunchConfiguration('spawn_object').perform(context))
    ns_mode = P.check_ns_mode(LaunchConfiguration('ns_mode').perform(context))
    world = LaunchConfiguration('world').perform(context)
    if not P.is_name(world):
        raise RuntimeError(f'world は英数字とアンダースコアのみ: {world!r}')
    wait_sec = _positive_float(LaunchConfiguration('wait_sec').perform(context), 'wait_sec')
    spawn_timeout = _positive_float(LaunchConfiguration('spawn_timeout').perform(context), 'spawn_timeout')
    fail_fast = _truthy(LaunchConfiguration('fail_fast').perform(context))
    name = prof['name']

    out_dir = paths.private_dir(name)
    cam_sdf, obj_sdf, br_yaml = (f'{out_dir}/camera.sdf', f'{out_dir}/object.sdf', f'{out_dir}/bridge.yaml')
    for path, text in ((cam_sdf, S.camera_model_sdf(prof, pl)), (obj_sdf, S.object_model_sdf(pl)),
                       (br_yaml, B.bridge_yaml(prof, pointcloud))):
        with open(path, 'w', encoding='utf-8') as f:
            f.write(text)

    sim_time = {'use_sim_time': True}
    setup_cmd = [sys.executable, '-m', 'ros2_poc_sim.gz_setup', '--camera-sdf', cam_sdf,
                 '--camera-name', S.camera_model_name(prof), '--world', world,
                 f'--wait-sec={wait_sec}', f'--spawn-timeout={spawn_timeout}',
                 f'--camera-pose={S.format_pose(S.camera_pose6(prof, pl))}']
    if spawn_object:
        setup_cmd += ['--object-sdf', obj_sdf, '--object-name', pl['object']['name'],
                      f'--object-pose={S.format_pose(S.object_pose6(pl))}']
    gz_setup = ExecuteProcess(cmd=setup_cmd, output='screen', name=f'gz_setup_{name}')

    camera_nodes = []   # このプロファイルのノード。gz_setup が失敗したらこれだけを止める
    camera_nodes.append(Node(
        package='ros_gz_bridge', executable='parameter_bridge', name=f'sim_camera_bridge_{name}',
        parameters=[{'config_file': br_yaml}, sim_time], output='screen',
        respawn=True, respawn_delay=2.0))

    # adapter にはユーザーが渡した値（名前またはパス）をそのまま渡す。名前だけだと別パスのプロファイルを再解決できない。
    camera_nodes.append(Node(
        package='ros2_poc_sim', executable='camera_adapter', name=f'camera_adapter_{name}',
        parameters=[{'profile': profile_arg, 'placement': placement_arg, 'pointcloud': pointcloud,
                     'ns_mode': ns_mode}, sim_time], output='screen',
        respawn=True, respawn_delay=2.0))

    if compressed:
        for sid in P.compressed_streams(prof):
            topic = P.topic_name(prof, sid, ns_mode or None)
            # Jazzy の republish は位置引数が効かない。圧縮出力の remap は out/compressed 単位。
            camera_nodes.append(Node(
                package='image_transport', executable='republish', name=f'rep_{name}_{sid}',
                parameters=[{'in_transport': 'raw', 'out_transport': 'compressed'}, sim_time],
                remappings=[('in', topic), ('out/compressed', f'{topic}/compressed')],
                output='screen'))

    def _on_setup_exit(event, _context):
        if event.returncode == 0 or event.returncode < 0 or event.returncode in (130, 143):
            return []   # 成功、またはユーザーの停止操作（Ctrl-C / SIGTERM）。失敗の案内は出さない
        msg = (f'[ros2_poc_sim] gz_setup が失敗した（rc={event.returncode}）。仮想カメラはまだ無い。'
               '終了コードの意味と対処は docs/sim-camera-profile.md の表を参照')
        if fail_fast:
            return [LogInfo(msg=msg + '。fail_fast=true なので launch 全体を止める'),
                    EmitEvent(event=Shutdown(reason='gz_setup failed'))]
        # 既定: このプロファイルのノードだけを止める。公式の Gazebo / MoveIt は止めない。
        # 止めてから sim_camera.launch.py を単独で再実行すれば、ノードが二重にならない
        return ([LogInfo(msg=msg + f'。{name} の bridge / adapter / republish を止める（公式スタックは動いたまま）。'
                                   'sim_camera.launch.py を再実行する（Gazebo の再起動が要る場合はそう案内が出ている）')]
                + [EmitEvent(event=ShutdownProcess(process_matcher=matches_action(n))) for n in camera_nodes])

    actions = [gz_setup,
               RegisterEventHandler(OnProcessExit(target_action=gz_setup, on_exit=_on_setup_exit))]
    actions += camera_nodes
    return actions


LAUNCH_ARGS = {
    'profile': 'realsense_d435',
    'placement': 'fixed_near_top',
    'pointcloud': 'false',
    'compressed': 'true',
    'ns_mode': '',
    'spawn_object': 'true',
    'world': 'default',
    'wait_sec': '300',
    'spawn_timeout': '240',
    'fail_fast': 'false',
}


def generate_launch_description():
    return LaunchDescription(
        [DeclareLaunchArgument(k, default_value=v) for k, v in LAUNCH_ARGS.items()]
        + [OpaqueFunction(function=_setup)])
