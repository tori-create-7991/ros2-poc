import math

import numpy as np
import pytest

from ros2_poc_sim import geometry as G
from ros2_poc_sim import profile as P


def test_look_at_x_axis_points_to_target():
    R = G.look_at_matrix([0.2, 0, 0.42], [0.2, 0, 0.0], [1, 0, 0])
    assert R @ np.array([1, 0, 0]) == pytest.approx([0, 0, -1], abs=1e-9)
    # 右手系・正規直交
    assert np.linalg.det(R) == pytest.approx(1.0)
    assert R.T @ R == pytest.approx(np.eye(3), abs=1e-9)


def test_look_at_front_view_is_level_with_z_up():
    R = G.look_at_matrix([0.8, 0, 0.5], [0.2, 0, 0.0], [0, 0, 1])
    f = R @ np.array([1, 0, 0])
    assert f == pytest.approx(np.array([-0.6, 0, -0.5]) / np.linalg.norm([-0.6, 0, -0.5]))
    # 画像の上方向（z 軸）は世界の上向き成分を持つ
    assert (R @ np.array([0, 0, 1]))[2] > 0


def test_rpy_roundtrip():
    for rpy in [(0.1, 0.2, 0.3), (-1.2, 0.4, 2.5), (0, 0, 0)]:
        R = G.rpy_to_matrix(*rpy)
        assert G.matrix_to_rpy(R) == pytest.approx(rpy, abs=1e-9)


def test_quaternion_unit_and_consistent():
    R = G.rpy_to_matrix(0.3, -0.2, 1.0)
    q = G.matrix_to_quat(R)
    assert np.linalg.norm(q) == pytest.approx(1.0)
    x, y, z, w = q
    R2 = np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])
    assert R2 == pytest.approx(R, abs=1e-9)


def test_world_and_tf_poses_come_from_same_numbers():
    prof = P.load_profile('realsense_d435')
    pl = P.load_placement('fixed_near_top')
    cam = G.camera_link_pose_in_base(pl)
    world = G.camera_link_pose_in_world(pl)
    # base_link は world より 1.015 m 高い
    assert world[0] - cam[0] == pytest.approx([0, 0, 1.015])
    assert world[1] == pytest.approx(cam[1])
    s = G.sensor_pose_in_world(prof, pl)
    # センサは camera_color_frame の位置（camera_link から局所 y 方向 +0.015）
    off = s[0] - world[0]
    assert off == pytest.approx(world[1] @ np.array([0, 0.015, 0]), abs=1e-9)


def test_usb_sensor_sits_at_camera_link():
    prof = P.load_profile('usb_cam')
    pl = P.load_placement('fixed_front_oblique')
    s = G.sensor_pose_in_world(prof, pl)
    w = G.camera_link_pose_in_world(pl)
    assert s[0] == pytest.approx(w[0])


def test_chain_offset_accumulates_tf_links():
    prof = P.load_profile('realsense_d435')
    xyz, R = G.frame_in_camera_link(prof, 'camera_color_optical_frame')
    assert xyz == pytest.approx([0, 0.015, 0])
    # 光学フレームの z 軸は camera_link の x（前方）を向く
    assert R @ np.array([0, 0, 1]) == pytest.approx([1, 0, 0], abs=1e-9)
