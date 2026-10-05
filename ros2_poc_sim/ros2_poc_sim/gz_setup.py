"""起動済みの Gazebo ワールドへカメラと物体を後付けする（公式 launch は無改変）。

手順: ワールドのサービスが立つまで待つ → Sensors システムを実行時に追加（未追加の場合）
→ 追加の印となるマーカーモデルを置く → カメラモデルと物体を ros_gz_sim create でスポーン。

Gazebo にはシステムの有無を問い合わせる手段が無い。そこで「Sensors システムを追加したら直後に
マーカーモデルを置く」ことを印にして二重追加を防ぐ。状態が分からない（問い合わせ失敗）ときは
推測で追加せず中止する。複数の gz_setup が同時に走ってもロックで直列化する。

終了コード: 0 成功 / 2 ワールドが立たない / 3 Sensors 追加またはマーカー失敗（Gazebo の再起動を推奨）
 / 4 カメラのスポーン失敗 / 5 物体のスポーン失敗 / 6 ワールドの状態を取得できない / 7 他の gz_setup を待ちきれない
 / 64 コマンドラインの誤り。 2, 4, 5, 6, 7 は Gazebo の状態を壊していないので、そのまま再実行してよい。
"""
import argparse
import fcntl
import math
import os
import signal
import subprocess
import sys
import tempfile
import time

from ros2_poc_sim import paths
from ros2_poc_sim import profile as P
from ros2_poc_sim import sdf as S

(EXIT_NO_WORLD, EXIT_SENSORS, EXIT_CAMERA, EXIT_OBJECT,
 EXIT_UNKNOWN_STATE, EXIT_LOCK_TIMEOUT) = 2, 3, 4, 5, 6, 7
EXIT_USAGE = 64   # argparse の終了コード 2 は「ワールドが立たない」と区別できないので別番号にする


def _log(msg: str) -> None:
    print(f'[gz_setup {time.strftime("%H:%M:%S")}] {msg}', flush=True)


def _kill_group(proc) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        pass


def _run(cmd, timeout=60):
    """タイムアウトや実行失敗を例外にせず rc で返す（負荷時に gz CLI は現実に遅い）。
    子は別セッションで動かし、タイムアウト・中断（Ctrl-C / SIGTERM）では子孫プロセスごと止める
    （`ros2 run` の下の create を孤児にしない）。"""
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                start_new_session=True)
    except OSError as exc:
        return subprocess.CompletedProcess(cmd, 127, '', f'{cmd[0]}: {exc}')
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        try:
            out, err = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:   # setsid した孫がパイプを握っていても固まらない
            out, err = '', ''
        return subprocess.CompletedProcess(cmd, 124, out or '', f'timeout after {timeout}s')
    except BaseException:
        _kill_group(proc)
        raise
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def _tail(text: str, n: int = 200) -> str:
    return (text or '').strip().replace('\n', ' ')[-n:]


def wait_for_service(world: str, timeout: float):
    """ワールドのサービスが立つまで待つ。立てば (True, '')、立たなければ (False, 最後の失敗の要約)。"""
    want = f'/world/{world}/entity/system/add'
    start = time.time()
    last_report = start
    last = ''
    while time.time() - start < timeout:
        r = _run(['gz', 'service', '-l'], timeout=min(30, max(5, timeout - (time.time() - start))))
        if want in r.stdout:
            _log(f'ワールド {world} のサービスが立った（{time.time() - start:.0f}s）')
            return True, ''
        if r.returncode != 0:
            last = f'rc={r.returncode} {_tail(r.stderr)}'
        if time.time() - last_report >= 30:
            _log(f'ワールド待ち {time.time() - start:.0f}s / {timeout:.0f}s'
                 + (f'（最後の gz の失敗: {last}）' if last else ''))
            last_report = time.time()
        time.sleep(2.0)
    return False, last


def list_models(world: str):
    """ワールドのトップレベルモデル名。問い合わせに失敗したら None（空集合と区別する）。"""
    r = _run(['gz', 'service', '-s', f'/world/{world}/scene/info', '--reqtype', 'gz.msgs.Empty',
              '--reptype', 'gz.msgs.Scene', '--timeout', '10000', '--req', ''], timeout=30)
    if r.returncode != 0 or 'name:' not in r.stdout:
        return None   # 取得失敗（モデルが 0 個でもワールド名やライトの name: は返る）
    return parse_model_names(r.stdout)


