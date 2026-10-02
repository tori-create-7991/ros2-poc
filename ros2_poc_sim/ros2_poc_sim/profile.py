"""カメラプロファイル（YAML）の読み込み・検証。

プロファイルは「どの gz ストリームを、どの名前・型・エンコーディング・フレームで出すか」を宣言する。
CameraInfo は gz が描画できる理想ピンホールに限る（描画と CameraInfo を一致させるため）。
"""
import math
from pathlib import Path

import yaml

from ros2_poc_sim.paths import config_dir

SUPPORTED_TRANSFORMS = {
    'passthrough': None,
    'depth_to_16uc1_mm': '16UC1',
    'points_to_optical': None,
}
SOURCES = {'color', 'depth', 'points', 'info_color', 'info_depth'}
GZ_TYPES = {'camera', 'rgbd_camera'}
REQUIRED_TOP = ('schema', 'name', 'based_on', 'sensor', 'intrinsics', 'frames', 'tf', 'topics', 'qos')


class ProfileError(ValueError):
    pass


def _load_yaml(path: Path) -> dict:
    with open(path, encoding='utf-8') as f:
        return yaml.safe_load(f)


def load_profile(name_or_path: str) -> dict:
    p = Path(name_or_path)
    if not (p.suffix in ('.yaml', '.yml') and p.exists()):
        p = config_dir('profiles') / f'{name_or_path}.yaml'
    data = _load_yaml(p)
    validate(data)
    return data


def load_placement(name_or_path: str) -> dict:
    p = Path(name_or_path)
    if not (p.suffix in ('.yaml', '.yml') and p.exists()):
        p = config_dir('placements') / f'{name_or_path}.yaml'
    return _load_yaml(p)


def horizontal_fov(profile: dict) -> float:
    i = profile['intrinsics']
    return 2.0 * math.atan(profile['sensor']['width'] / (2.0 * i['fx']))


def validate(d: dict) -> None:
    for k in REQUIRED_TOP:
        if k not in d:
            raise ProfileError(f'必須キーがない: {k}')
    if d['schema'] != 1:
        raise ProfileError(f"未対応の schema: {d['schema']}")
    s = d['sensor']
    if s.get('gz_type') not in GZ_TYPES:
        raise ProfileError(f"sensor.gz_type は {sorted(GZ_TYPES)} のどれか")
    for k in ('width', 'height', 'fps'):
        if not isinstance(s.get(k), (int, float)) or s[k] <= 0:
            raise ProfileError(f'sensor.{k} が不正')
    _check_intrinsics(d)
    _check_tf(d)
    _check_streams(d)
    if d['topics'].get('ns_mode', 'flat') not in ('flat', 'nested'):
        raise ProfileError('topics.ns_mode は flat か nested')


def _check_intrinsics(d: dict) -> None:
    i, s = d['intrinsics'], d['sensor']
    # Gazebo は fx==fy・主点が画像中心・歪みゼロの理想ピンホールしか描画できない。
    if abs(i['fx'] - i['fy']) > 1e-6:
        raise ProfileError('fx != fy は描画できない')
    if abs(i['cx'] - s['width'] / 2) > 0.5 or abs(i['cy'] - s['height'] / 2) > 0.5:
        raise ProfileError('主点が画像中心でないプロファイルは描画できない')
    if any(abs(v) > 0 for v in i.get('d', [])):
        raise ProfileError('歪み係数は描画できない（d はゼロ）')


def _check_tf(d: dict) -> None:
    frames = d['frames']
    root = frames['camera_link']
    reach = {root}
    links = d['tf'].get('links', [])
    changed = True
    while changed:
        changed = False
        for ln in links:
            if ln['parent'] in reach and ln['child'] not in reach:
                reach.add(ln['child'])
                changed = True
    for ln in links:
        if ln['child'] not in reach:
            raise ProfileError(f"tf.links が camera_link から連結していない: {ln['child']}")
    sensor_key = d['tf'].get('gz_sensor_frame')
    if sensor_key not in frames:
        raise ProfileError('tf.gz_sensor_frame が frames に無い')


def _check_streams(d: dict) -> None:
    frames = d['frames']
    streams = d['topics'].get('streams', {})
    if not streams:
        raise ProfileError('topics.streams が空')
    for sid, st in streams.items():
        if st.get('source') not in SOURCES:
            raise ProfileError(f'{sid}: source が不正')
        if st.get('frame') not in frames:
            raise ProfileError(f"{sid}: frame '{st.get('frame')}' が frames に無い")
        tr = st.get('transform')
        if tr is not None:
            if tr not in SUPPORTED_TRANSFORMS:
                raise ProfileError(f'{sid}: 未知の transform {tr}')
            need = SUPPORTED_TRANSFORMS[tr]
            if need and st.get('encoding') != need:
                raise ProfileError(f'{sid}: {tr} の出力は {need}（encoding={st.get("encoding")}）')
        if st['source'] in ('depth', 'info_depth', 'points') and d['sensor']['gz_type'] != 'rgbd_camera':
            raise ProfileError(f'{sid}: {st["source"]} は rgbd_camera が必要')


def topic_name(d: dict, stream_id: str, ns_mode: str = None) -> str:
    t = d['topics']
    mode = ns_mode or t.get('ns_mode', 'flat')
    root = t.get('root', '').rstrip('/')
    if mode == 'nested' and root:
        root = f'{root}{root}'
    name = t['streams'][stream_id]['name'].lstrip('/')
    return f'{root}/{name}'


def enabled_streams(d: dict, pointcloud: bool = False) -> list:
    out = []
    for sid, st in d['topics']['streams'].items():
        gate = st.get('enabled_by')
        if gate == 'pointcloud.enable' and not pointcloud:
            continue
        out.append(sid)
    return out


def camera_info_fields(d: dict) -> dict:
    """プロファイルの内部パラメータから CameraInfo の値を作る（単眼、歪みなし、R=I）。"""
    i, s = d['intrinsics'], d['sensor']
    fx, fy, cx, cy = i['fx'], i['fy'], i['cx'], i['cy']
    return {
        'width': int(s['width']),
        'height': int(s['height']),
        'distortion_model': i.get('distortion_model', 'plumb_bob'),
        'd': [float(v) for v in i.get('d', [0.0] * 5)],
        'k': [fx, 0.0, cx, 0.0, fy, cy, 0.0, 0.0, 1.0],
        'r': [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0],
        'p': [fx, 0.0, cx, 0.0, 0.0, fy, cy, 0.0, 0.0, 0.0, 1.0, 0.0],
    }
