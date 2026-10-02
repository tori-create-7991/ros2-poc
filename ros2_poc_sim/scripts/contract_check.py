#!/usr/bin/env python3
"""カメラの出力契約（トピック名・型・エンコーディング・frame_id・Hz・同一 stamp・TF）を検査する。

受け手（ros2lab）から見える姿を検査するので、シミュでも実機でも同じ契約ファイルを流せる。
ros2lab（ros-base）で動くよう rclpy / tf2_ros / PyYAML / stdlib のみ。
  python3 contract_check.py /tmp/realsense_d435.yaml [--seconds 10] [--with-optional] [--min-hz 5]
契約ファイルの min_hz は「生きている」ことの下限（シミュは CPU 描画で数 Hz）。実機では --min-hz で厳しくする。
終了コード: 0 = 全て合格、1 = 不合格あり、2 = 実行エラー（契約ファイルが読めない・不正）
"""
import sys
import time

import yaml


def evaluate(exp: dict, obs):
    """期待(exp)と観測(obs)を比べ、問題の文字列リストを返す。"""
    if obs is None:
        return [f"{exp['name']}: topic not found"]
    problems = []
    if exp['type'] not in obs['types']:
        problems.append(f"type {obs['types']!r} != {exp['type']}")
    if obs['count'] == 0:
        return problems + ['no message received']
    if 'encoding' in exp and obs.get('encoding') != exp['encoding']:
        problems.append(f"encoding {obs.get('encoding')!r} != {exp['encoding']}")
    if 'frame_id' in exp and obs.get('frame_id') != exp['frame_id']:
        problems.append(f"frame_id {obs.get('frame_id')!r} != {exp['frame_id']}")
    if obs['hz'] < exp.get('min_hz', 0.0):
        problems.append(f"hz {obs['hz']:.2f} < {exp['min_hz']}")
    return problems


def stamp_ratio(names, stamps):
    """グループ内で最少の件数に対する、全トピックに共通する stamp の割合。"""
    sets = [stamps.get(n, set()) for n in names]
    smallest = min((len(x) for x in sets), default=0)
    return (len(set.intersection(*sets)) / smallest) if smallest else 0.0


def check_info(exp: dict, info) -> list:
    """CameraInfo の内容（幅・高さ・fx）を契約と比べる。info は dict(width,height,k)。"""
    problems = []
    if info is None:
        return ['camera_info を受信できず内容を検査できない']
    if 'width' in exp and info['width'] != exp['width']:
        problems.append(f"width {info['width']} != {exp['width']}")
    if 'height' in exp and info['height'] != exp['height']:
        problems.append(f"height {info['height']} != {exp['height']}")
    tol = exp.get('fx_tol', 1.0)
    for key, idx in (('fx', 0), ('cx', 2), ('fy', 4), ('cy', 5)):
        if key in exp and abs(info['k'][idx] - exp[key]) > tol:
            problems.append(f"{key} {info['k'][idx]} != {exp[key]} ±{tol}")
    return problems


def check_same_stamp(group, stamps, expected_names, min_ratio):
    """同一 stamp の検査。戻り値は (ok, 説明)。グループの要素が契約に無い・stamp が自明（全て 0 / 1 種類）なら不合格。"""
    missing = [n for n in group if n not in expected_names]
    if missing:
        return False, f'契約に無いトピックがグループにある: {missing}'
    for n in group:
        vals = stamps.get(n, set())
        if not vals:
            return False, f'{n} を受信できず検査できない'
        if vals == {(0, 0)} or len(vals) < 2:
            return False, f'{n} の stamp が自明（受信した種類 {len(vals)}、全て 0 の可能性）'
    ratio = stamp_ratio(group, stamps)
    return ratio >= min_ratio, f'共通 stamp {len(common_stamps(group, stamps))} 件（最少の {ratio:.0%}）'


def check_image_info_size(img_name, info_name, sizes, infos):
    if img_name not in sizes:
        return [f'画像 {img_name} の大きさを受信できず比較できない']
    if info_name not in infos:
        return [f'{info_name} を受信できず比較できない']
    i = infos[info_name]
    if sizes[img_name] != (i['width'], i['height']):
        return [f'画像 {sizes[img_name]} と camera_info の大きさ {(i["width"], i["height"])} が違う']
    return []


def common_stamps(names, stamps):
    sets = [stamps.get(n, set()) for n in names]
    return set.intersection(*sets) if sets else set()


