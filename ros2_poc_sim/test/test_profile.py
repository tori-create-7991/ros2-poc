import copy
import math

import pytest
import yaml

from ros2_poc_sim import profile as P


def test_both_profiles_load():
    for name in ('realsense_d435', 'usb_cam'):
        prof = P.load_profile(name)
        assert prof['name'] == name


def test_horizontal_fov_derived_from_fx():
    d435 = P.load_profile('realsense_d435')
    usb = P.load_profile('usb_cam')
    assert P.horizontal_fov(d435) == pytest.approx(1.2111, abs=1e-3)
    assert P.horizontal_fov(usb) == pytest.approx(1.2602, abs=1e-3)
    assert P.horizontal_fov(d435) == pytest.approx(
        2 * math.atan(320 / 462.14), abs=1e-9)


def _base():
    return copy.deepcopy(P.load_profile('realsense_d435'))


@pytest.mark.parametrize('mutate', [
    lambda d: d['intrinsics'].update(fy=500.0),
    lambda d: d['intrinsics'].update(cx=300.0),
    lambda d: d['intrinsics'].update(cy=100.0),
    lambda d: d['intrinsics'].update(d=[0.1, 0, 0, 0, 0]),
])
def test_non_renderable_intrinsics_rejected(mutate):
    d = _base()
    mutate(d)
    with pytest.raises(P.ProfileError):
        P.validate(d)


def test_unknown_frame_rejected():
    d = _base()
    d['topics']['streams']['color_image']['frame'] = 'nope'
    with pytest.raises(P.ProfileError):
        P.validate(d)


def test_disconnected_tf_rejected():
    d = _base()
    d['tf']['links'].append(
        {'parent': 'ghost', 'child': 'orphan', 'xyz': [0, 0, 0], 'rpy': [0, 0, 0]})
    with pytest.raises(P.ProfileError):
        P.validate(d)


def test_transform_encoding_mismatch_rejected():
    d = _base()
    d['topics']['streams']['aligned_depth']['encoding'] = '32FC1'
    with pytest.raises(P.ProfileError):
        P.validate(d)


def test_unknown_transform_rejected():
    d = _base()
    d['topics']['streams']['color_image']['transform'] = 'magic'
    with pytest.raises(P.ProfileError):
        P.validate(d)


def test_missing_key_rejected():
    d = _base()
    del d['sensor']
    with pytest.raises(P.ProfileError):
        P.validate(d)


def test_topic_names_flat_and_nested():
    d = _base()
    assert P.topic_name(d, 'color_image') == '/camera/color/image_raw'
    assert P.topic_name(d, 'aligned_depth') == '/camera/aligned_depth_to_color/image_raw'
    assert P.topic_name(d, 'points') == '/camera/depth/color/points'
    assert P.topic_name(d, 'color_image', ns_mode='nested') == '/camera/camera/color/image_raw'
    usb = P.load_profile('usb_cam')
    assert P.topic_name(usb, 'color_image') == '/image_raw'
    assert P.topic_name(usb, 'color_info') == '/camera_info'


def test_tagged_yaml_rejected(tmp_path):
    f = tmp_path / 'evil.yaml'
    f.write_text('a: !!python/object/apply:os.system ["echo hi"]\n')
    with pytest.raises(yaml.YAMLError):
        P.load_profile(str(f))


def test_enabled_streams_respect_pointcloud_flag():
    d = _base()
    names = P.enabled_streams(d, pointcloud=False)
    assert 'points' not in names and 'color_image' in names
    assert 'points' in P.enabled_streams(d, pointcloud=True)


def test_camera_info_fields():
    f = P.camera_info_fields(P.load_profile('realsense_d435'))
    assert f['width'] == 640 and f['height'] == 480
    assert f['k'] == [462.14, 0.0, 320.0, 0.0, 462.14, 240.0, 0.0, 0.0, 1.0]
    assert f['p'][0] == 462.14 and f['p'][2] == 320.0 and f['p'][5] == 462.14 and f['p'][6] == 240.0
    assert len(f['p']) == 12 and len(f['d']) == 5 and f['distortion_model'] == 'plumb_bob'


def test_profiles_do_not_share_mount_frame_names():
    """複数のカメラを同時に置いても base_link→camera_link の静的 TF が衝突しないこと。"""
    d435 = P.load_profile('realsense_d435')
    usb = P.load_profile('usb_cam')
    assert d435['frames']['camera_link'] != usb['frames']['camera_link']
    d435_frames = set(d435['frames'].values())
    usb_frames = set(usb['frames'].values())
    assert not (d435_frames & usb_frames)
