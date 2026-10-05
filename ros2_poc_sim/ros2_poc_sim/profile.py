"""カメラプロファイル（YAML）の読み込み・検証。

プロファイルは「どの gz ストリームを、どの名前・型・エンコーディング・フレームで出すか」を宣言する。
CameraInfo は gz が描画できる理想ピンホールに限る（描画と CameraInfo を一致させるため）。
"""
import math
import re
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


NAME_RE = re.compile(r'[A-Za-z0-9_]+')


def is_name(v) -> bool:
    """英数字とアンダースコアだけ（末尾の改行も許さない: fullmatch）。"""
    return isinstance(v, str) and NAME_RE.fullmatch(v) is not None
QOS_KIND = {'color': 'image', 'depth': 'image', 'points': 'points',
             'info_color': 'info', 'info_depth': 'info'}
_TYPE_FOR_SOURCE = {
    'color': 'sensor_msgs/msg/Image', 'depth': 'sensor_msgs/msg/Image',
    'points': 'sensor_msgs/msg/PointCloud2',
    'info_color': 'sensor_msgs/msg/CameraInfo', 'info_depth': 'sensor_msgs/msg/CameraInfo',
}


class ProfileError(ValueError):
    pass


def _load_yaml(path: Path) -> dict:
    with open(path, encoding='utf-8') as f:
        return yaml.safe_load(f)


def _finite_positive(v) -> bool:
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(v) and v > 0)


def _vec3(v, label: str) -> None:
    if (not isinstance(v, (list, tuple)) or len(v) != 3
            or any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x)
                   for x in v)):
        raise ProfileError(f'{label} は有限な数 3 つ')


def compressed_streams(d: dict) -> list:
    """圧縮して出すストリームの一覧（`compressed:` が空・null でも空リスト）。validate と launch の共通の入口。"""
    return list(d.get('compressed') or [])


def _resolve(kind: str, name_or_path: str) -> Path:
    p = Path(name_or_path)
    if p.suffix in ('.yaml', '.yml') and p.exists():
        return p
    if not is_name(name_or_path):
        raise ProfileError(f'名前は英数字とアンダースコアのみ: {name_or_path!r}')
    return config_dir(kind) / f'{name_or_path}.yaml'


def load_profile(name_or_path: str) -> dict:
    p = _resolve('profiles', name_or_path)
    data = _load_yaml(p)
    validate(data)
    return data


def load_placement(name_or_path: str) -> dict:
    d = _load_yaml(_resolve('placements', name_or_path))
    if not isinstance(d, dict):
        raise ProfileError('placement が空か不正')
    for k in ('name', 'mode', 'parent_frame', 'robot_base_in_world', 'look_at', 'object'):
        if k not in d:
            raise ProfileError(f'placement に必須キーがない: {k}')
    if d['mode'] != 'fixed':
        raise ProfileError("placement.mode は 'fixed' のみ対応（手先カメラは第 2 弾）")
    for k in ('look_at', 'object', 'robot_base_in_world'):
        if not isinstance(d[k], dict):
            raise ProfileError(f'placement.{k} は辞書')
    for k in ('eye', 'target', 'up_hint'):
        _vec3(d['look_at'].get(k), f'placement.look_at.{k}')
    _vec3(d['object'].get('xyz_in_base_link'), 'placement.object.xyz_in_base_link')
    _vec3(d['robot_base_in_world'].get('xyz'), 'placement.robot_base_in_world.xyz')
    _vec3(d['robot_base_in_world'].get('rpy'), 'placement.robot_base_in_world.rpy')
    obj = d.get('object') or {}
    for label, v in (('name', d.get('name')), ('object.name', obj.get('name'))):
        if v is not None and not is_name(v):
            raise ProfileError(f'{label} は英数字とアンダースコアのみ: {v!r}')
    return d


def horizontal_fov(profile: dict) -> float:
    i = profile['intrinsics']
    return 2.0 * math.atan(profile['sensor']['width'] / (2.0 * i['fx']))


