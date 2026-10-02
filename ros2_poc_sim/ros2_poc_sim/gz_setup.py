"""起動済みの Gazebo ワールドへカメラと物体を後付けする（公式 launch は無改変）。

手順: ワールドのサービスが立つまで待つ → Sensors システムを実行時に追加（未追加の場合）
→ 追加の印となるマーカーモデルを置く → カメラモデルと物体を ros_gz_sim create でスポーン。

Gazebo にはシステムの有無を問い合わせる手段が無い。そこで「Sensors システムを追加したら直後に
マーカーモデルを置く」ことを印にして二重追加を防ぐ。状態が分からない（問い合わせ失敗）ときは
推測で追加せず中止する。複数の gz_setup が同時に走ってもロックで直列化する。
"""
import argparse
import fcntl
import os
import subprocess
import sys
import tempfile
import time

from ros2_poc_sim import sdf as S

EXIT_NO_WORLD, EXIT_SENSORS, EXIT_CAMERA, EXIT_OBJECT, EXIT_UNKNOWN_STATE = 2, 3, 4, 5, 6


def _log(msg: str) -> None:
    print(f'[gz_setup {time.strftime("%H:%M:%S")}] {msg}', flush=True)


def _run(cmd, timeout=60):
    """タイムアウトを例外にせず rc=124 で返す（負荷時に gz CLI は現実に遅い）。"""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or '')
        return subprocess.CompletedProcess(cmd, 124, out, f'timeout after {timeout}s')


def wait_for_service(world: str, timeout: float) -> bool:
    want = f'/world/{world}/entity/system/add'
    start = time.time()
    last_report = start
    while time.time() - start < timeout:
        r = _run(['gz', 'service', '-l'], timeout=30)
        if want in r.stdout:
            _log(f'ワールド {world} のサービスが立った（{time.time() - start:.0f}s）')
            return True
        if time.time() - last_report >= 30:
            _log(f'ワールド待ち {time.time() - start:.0f}s / {timeout:.0f}s')
            last_report = time.time()
        time.sleep(2.0)
    return False


def list_models(world: str):
    """ワールドのトップレベルモデル名。問い合わせに失敗したら None（空集合と区別する）。"""
    r = _run(['gz', 'service', '-s', f'/world/{world}/scene/info', '--reqtype', 'gz.msgs.Empty',
              '--reptype', 'gz.msgs.Scene', '--timeout', '10000', '--req', ''], timeout=30)
    if r.returncode != 0 or 'model {' not in r.stdout:
        return None
    return parse_model_names(r.stdout)


def parse_model_names(text: str) -> set:
    names, depth, pending = set(), 0, False
    for raw in text.splitlines():
        line = raw.strip()
        if line.endswith('{'):
            if depth == 0:
                pending = (line == 'model {')
            depth += 1
        elif line == '}':
            depth -= 1
        elif pending and depth == 1 and line.startswith('name:'):
            parts = line.split('"')
            if len(parts) >= 2:
                names.add(parts[1])
            pending = False
    return names


def needs_sensors_system(models: set) -> bool:
    """マーカー（または後方互換で sim_camera_* モデル）があれば Sensors システムは追加済み。"""
    return not any(m.startswith(S.MODEL_PREFIX) for m in models)


def add_sensors_system(world: str, world_entity_id: int) -> bool:
    r = _run(['gz', 'service', '-s', f'/world/{world}/entity/system/add',
              '--reqtype', 'gz.msgs.EntityPlugin_V', '--reptype', 'gz.msgs.Boolean',
              '--timeout', '20000', '--req', S.sensors_system_request(world_entity_id)], timeout=60)
    return r.returncode == 0 and 'data: true' in r.stdout


def spawn(world: str, name: str, sdf_path: str, pose: list, timeout: float) -> bool:
    # create は SDF 内の <pose> を無視して -x/-y/-z/-R/-P/-Y（既定 0）で上書きする。必ず姿勢を渡す。
    r = _run(['ros2', 'run', 'ros_gz_sim', 'create', '-world', world, '-file', sdf_path,
              '-name', name, '-allow_renaming', 'false', *pose], timeout=timeout)
    sys.stdout.write(r.stdout + r.stderr)
    return r.returncode == 0


