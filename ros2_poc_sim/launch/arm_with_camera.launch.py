"""公式 crane_x7_with_table.launch.py（無改変）と sim_camera.launch.py をまとめて起動する。"""
import importlib.util
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration


def _camera_launch_args():
    # 既定値は sim_camera.launch.py の LAUNCH_ARGS を唯一の定義にする（重複させるとずれる）
    path = os.path.join(get_package_share_directory('ros2_poc_sim'), 'launch', 'sim_camera.launch.py')
    spec = importlib.util.spec_from_file_location('ros2_poc_sim_sim_camera_launch', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return path, mod.LAUNCH_ARGS


def generate_launch_description():
    official = os.path.join(get_package_share_directory('crane_x7_gazebo'),
                            'launch', 'crane_x7_with_table.launch.py')
    mine, args = _camera_launch_args()
    return LaunchDescription(
        [DeclareLaunchArgument(k, default_value=v) for k, v in args.items()] + [
            IncludeLaunchDescription(PythonLaunchDescriptionSource(official)),
            # gz_setup がワールドのサービスを待つので、公式 launch の直後に起動してよい
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource(mine),
                launch_arguments={k: LaunchConfiguration(k) for k in args}.items()),
        ])
