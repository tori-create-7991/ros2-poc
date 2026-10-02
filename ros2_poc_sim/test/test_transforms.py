import numpy as np
import pytest

from ros2_poc_sim import transforms as T


def test_depth_to_16uc1_mm_values():
    d = np.array([[0.5, 1.0, np.inf], [np.nan, 0.05, 100.0]], dtype=np.float32)
    out = T.depth_to_16uc1_mm(d, near=0.2, far=6.0)
    assert out.dtype == np.uint16
    assert out.tolist() == [[500, 1000, 0], [0, 0, 0]]


def test_depth_rounds_to_nearest_mm():
    d = np.array([[1.2344, 1.2346]], dtype=np.float32)
    assert T.depth_to_16uc1_mm(d, 0.2, 6.0).tolist() == [[1234, 1235]]


def test_depth_over_uint16_is_invalid():
    d = np.array([[70.0]], dtype=np.float32)
    assert T.depth_to_16uc1_mm(d, near=0.2, far=100.0).tolist() == [[0]]


def _cloud(points, step=24):
    """x,y,z (float32 @0,4,8) と rgb (float32 @16) を持つ点群のバイト列。"""
    arr = np.zeros((len(points), step), dtype=np.uint8)
    for i, (x, y, z) in enumerate(points):
        arr[i, 0:12] = np.array([x, y, z], dtype='<f4').view(np.uint8)
        arr[i, 16:20] = np.array([i + 1], dtype='<f4').view(np.uint8)
    return arr.tobytes()


def _xyz(data, step=24):
    arr = np.frombuffer(data, np.uint8).reshape(-1, step)
    return np.ascontiguousarray(arr[:, 0:12]).view('<f4').reshape(-1, 3)


def test_points_to_optical_rotation():
    data = _cloud([(1, 0, 0), (0, 1, 0), (0, 0, 1)])
    out = T.points_to_optical(data, point_step=24)
    xyz = _xyz(out)
    # x前方→z前方、y左→x負、z上→y負
    assert xyz.tolist() == [[0, 0, 1], [-1, 0, 0], [0, -1, 0]]


def test_points_to_optical_keeps_other_fields():
    data = _cloud([(1, 2, 3), (4, 5, 6)])
    out = T.points_to_optical(data, point_step=24)
    a = np.frombuffer(data, np.uint8).reshape(-1, 24)
    b = np.frombuffer(out, np.uint8).reshape(-1, 24)
    assert (a[:, 12:] == b[:, 12:]).all()
    assert len(out) == len(data)


def test_points_to_optical_invalid_becomes_nan():
    inf = float('inf')
    out = T.points_to_optical(_cloud([(inf, inf, inf), (1, 0, 0)]), point_step=24)
    xyz = _xyz(out)
    assert np.isnan(xyz[0]).all()
    assert xyz[1].tolist() == [0, 0, 1]


def test_points_to_optical_regression_from_measurement():
    # 実測: 中心画素 (1.686, -0.002, -0.002) → 光学 z≈1.686
    out = T.points_to_optical(_cloud([(1.686, -0.00180247, -0.00180239)]), point_step=24)
    x, y, z = _xyz(out)[0]
    assert z == pytest.approx(1.686, abs=1e-6)
    assert x == pytest.approx(0.00180247, abs=1e-7)
    assert y == pytest.approx(0.00180239, abs=1e-7)


def test_rgb_image_passthrough_returns_same_bytes():
    assert T.passthrough(b'abc') == b'abc'
