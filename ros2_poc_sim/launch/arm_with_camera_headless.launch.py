"""arm_with_camera.launch.py の GUI なし版（run-scenario.sh --light 用）。

公式 crane_x7_with_table.launch.py（無改変）を読み込み、その中の `gz sim -r <world> --gui-config ...`（サーバー + GUI）だけを
サーバーのみ（`gz sim -r -s <world>`）に差し替える。GUI（gz sim gui）だけを実行中に止めると、親の gz sim ごと終了して
物理サーバーも落ちるので、止めるのではなく起動時に GUI を持たせない。
RViz は move_group の launch が起動するが、止めても他のノードは生きている（run-scenario.sh が止める）。
"""
import importlib.util
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _cmd_text(entity):
    """ExecuteProcess の cmd（Substitution のリストのリスト）を、実行前の text だけで 1 つの文字列にする。"""
    return ' '.join(''.join(getattr(sub, 'text', '') for sub in part) for part in entity.cmd)


def _is_gz_sim(entity):
    # Node も ExecuteProcess の子クラスなので type で厳密に絞り、さらに cmd が gz sim であることも見る。
    # 公式の gz_sim は shell=True の素の ExecuteProcess
    return type(entity) is ExecuteProcess and _cmd_text(entity).startswith('gz sim')


def headless_gz_sim():
    """公式と同じ環境変数・ワールドで、GUI なしの gz sim を作る（公式の gz_sim の cmd と同じ前提）。"""
    env = {
        'GZ_SIM_SYSTEM_PLUGIN_PATH': os.environ.get('LD_LIBRARY_PATH', ''),
        'GZ_SIM_RESOURCE_PATH': os.path.dirname(get_package_share_directory('crane_x7_description')),
    }
    world_file = os.path.join(get_package_share_directory('crane_x7_gazebo'), 'worlds', 'table.sdf')
    # -r: 起動時にシミュレーションを開始しないとコントローラが起動しない（公式と同じ）。-s: サーバーのみ
    return ExecuteProcess(cmd=['gz sim -r -s', world_file], output='screen', additional_env=env, shell=True)


def replace_gz_sim(entities):
    """公式 launch のエンティティ列のうち、gz sim の ExecuteProcess を GUI なしのものに差し替えて返す。"""
    gz = [i for i, e in enumerate(entities) if _is_gz_sim(e)]
    if len(gz) != 1:
        # 公式 launch の構成が変わったときに、GUI 付きのまま黙って起動しない
        raise RuntimeError(f'公式 launch の gz sim を 1 つに特定できない（{len(gz)} 個）。arm_with_camera_headless を見直す')
    entities[gz[0]] = headless_gz_sim()
    return entities


def generate_launch_description():
    share = get_package_share_directory('ros2_poc_sim')
    mine_path = os.path.join(share, 'launch', 'sim_camera.launch.py')
    args = _load(mine_path, 'ros2_poc_sim_sim_camera_launch').LAUNCH_ARGS
    official = _load(os.path.join(get_package_share_directory('crane_x7_gazebo'), 'launch',
                                  'crane_x7_with_table.launch.py'), 'crane_x7_with_table_launch')
    entities = replace_gz_sim(list(official.generate_launch_description().entities))
    return LaunchDescription(
        [DeclareLaunchArgument(k, default_value=v) for k, v in args.items()] + entities + [
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource(mine_path),
                launch_arguments={k: LaunchConfiguration(k) for k in args}.items()),
        ])
