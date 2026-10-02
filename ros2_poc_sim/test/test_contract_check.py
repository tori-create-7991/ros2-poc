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
