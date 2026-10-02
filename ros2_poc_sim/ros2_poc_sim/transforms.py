"""変換カタログ。rclpy に依存しない純粋関数（numpy のみ）で、ノードから呼ばれる。"""
import numpy as np

_UINT16_MAX_M = 65.535


def passthrough(data: bytes) -> bytes:
    return data


def depth_to_16uc1_mm(depth_m: np.ndarray, near: float, far: float) -> np.ndarray:
    """32FC1(m) → 16UC1(mm)。無効（inf/nan/レンジ外/uint16 を超える）は 0（RealSense と同じ）。"""
    d = np.asarray(depth_m, dtype=np.float32)
    valid = np.isfinite(d) & (d >= near) & (d <= far) & (d <= _UINT16_MAX_M)
    mm = np.zeros(d.shape, dtype=np.uint16)
    mm[valid] = np.rint(d[valid] * 1000.0).astype(np.uint16)
    return mm


def points_to_optical(data: bytes, point_step: int) -> bytes:
    """点群の x,y,z（float32 @0/4/8）を gz のセンサ座標（x前方・y左・z上）から
    光学座標（z前方・x右・y下）へ回す。x_o=-y, y_o=-z, z_o=x。無効点は NaN にする。"""
    arr = np.frombuffer(data, dtype=np.uint8).reshape(-1, point_step).copy()
    xyz = np.ascontiguousarray(arr[:, 0:12]).view('<f4').reshape(-1, 3)
    x, y, z = xyz[:, 0], xyz[:, 1], xyz[:, 2]
    opt = np.stack([-y, -z, x], axis=1)
    bad = ~np.isfinite(xyz).all(axis=1)
    opt[bad] = np.nan
    arr[:, 0:12] = np.ascontiguousarray(opt.astype('<f4')).view(np.uint8).reshape(-1, 12)
    return arr.tobytes()