def validate(d: dict) -> None:
    for k in REQUIRED_TOP:
        if k not in d:
            raise ProfileError(f'必須キーがない: {k}')
    if d['schema'] != 1:
        raise ProfileError(f"未対応の schema: {d['schema']}")
    if not is_name(d['name']):
        raise ProfileError(f"name は英数字とアンダースコアのみ: {d['name']!r}")
    s = d['sensor']
    if s.get('gz_type') not in GZ_TYPES:
        raise ProfileError(f"sensor.gz_type は {sorted(GZ_TYPES)} のどれか")
    for k in ('width', 'height', 'fps'):
        if not _finite_positive(s.get(k)):
            raise ProfileError(f'sensor.{k} が不正')
    clip = s.get('clip') or {}
    for k in ('near', 'far'):
        if not _finite_positive(clip.get(k)):
            raise ProfileError(f'sensor.clip.{k} が不正')
    if clip['near'] >= clip['far']:
        raise ProfileError('sensor.clip.near < far でなければならない')
    _check_intrinsics(d)
    _check_tf(d)
    _check_streams(d)
    _check_extras(d)
    if d['topics'].get('ns_mode', 'flat') not in ('flat', 'nested'):
        raise ProfileError('topics.ns_mode は flat か nested')


def _check_intrinsics(d: dict) -> None:
    i, s = d['intrinsics'], d['sensor']
    for k in ('fx', 'fy', 'cx', 'cy'):
        if not _finite_positive(i.get(k)):
            raise ProfileError(f'intrinsics.{k} が不正')
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
    if frames[sensor_key] not in reach:
        raise ProfileError(f'gz_sensor_frame の {frames[sensor_key]} が camera_link から辿れない')
    # フレームのキー（frames）→実フレーム名の対応を取り違えやすいので、TF に載るフレームだけを許す
    for sid, st in d['topics'].get('streams', {}).items():
        real = frames.get(st.get('frame'))
        if real is not None and real not in reach:
            raise ProfileError(f'{sid}: frame {real} が tf.links から辿れない（TF が無い frame_id になる）')


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
        if st['type'] != _TYPE_FOR_SOURCE[st['source']]:
            raise ProfileError(f"{sid}: source {st['source']} の type は {_TYPE_FOR_SOURCE[st['source']]}")
        if st['type'] == 'sensor_msgs/msg/Image' and not st.get('encoding'):
            raise ProfileError(f'{sid}: Image には encoding が要る')
        if st['source'] == 'color' and tr in (None, 'passthrough') and st.get('encoding') != 'rgb8':
            raise ProfileError(f"{sid}: gz の色は rgb8。passthrough で {st.get('encoding')} と名乗ると嘘になる（変換を足す）")
        if QOS_KIND[st['source']] not in d['qos']:
            raise ProfileError(f"{sid}: qos.{QOS_KIND[st['source']]} が無い")
        if st['source'] in ('depth', 'info_depth', 'points') and d['sensor']['gz_type'] != 'rgbd_camera':
            raise ProfileError(f'{sid}: {st["source"]} は rgbd_camera が必要')


def _check_extras(d: dict) -> None:
    streams = d['topics']['streams']
    for sid in compressed_streams(d):
        st = streams.get(sid)
        if st is None or st['type'] != 'sensor_msgs/msg/Image':
            raise ProfileError(f'compressed: {sid} は Image のストリームでなければならない')
    for sid, st in streams.items():
        if st.get('enabled_by') not in (None, 'pointcloud.enable'):
            raise ProfileError(f"{sid}: 未知の enabled_by {st['enabled_by']!r}")


def check_ns_mode(ns_mode: str) -> str:
    if ns_mode not in ('', 'flat', 'nested'):
        raise ProfileError(f"ns_mode は flat か nested（空で既定）: {ns_mode!r}")
    return ns_mode


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