def main(argv=None):
    import rclpy
    from rclpy.qos import qos_profile_sensor_data
    from rosidl_runtime_py.utilities import get_message
    from tf2_ros import Buffer, TransformListener

    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print(__doc__)
        return 2
    path = argv[0]
    seconds = float(argv[argv.index('--seconds') + 1]) if '--seconds' in argv else 10.0
    with_optional = '--with-optional' in argv
    min_hz_override = float(argv[argv.index('--min-hz') + 1]) if '--min-hz' in argv else None
    try:
        with open(path, encoding='utf-8') as f:
            contract = yaml.safe_load(f)
        if not isinstance(contract, dict) or 'topics' not in contract:
            raise ValueError("'topics' が無い")
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f'ERROR: 契約ファイルを読めない: {path}: {exc}', file=sys.stderr)
        return 2
    expected = list(contract['topics']) + (list(contract.get('optional_topics', [])) if with_optional else [])

    if min_hz_override is not None:
        for e in expected:
            e['min_hz'] = max(e.get('min_hz', 0.0), min_hz_override)

    rclpy.init()
    node = rclpy.create_node('contract_check')
    tf_buffer = Buffer()
    TransformListener(tf_buffer, node)
    seen = {e['name']: {'count': 0, 'first': None, 'last': None, 'encoding': None, 'frame_id': None}
            for e in expected}
    stamps = {e['name']: set() for e in expected}
    infos, sizes = {}, {}

    def make_cb(name):
        def cb(msg):
            s = seen[name]
            now = time.monotonic()
            s['first'] = s['first'] or now
            s['last'] = now
            s['count'] += 1
            s['frame_id'] = msg.header.frame_id
            s['encoding'] = getattr(msg, 'encoding', None)
            stamps[name].add((msg.header.stamp.sec, msg.header.stamp.nanosec))
            if hasattr(msg, 'k'):
                infos[name] = {'width': msg.width, 'height': msg.height, 'k': list(msg.k)}
            elif hasattr(msg, 'width') and hasattr(msg, 'height') and hasattr(msg, 'encoding'):
                sizes[name] = (msg.width, msg.height)
        return cb

    types_by_topic = {}
    for _ in range(40):  # discovery 待ち（コンテナ間は 10〜20 秒かかることがある）
        types_by_topic = dict(node.get_topic_names_and_types())
        if all(e['name'] in types_by_topic for e in expected):
            break
        rclpy.spin_once(node, timeout_sec=0.5)
    for e in expected:
        ts = types_by_topic.get(e['name'])
        if ts:
            node.create_subscription(get_message(e['type']), e['name'], make_cb(e['name']),
                                     qos_profile_sensor_data)
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        rclpy.spin_once(node, timeout_sec=0.1)

    failed = False
    for e in expected:
        types = types_by_topic.get(e['name'])
        s = seen[e['name']]
        obs = None
        if types:
            span = (s['last'] - s['first']) if s['count'] > 1 else 0
            hz = (s['count'] - 1) / span if span > 0 else 0.0
            obs = {'types': types, 'count': s['count'], 'hz': hz,
                   'encoding': s['encoding'], 'frame_id': s['frame_id']}
        problems = evaluate(e, obs)
        failed |= bool(problems)
        print(f"{'FAIL' if problems else 'ok  '} {e['name']}"
              + (f"  hz={obs['hz']:.1f} enc={obs['encoding']!r} frame={obs['frame_id']!r}" if obs and obs['count'] else '')
              + ''.join(f'\n       - {p}' for p in problems))
    names = {e['name'] for e in expected}
    for group in contract.get('same_stamp', []):
        if not with_optional and any(n not in names for n in group):
            continue   # オプションのトピックを含むグループは --with-optional のときだけ
        ok, detail = check_same_stamp(group, stamps, names, contract.get('same_stamp_min_ratio', 0.5))
        failed |= not ok
        print(f"{'ok  ' if ok else 'FAIL'} same_stamp {group}: {detail}")
    for name, exp_info in (contract.get('camera_info') or {}).items():
        problems = check_info(exp_info, infos.get(name))
        if exp_info.get('image'):
            problems += check_image_info_size(exp_info['image'], name, sizes, infos)
        failed |= bool(problems)
        print(f"{'FAIL' if problems else 'ok  '} camera_info {name}" + ''.join(f'\n       - {p}' for p in problems))
    tf = contract.get('tf') or {}
    for fr in tf.get('frames', []):
        deadline = time.monotonic() + 5.0
        ok = False
        while time.monotonic() < deadline and not ok:
            rclpy.spin_once(node, timeout_sec=0.2)
            ok = tf_buffer.can_transform(tf['parent'], fr, rclpy.time.Time())
        failed |= not ok
        print(f"{'ok  ' if ok else 'FAIL'} tf {tf['parent']} -> {fr}")
    node.destroy_node()
    rclpy.shutdown()
    print('RESULT:', 'FAIL' if failed else 'PASS')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
