"""公式 crane_x7_with_table.launch.py（無改変）と sim_camera.launch.py をまとめて起動する。"""
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration


def generate_launch_description():
    official = os.path.join(get_package_share_directory('crane_x7_gazebo'),
                            'launch', 'crane_x7_with_table.launch.py')
    mine = os.path.join(get_package_share_directory('ros2_poc_sim'),
                        'launch', 'sim_camera.launch.py')
    args = ['profile', 'placement', 'pointcloud', 'compressed', 'ns_mode', 'spawn_object']
    defaults = {'profile': 'realsense_d435', 'placement': 'fixed_near_top', 'pointcloud': 'false',
                'compressed': 'true', 'ns_mode': '', 'spawn_object': 'true'}
    return LaunchDescription(
        [DeclareLaunchArgument(a, default_value=defaults[a]) for a in args] + [
            IncludeLaunchDescription(PythonLaunchDescriptionSource(official)),
            # gz_setup がワールドのサービスを待つので、公式 launch の直後に起動してよい
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource(mine),
                launch_arguments={a: LaunchConfiguration(a) for a in args}.items()),
        ])
