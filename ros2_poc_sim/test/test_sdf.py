import colorsys
import xml.etree.ElementTree as ET

import pytest

from ros2_poc_sim import geometry as G
from ros2_poc_sim import profile as P
from ros2_poc_sim import sdf as S


def _parse(profile_name, placement_name):
    prof = P.load_profile(profile_name)
    pl = P.load_placement(placement_name)
    root = ET.fromstring(S.camera_model_sdf(prof, pl))
    return prof, pl, root


def test_rgbd_sensor_fields():
    prof, pl, root = _parse('realsense_d435', 'fixed_near_top')
    sensor = root.find('.//sensor')
    assert sensor.get('type') == 'rgbd_camera'
    assert sensor.findtext('topic') == 'sim_camera/realsense_d435'
    assert sensor.findtext('gz_frame_id') == 'camera_color_optical_frame'
    assert float(sensor.findtext('update_rate')) == 10
    cam = sensor.find('camera')
    assert float(cam.findtext('horizontal_fov')) == pytest.approx(P.horizontal_fov(prof), abs=1e-6)
    assert cam.findtext('image/width') == '640' and cam.findtext('image/height') == '480'
    assert float(cam.findtext('clip/near')) == 0.2


def test_camera_sensor_topic_is_image_leaf():
    _, _, root = _parse('usb_cam', 'fixed_front_oblique')
    sensor = root.find('.//sensor')
    assert sensor.get('type') == 'camera'
    assert sensor.findtext('topic') == 'sim_camera/usb_cam/image'
    assert sensor.findtext('gz_frame_id') == 'camera'
    assert sensor.findtext('camera/image/format') == 'R8G8B8'


def test_model_is_static_and_has_no_collision():
    _, _, root = _parse('realsense_d435', 'fixed_near_top')
    assert root.tag == 'sdf'
    model = root.find('model')
    assert model.findtext('static') == 'true'
    assert root.find('.//collision') is None


def test_model_pose_matches_geometry():
    prof, pl, root = _parse('realsense_d435', 'fixed_near_top')
    vals = [float(v) for v in root.find('model').findtext('pose').split()]
    pos, R = G.sensor_pose_in_world(prof, pl)
    assert vals[:3] == pytest.approx(list(pos), abs=1e-6)
    assert vals[3:] == pytest.approx(list(G.matrix_to_rpy(R)), abs=1e-6)


def test_object_sdf_is_dynamic_blue_cube():
    pl = P.load_placement('fixed_near_top')
    root = ET.fromstring(S.object_model_sdf(pl))
    model = root.find('model')
    assert model.get('name') == 'blue_cube'
    assert model.findtext('static') in (None, 'false')
    size = [float(v) for v in model.find('.//collision//box/size').text.split()]
    assert size == [0.04, 0.04, 0.04]
    assert float(model.findtext('.//inertial/mass')) == pytest.approx(0.05)
    assert float(model.findtext('.//surface/friction/ode/mu')) == pytest.approx(1.0)
    x, y, z = (float(v) for v in model.findtext('pose').split()[:3])
    assert (x, y, z) == pytest.approx((0.20, 0.10, 1.045))


def test_cube_color_is_inside_color_detection_hsv_range():
    pl = P.load_placement('fixed_near_top')
    root = ET.fromstring(S.object_model_sdf(pl))
    r, g, b, _ = (float(v) for v in root.find('.//visual//diffuse').text.split())
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    h, s, v = h * 180, s * 255, v * 255  # OpenCV スケール
    assert 100 <= h <= 125 and s >= 100 and v >= 30


def test_sensors_system_request_text():
    req = S.sensors_system_request(world_entity_id=1)
    assert 'gz-sim-sensors-system' in req and 'ogre2' in req and 'id: 1' in req


def test_pose_args_for_create_match_sdf_pose():
    prof = P.load_profile('realsense_d435')
    pl = P.load_placement('fixed_near_top')
    pose = S.camera_pose6(prof, pl)
    root = ET.fromstring(S.camera_model_sdf(prof, pl))
    vals = [float(v) for v in root.find('model').findtext('pose').split()]
    assert vals == pytest.approx(list(pose), abs=1e-6)
    args = S.pose_args(pose)
    assert args[0::2] == ['-x', '-y', '-z', '-R', '-P', '-Y']
    assert float(args[5]) == pytest.approx(1.015 + 0.42, abs=1e-6)  # eye z + base 1.015


def test_object_pose6_is_base_offset_plus_placement():
    pl = P.load_placement('fixed_near_top')
    assert S.object_pose6(pl) == pytest.approx((0.20, 0.10, 1.045, 0, 0, 0))