def list_models_retry(world: str, budget: float):
    """サービスが立った直後は scene/info が未登録のことがあるので、予算内でリトライする。"""
    end = time.time() + budget
    while True:
        models = list_models(world)
        if models is not None or time.time() >= end:
            return models
        time.sleep(2.0)


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
    """マーカー（または sim_camera_* モデル）があれば Sensors システムは追加済み。"""
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
    sys.stdout.write((r.stdout or '') + (r.stderr or ''))
    if r.returncode == 124:
        _log(f'{name} のスポーンがタイムアウト（Gazebo 側では後から完了するかもしれない）')
    return r.returncode == 0


def wait_for_model(world: str, name: str, budget: float) -> bool:
    """スポーン要求が受理されてから、モデルが scene/info に現れるまで待つ（RTF が低いと遅れる）。"""
    end = time.time() + budget
    while time.time() < end:
        models = list_models(world)
        if models is not None and name in models:
            return True
        time.sleep(1.0)
    return False


def spawn_and_confirm(world: str, name: str, sdf_path: str, pose: list, timeout: float,
                      retries: int = 0) -> bool:
    """スポーンして、モデルが scene/info に現れたことを成功の基準にする。
    create がタイムアウトや非ゼロで終わっても Gazebo 側で後から完了することがあるので、
    失敗扱いにする前に現れるのを（短く）待つ。現れなければ retries 回だけ再スポーンする。"""
    for attempt in range(retries + 1):
        accepted = spawn(world, name, sdf_path, pose, timeout)
        # 受理されたなら RTF が低くても反映を待つ。受理されなかった場合も遅れて完了する余地があるので短く待つ
        if wait_for_model(world, name, timeout if accepted else min(15.0, timeout)):
            return True
        if attempt < retries:
            _log(f'{name} が現れない。再スポーン（{attempt + 1}/{retries}）')
    return False


def _write_temp_sdf(text: str) -> str:
    fd, path = tempfile.mkstemp(prefix='ros2_poc_sim_', suffix='.sdf', dir=paths.private_dir())
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(text)
    return path


def run_setup(a) -> int:
    t0 = time.time()
    ok, last = wait_for_service(a.world, a.wait_sec)
    if not ok:
        _log(f'ERROR: ワールド {a.world} のサービスが {a.wait_sec:.0f}s 以内に立たない。'
             '公式 launch を先に起動したか、Gazebo の起動が遅い（初回の Fuel 取得・高負荷）。'
             f'GZ_PARTITION={os.environ.get("GZ_PARTITION", "(未設定)")} GZ_IP={os.environ.get("GZ_IP", "(未設定)")}'
             + (f' 最後の gz の失敗: {last}' if last else ''))
        return EXIT_NO_WORLD
    models = list_models_retry(a.world, min(60.0, a.wait_sec))
    if models is None:
        _log('ERROR: ワールドの状態を取得できない（gz が重い可能性）。推測で Sensors を追加しないので中止。そのまま再実行してよい')
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
            try:
                visible = spawn_and_confirm(a.world, S.SENSORS_MARKER_NAME, marker,
                                            S.pose_args((0, 0, -100, 0, 0, 0)), a.spawn_timeout, retries=1)
            finally:
                os.unlink(marker)
            # 次の gz_setup が確実にマーカーを見られるよう、現れるまで待ってからロックを手放す
            if not visible:
                _log('ERROR: マーカーを確認できない。次の再実行で Sensors が二重になる恐れがあるので、Gazebo を再起動する'
                     '（この間に Ctrl-C / SIGTERM した場合も同様）')
                return EXIT_SENSORS
        else:
            _log('Sensors システムは追加済み（マーカーまたは sim_camera_* モデルあり）')
        _log('カメラモデルをスポーン')
        if not spawn_and_confirm(a.world, a.camera_name, a.camera_sdf, S.pose_args(a.camera_pose),
                                 a.spawn_timeout, retries=0):
            _log('ERROR: カメラモデルのスポーンに失敗（Sensors は追加済みなので再実行は安全）')
            return EXIT_CAMERA
    if a.object_sdf:
        if a.object_name in models:
            _log(f'{a.object_name} は既にある。スキップ')
        elif not spawn_and_confirm(a.world, a.object_name, a.object_sdf, S.pose_args(a.object_pose),
                                   a.spawn_timeout, retries=0):
            _log('ERROR: 物体のスポーンに失敗（カメラは作成済み。再実行は安全）')
            return EXIT_OBJECT
    _log(f'完了（{time.time() - t0:.0f}s）')
    return 0


