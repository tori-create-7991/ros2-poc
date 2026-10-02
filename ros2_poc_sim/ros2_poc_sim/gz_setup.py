"""起動済みの Gazebo ワールドへカメラと物体を後付けする（公式 launch は無改変）。

手順: ワールドのサービスが立つまで待つ → Sensors システムを実行時に追加（未追加の場合）
→ カメラモデルと物体を ros_gz_sim create でスポーン。
"""
import argparse
import subprocess
import sys
import time

from ros2_poc_sim import profile as P
from ros2_poc_sim import sdf as S


def _run(cmd, timeout=60):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def wait_for_service(world: str, timeout: float) -> bool:
    want = f'/world/{world}/entity/system/add'
    end = time.time() + timeout
    while time.time() < end:
        r = _run(['gz', 'service', '-l'], timeout=15)
        if want in r.stdout:
            return True
        time.sleep(2.0)
    return False


def list_models(world: str) -> set:
    """ワールドにあるトップレベルモデル名。`scene/info` の `model { name: "..." }` から取る。"""
    r = _run(['gz', 'service', '-s', f'/world/{world}/scene/info', '--reqtype', 'gz.msgs.Empty',
              '--reptype', 'gz.msgs.Scene', '--timeout', '5000', '--req', ''], timeout=15)
    return parse_model_names(r.stdout)


def parse_model_names(text: str) -> set:
    names, depth, pending = set(), 0, False
    for raw in text.splitlines():
        line = raw.strip()
        if line.endswith('{'):
            if depth == 0 and line == 'model {':
                pending = True
            depth += 1
        elif line == '}':
            depth -= 1
        elif pending and depth == 1 and line.startswith('name:'):
            names.add(line.split('"')[1])
            pending = False
    return names


def needs_sensors_system(models: set) -> bool:
    """このスクリプトが作った `sim_camera_*` モデルが 1 つでもあれば、Sensors システムは追加済み。
    （Sensors システムの有無はサービス/トピックから判別できない。二重に追加すると描画が二重になる）"""
    return not any(m.startswith('sim_camera_') for m in models)


def add_sensors_system(world: str, world_entity_id: int) -> bool:
    r = _run(['gz', 'service', '-s', f'/world/{world}/entity/system/add',
              '--reqtype', 'gz.msgs.EntityPlugin_V', '--reptype', 'gz.msgs.Boolean',
              '--timeout', '10000', '--req', S.sensors_system_request(world_entity_id)], timeout=30)
    return 'true' in r.stdout


def spawn(world: str, name: str, sdf_path: str, pose: list) -> bool:
    # create は SDF 内の <pose> を無視して -x/-y/-z/-R/-P/-Y（既定 0）で上書きする。必ず姿勢を渡す。
    r = _run(['ros2', 'run', 'ros_gz_sim', 'create', '-world', world, '-file', sdf_path,
              '-name', name, '-allow_renaming', 'false', *pose], timeout=240)
    sys.stdout.write(r.stdout + r.stderr)
    return r.returncode == 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--camera-sdf', required=True)
    ap.add_argument('--camera-name', required=True)
    ap.add_argument('--camera-pose', nargs=6, type=float, required=True, metavar=('X', 'Y', 'Z', 'R', 'P', 'YAW'))
    ap.add_argument('--object-pose', nargs=6, type=float, metavar=('X', 'Y', 'Z', 'R', 'P', 'YAW'))
    ap.add_argument('--object-sdf', default='')
    ap.add_argument('--object-name', default='')
    ap.add_argument('--world', default='default')
    ap.add_argument('--world-entity-id', type=int, default=1)
    ap.add_argument('--wait-sec', type=float, default=180.0)
    a = ap.parse_args(argv)

    if not wait_for_service(a.world, a.wait_sec):
        print(f'ERROR: ワールド {a.world} のサービスが {a.wait_sec}s 以内に立たない（公式 launch を先に起動したか）')
        return 2
    models = list_models(a.world)
    if a.camera_name in models:
        print(f'{a.camera_name} は既にある。スポーンをスキップ')
    else:
        if needs_sensors_system(models):
            if not add_sensors_system(a.world, a.world_entity_id):
                print('ERROR: Sensors システムの追加に失敗')
                return 3
            print('Sensors システムを追加した')
            time.sleep(3.0)
        else:
            print('Sensors システムは追加済み（sim_camera_* モデルあり）')
        if not spawn(a.world, a.camera_name, a.camera_sdf, S.pose_args(a.camera_pose)):
            print('ERROR: カメラモデルのスポーンに失敗')
            return 4
    if a.object_sdf:
        if a.object_name in models:
            print(f'{a.object_name} は既にある。スキップ')
        elif not spawn(a.world, a.object_name, a.object_sdf, S.pose_args(a.object_pose)):
            print('ERROR: 物体のスポーンに失敗')
            return 5
    print('gz_setup 完了')
    return 0


if __name__ == '__main__':
    sys.exit(main())
