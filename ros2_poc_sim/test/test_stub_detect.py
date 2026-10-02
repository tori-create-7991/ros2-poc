import importlib.util
from pathlib import Path

import numpy as np
import pytest

spec = importlib.util.spec_from_file_location(
    'vla_stub_detect', Path(__file__).resolve().parents[1] / 'scripts' / 'vla_stub_detect.py')
V = importlib.util.module_from_spec(spec)
spec.loader.exec_module(V)

K = [462.14, 0, 320.0, 0, 462.14, 240.0, 0, 0, 1]


def _img(blue_box=None, color=(0, 0, 255)):
    img = np.full((480, 640, 3), (120, 80, 50), np.uint8)  # 茶色の机
    if blue_box:
        x0, y0, x1, y1 = blue_box
        img[y0:y1, x0:x1] = color
    return img


def test_blue_mask_matches_opencv_range():
    img = _img((100, 100, 140, 140), color=(13, 25, 230))   # シミュの立方体の色 (0.05,0.1,0.9)
    m = V.blue_mask(img)
    assert m[120, 120] and m.sum() == 40 * 40
    assert not V.blue_mask(_img()).any()


def test_blue_mask_rejects_other_colors():
    for c in [(255, 0, 0), (0, 255, 0), (200, 200, 200), (0, 0, 20)]:
        assert not V.blue_mask(_img((10, 10, 50, 50), color=c))[20, 20]


def test_find_target_centroid_and_none():
    img = _img((100, 200, 140, 240))
    mask = V.blue_mask(img)
    u, v = V.centroid(mask, min_pixels=30)
    assert (u, v) == pytest.approx((119.5, 219.5))
    assert V.centroid(np.zeros((10, 10), bool), min_pixels=30) is None
    tiny = np.zeros((10, 10), bool)
    tiny[1, 1] = True
    assert V.centroid(tiny, min_pixels=30) is None


def test_backproject_center_pixel():
    p = V.backproject(320.0, 240.0, 0.38, K, offset=0.015)
    assert p == pytest.approx((0.0, 0.0, 0.395))


def test_backproject_offset_pixel_uses_z_depth():
    p = V.backproject(320.0 + 46.214, 240.0, 0.40, K, offset=0.0)
    assert p == pytest.approx((0.04, 0.0, 0.40), abs=1e-6)


def test_depth_at_mask_median_ignores_invalid():
    depth = np.zeros((480, 640), np.uint16)
    depth[200:240, 100:140] = 380
    depth[200:210, 100:110] = 0       # 欠け
    depth[220, 120] = 3000            # 外れ値
    mask = np.zeros((480, 640), bool)
    mask[200:240, 100:140] = True
    assert V.depth_at(depth, mask) == pytest.approx(0.380)
    assert V.depth_at(np.zeros((480, 640), np.uint16), mask) is None


def test_range_gate():
    assert V.in_range(0.395, 0.2, 0.5)
    assert not V.in_range(0.55, 0.2, 0.5)
    assert not V.in_range(0.1, 0.2, 0.5)


def test_decode_requires_expected_encodings():
    with pytest.raises(ValueError):
        V.decode_color('bgr8', 1, 1, b'\0\0\0')
    with pytest.raises(ValueError):
        V.decode_depth('32FC1', 1, 1, b'\0\0\0\0')
    assert V.decode_depth('16UC1', 1, 2, b'\x01\x00\x02\x00').tolist() == [[1, 2]]


def test_intrinsics_ok():
    assert V.intrinsics_ok(K)
    assert not V.intrinsics_ok([0, 0, 320, 0, 462, 240])
    assert not V.intrinsics_ok([462, 0, float('nan'), 0, 462, 240])
    assert not V.intrinsics_ok([462, 0, 320])
    assert not V.intrinsics_ok([-1.0, 0, 320, 0, 462, 240])


def test_shaded_cube_faces_still_detected():
    # Gazebo では上面と側面で明るさが違う。公式の下限 V>=30, S>=100 の内側に入る色は拾う
    for c in [(13, 25, 230), (10, 20, 150), (5, 10, 60)]:
        m = V.blue_mask(_img((10, 10, 30, 30), color=c))
        assert m[15, 15], c


def test_lateral_limit_constant_is_sane():
    # 有効距離 0.5m・画角 ±35° なら横方向は 0.35m 程度。上限はその数倍で十分
    assert 0.5 <= V.MAX_LATERAL <= 2.0


def test_in_range_edges():
    assert V.in_range(0.2) and V.in_range(0.5)
    assert not V.in_range(0.1999) and not V.in_range(0.5001)


class _H:
    def __init__(self, frame):
        self.frame_id = frame


class _Img:
    def __init__(self, w=640, h=480, frame='camera_color_optical_frame', big=0):
        self.width, self.height, self.is_bigendian = w, h, big
        self.header = _H(frame)


class _Info:
    def __init__(self, w=640, h=480, k=None):
        self.width, self.height = w, h
        self.k = k or K


FRAME = 'camera_color_optical_frame'


def test_validate_pair_accepts_a_good_triple():
    assert V.validate_pair(_Img(), _Img(), _Info(), FRAME) is None


@pytest.mark.parametrize('color,depth,info,needle', [
    (_Img(frame='evil'), _Img(), _Info(), 'frame_id'),
    (_Img(), _Img(frame='evil'), _Info(), '深度の frame_id'),
    (_Img(), _Img(), _Info(k=[0, 0, 320, 0, 462, 240]), '内部パラメータ'),
    (_Img(w=5000, h=5000), _Img(w=5000, h=5000), _Info(w=5000, h=5000), '大きすぎる'),
    (_Img(big=1), _Img(), _Info(), 'ビッグ'),
    (_Img(), _Img(), _Info(w=320), 'camera_info'),
    (_Img(), _Img(w=320), _Info(), '深度'),
])
def test_validate_pair_rejects(color, depth, info, needle):
    reason = V.validate_pair(color, depth, info, FRAME)
    assert reason and needle in reason


def test_decode_errors_quote_the_untrusted_encoding():
    evil = "rgb8\nbase_link=(0.200,0.100,0.020)"
    for fn in (V.decode_color, V.decode_depth):
        with pytest.raises(ValueError) as e:
            fn(evil, 1, 1, b'')
        assert '\n' not in str(e.value)               # 偽の結果行を 1 行として挿入できない


def test_pixel_cap_is_hd_sized():
    assert V.MAX_PIXELS == 1920 * 1080