def _write_temp_sdf(text: str) -> str:
    fd, path = tempfile.mkstemp(prefix='ros2_poc_sim_', suffix='.sdf')
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(text)
    return path


def run_setup(a) -> int:
    t0 = time.time()
    if not wait_for_service(a.world, a.wait_sec):
        _log(f'ERROR: ワールド {a.world} のサービスが {a.wait_sec:.0f}s 以内に立たない（公式 launch を先に起動したか）')
        return EXIT_NO_WORLD
    models = list_models(a.world)
    if models is None:
        _log('ERROR: ワールドの状態を取得できない（gz が重い可能性）。推測で Sensors を追加しないので中止')
        return EXIT_UNKNOWN_STATE
    if a.camera_name in models:
        _log(f'{a.camera_name} は既にある。スポーンをスキップ')
    else:
        if needs_sensors_system(models):
            if not add_sensors_system(a.world, a.world_entity_id):
                # 応答が無くても追加できている可能性がある。再追加で二重にしないため、ここで止める。
                _log('ERROR: Sensors システムの追加を確認できない（追加済みかもしれない）。Gazebo を再起動して再実行する')
                return EXIT_SENSORS
            _log('Sensors システムを追加した')
            marker = _write_temp_sdf(S.sensors_marker_sdf())
            if not spawn(a.world, S.SENSORS_MARKER_NAME, marker, S.pose_args((0, 0, -100, 0, 0, 0)),
                         a.spawn_timeout):
                _log('ERROR: マーカーのスポーンに失敗。次回の再実行で Sensors が二重になる恐れがある。Gazebo を再起動する')
                return EXIT_SENSORS
            time.sleep(3.0)
        else:
            _log('Sensors システムは追加済み（マーカーまたは sim_camera_* モデルあり）')
        _log('カメラモデルをスポーン')
        if not spawn(a.world, a.camera_name, a.camera_sdf, S.pose_args(a.camera_pose), a.spawn_timeout):
            _log('ERROR: カメラモデルのスポーンに失敗（Sensors は追加済みなので再実行は安全）')
            return EXIT_CAMERA
    if a.object_sdf:
        if a.object_name in models:
            _log(f'{a.object_name} は既にある。スキップ')
        elif not spawn(a.world, a.object_name, a.object_sdf, S.pose_args(a.object_pose), a.spawn_timeout):
            _log('ERROR: 物体のスポーンに失敗')
            return EXIT_OBJECT
    _log(f'完了（{time.time() - t0:.0f}s）')
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--camera-sdf', required=True)
    ap.add_argument('--camera-name', required=True)
    ap.add_argument('--camera-pose', nargs=6, type=float, required=True,
                    metavar=('X', 'Y', 'Z', 'R', 'P', 'YAW'))
    ap.add_argument('--object-pose', nargs=6, type=float, metavar=('X', 'Y', 'Z', 'R', 'P', 'YAW'))
    ap.add_argument('--object-sdf', default='')
    ap.add_argument('--object-name', default='')
    ap.add_argument('--world', default='default')
    ap.add_argument('--world-entity-id', type=int, default=1)
    ap.add_argument('--wait-sec', type=float, default=300.0)
    ap.add_argument('--spawn-timeout', type=float, default=240.0)
    a = ap.parse_args(argv)

    lock_dir = os.path.join(tempfile.gettempdir(), f'ros2_poc_sim_{os.getuid()}')
    os.makedirs(lock_dir, mode=0o700, exist_ok=True)
    with open(os.path.join(lock_dir, f'gz_setup_{a.world}.lock'), 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)   # 複数プロファイルを同時に起動しても直列化する
        return run_setup(a)


if __name__ == '__main__':
    sys.exit(main())
