import importlib.util
from pathlib import Path

import pytest
import yaml

spec = importlib.util.spec_from_file_location(
    'contract_check', Path(__file__).resolve().parents[1] / 'scripts' / 'contract_check.py')
C = importlib.util.module_from_spec(spec)
spec.loader.exec_module(C)

ROOT = Path(__file__).resolve().parents[1] / 'config' / 'contracts'


def _contract(name):
    return yaml.safe_load((ROOT / f'{name}.yaml').read_text(encoding='utf-8'))


def test_contract_files_are_well_formed():
    for name in ('realsense_d435', 'usb_cam'):
        c = _contract(name)
        assert c['profile'] == name
        for t in c['topics']:
            assert t['name'].startswith('/') and t['type'].startswith('sensor_msgs/msg/')


def test_evaluate_pass():
    exp = {'name': '/a', 'type': 'sensor_msgs/msg/Image', 'encoding': 'rgb8', 'frame_id': 'f', 'min_hz': 2.0}
    obs = {'types': ['sensor_msgs/msg/Image'], 'encoding': 'rgb8', 'frame_id': 'f', 'hz': 4.0, 'count': 8}
    assert C.evaluate(exp, obs) == []


@pytest.mark.parametrize('obs_patch,needle', [
    ({'types': ['sensor_msgs/msg/CameraInfo']}, 'type'),
    ({'encoding': '32FC1'}, 'encoding'),
    ({'frame_id': 'other'}, 'frame_id'),
    ({'hz': 0.5}, 'hz'),
    ({'count': 0, 'hz': 0.0}, 'no message'),
])
def test_evaluate_failures(obs_patch, needle):
    exp = {'name': '/a', 'type': 'sensor_msgs/msg/Image', 'encoding': 'rgb8', 'frame_id': 'f', 'min_hz': 2.0}
    obs = {'types': ['sensor_msgs/msg/Image'], 'encoding': 'rgb8', 'frame_id': 'f', 'hz': 4.0, 'count': 8}
    obs.update(obs_patch)
    problems = C.evaluate(exp, obs)
    assert problems and any(needle in p for p in problems)


def test_missing_topic_reported():
    exp = {'name': '/a', 'type': 'sensor_msgs/msg/Image', 'min_hz': 1.0}
    assert any('not found' in p for p in C.evaluate(exp, None))


def test_same_stamp_groups():
    stamps = {'/a': {(1, 2), (3, 4)}, '/b': {(1, 2), (5, 6)}, '/c': {(1, 2)}}
    assert C.common_stamps(['/a', '/b', '/c'], stamps) == {(1, 2)}
    assert C.common_stamps(['/a', '/b'], {'/a': {(1, 2)}, '/b': {(9, 9)}}) == set()


def test_stamp_ratio():
    st = {'/a': {1, 2, 3, 4}, '/b': {1, 2}, '/c': {1, 2, 9}}
    assert C.stamp_ratio(['/a', '/b', '/c'], st) == 1.0   # 最少の /b(2 件) が全て共通
    assert C.stamp_ratio(['/a', '/b'], {'/a': {1, 2, 3}, '/b': {1, 9}}) == 0.5
    assert C.stamp_ratio(['/a'], {'/a': set()}) == 0.0


def test_check_info():
    exp = {'width': 640, 'height': 480, 'fx': 462.14, 'fx_tol': 1.0}
    ok = {'width': 640, 'height': 480, 'k': [462.14] + [0] * 8}
    assert C.check_info(exp, ok) == []
    assert any('fx' in p for p in C.check_info(exp, {**ok, 'k': [300.0] + [0] * 8}))
    assert any('width' in p for p in C.check_info(exp, {**ok, 'width': 320}))
    assert C.check_info(exp, None)


def test_check_same_stamp_ok_and_failures():
    names = {'/a', '/b'}
    good = {'/a': {(1, 1), (2, 2), (3, 3)}, '/b': {(1, 1), (2, 2), (4, 4)}}
    assert C.check_same_stamp(['/a', '/b'], good, names, 0.5)[0] is True
    assert C.check_same_stamp(['/a', '/zzz'], good, names, 0.5)[0] is False        # 契約に無い要素
    assert C.check_same_stamp(['/a', '/b'], {'/a': {(1, 1), (2, 2)}}, names, 0.5)[0] is False   # 未受信
    zero = {'/a': {(0, 0)}, '/b': {(0, 0)}}
    assert C.check_same_stamp(['/a', '/b'], zero, names, 0.5)[0] is False           # 全て stamp 0
    pinned = {'/a': {(5, 5)}, '/b': {(5, 5)}}
    assert C.check_same_stamp(['/a', '/b'], pinned, names, 0.5)[0] is False         # 1 種類だけ
    low = {'/a': {(1, 1), (2, 2), (3, 3), (4, 4)}, '/b': {(1, 1), (9, 9), (8, 8), (7, 7)}}
    assert C.check_same_stamp(['/a', '/b'], low, names, 0.5)[0] is False            # 割合不足


def test_check_image_info_size():
    sizes = {'/img': (640, 480)}
    infos = {'/info': {'width': 640, 'height': 480, 'k': []}}
    assert C.check_image_info_size('/img', '/info', sizes, infos) == []
    assert C.check_image_info_size('/img', '/info', {'/img': (320, 240)}, infos)
    assert C.check_image_info_size('/missing', '/info', sizes, infos)
    assert C.check_image_info_size('/img', '/missing', sizes, infos)


def test_check_info_compares_all_intrinsics():
    exp = {'fx': 462.14, 'fy': 462.14, 'cx': 320.0, 'cy': 240.0, 'fx_tol': 1.0}
    base = {'width': 1, 'height': 1, 'k': [462.14, 0, 320.0, 0, 462.14, 240.0, 0, 0, 1]}
    assert C.check_info(exp, base) == []
    for idx, key in ((2, 'cx'), (4, 'fy'), (5, 'cy')):
        k = list(base['k'])
        k[idx] += 10
        assert any(key in p for p in C.check_info(exp, {**base, 'k': k}))


def test_main_returns_2_for_unreadable_contract(tmp_path, capsys):
    assert C.main([str(tmp_path / 'nope.yaml')]) == 2
    bad = tmp_path / 'bad.yaml'
    bad.write_text('x: 1\n')
    assert C.main([str(bad)]) == 2
    bad.write_text('topics: [')
    assert C.main([str(bad)]) == 2


def test_remote_strings_are_escaped_in_messages():
    evil = '\x1b]0;pwned\x07\nFAKE ok line'
    exp = {'name': '/a', 'type': 'sensor_msgs/msg/Image', 'encoding': 'rgb8', 'frame_id': 'f', 'min_hz': 0}
    obs = {'types': [evil], 'encoding': evil, 'frame_id': evil, 'hz': 9.0, 'count': 3}
    for msg in C.evaluate(exp, obs):
        assert '\x1b' not in msg and '\n' not in msg and '\x07' not in msg
