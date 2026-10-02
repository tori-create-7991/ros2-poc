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