def _positive_finite(text: str) -> float:
    x = float(text)
    if not (math.isfinite(x) and x > 0):
        raise argparse.ArgumentTypeError('正の有限な数')
    return x


def worst_case_seconds(wait_sec: float, spawn_timeout: float) -> float:
    """先行する gz_setup の最悪経路の見積もり [秒]（ロック待ちの上限の根拠）。
    世界待ち（最後の gz 呼び出し分 +30）→ 状態取得のリトライ（60 + 30）→ マーカー（スポーン + 可視化待ち を最大 2 回）
    → カメラ・物体（それぞれ スポーン + 可視化待ち）。余裕を 60 秒足す。"""
    one = 2 * spawn_timeout           # create の待ち + モデルが現れるまでの待ち
    return (wait_sec + 30) + 90 + 2 * one + one + one + 60


def _pose(text: str):
    """`x,y,z,roll,pitch,yaw`。`--opt=...` の形で渡す（Python 3.12 の argparse は
    `-1e-05` のような負の指数表記を別オプションと誤認して nargs が失敗する）。"""
    vals = [float(v) for v in text.split(',')]
    if len(vals) != 6 or not all(math.isfinite(v) for v in vals):
        raise argparse.ArgumentTypeError('x,y,z,roll,pitch,yaw の有限な 6 要素')
    return vals


format_pose = S.format_pose


def build_parser():
    ap = argparse.ArgumentParser()
    ap.add_argument('--camera-sdf', required=True)
    ap.add_argument('--camera-name', required=True)
    ap.add_argument('--camera-pose', type=_pose, required=True, metavar='X,Y,Z,R,P,YAW')
    ap.add_argument('--object-pose', type=_pose, metavar='X,Y,Z,R,P,YAW')
    ap.add_argument('--object-sdf', default='')
    ap.add_argument('--object-name', default='')
    ap.add_argument('--world', default='default')
    ap.add_argument('--world-entity-id', type=int, default=1)
    ap.add_argument('--wait-sec', type=_positive_finite, default=300.0)
    ap.add_argument('--spawn-timeout', type=_positive_finite, default=240.0)
    return ap


def parse_args(argv=None):
    ap = build_parser()
    a = ap.parse_args(argv)
    if not P.is_name(a.world):
        ap.error(f'--world は英数字とアンダースコアのみ: {a.world!r}')
    if a.object_sdf and (a.object_pose is None or not a.object_name):
        ap.error('--object-sdf には --object-pose と --object-name が要る')
    return a


def acquire_lock(world: str, timeout: float):
    """他の gz_setup が走っている間は待つ（ログを出し、上限で諦める）。ロックを返す。None なら時間切れ。"""
    lock = open(os.path.join(paths.private_dir(), f'gz_setup_{world}.lock'), 'a')
    start = time.time()
    last = start
    while True:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return lock
        except BlockingIOError:
            if time.time() - start > timeout:
                lock.close()
                return None
            if time.time() - last >= 30:
                _log(f'他の gz_setup の完了待ち {time.time() - start:.0f}s')
                last = time.time()
            time.sleep(1.0)


def main(argv=None):
    try:
        a = parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code in (0, None) else EXIT_USAGE
    # SIGTERM（launch の停止）でも finally とロックの解放が走るよう、例外に変える。終わったら元に戻す
    prev = signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(SystemExit(143)))
    try:
        lock = acquire_lock(a.world, worst_case_seconds(a.wait_sec, a.spawn_timeout))   # 複数プロファイルを同時に起動しても直列化する
        if lock is None:
            _log('ERROR: 他の gz_setup が終わらない。先行のログを確認する')
            return EXIT_LOCK_TIMEOUT
        with lock:
            return run_setup(a)
    except KeyboardInterrupt:
        _log('中断された（Ctrl-C）。Sensors の追加直後から目印の確認までの間なら、Gazebo を再起動する')
        return 130
    except SystemExit as exc:
        if exc.code == 143:
            _log('停止された（SIGTERM）。Sensors の追加直後から目印の確認までの間なら、Gazebo を再起動する')
        raise
    finally:
        signal.signal(signal.SIGTERM, prev)


if __name__ == '__main__':
    sys.exit(main())
