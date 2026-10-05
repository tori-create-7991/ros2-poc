"""姿勢計算（numpy のみ）。TF と Gazebo の描画位置を同じ数値から導くための関数群。"""
import math

import numpy as np


def rpy_to_matrix(roll: float, pitch: float, yaw: float) -> np.ndarray:
    cr, sr = math.cos(roll), math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    return np.array([
        [cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
        [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
        [-sp, cp * sr, cp * cr],
    ])


def matrix_to_rpy(R: np.ndarray):
    pitch = -math.asin(max(-1.0, min(1.0, R[2, 0])))
    if abs(R[2, 0]) < 0.999999:
        roll = math.atan2(R[2, 1], R[2, 2])
        yaw = math.atan2(R[1, 0], R[0, 0])
    else:  # ジンバルロック
        roll = math.atan2(-R[1, 2], R[1, 1])
        yaw = 0.0
    return (roll, pitch, yaw)


def matrix_to_quat(R: np.ndarray):
    """(x, y, z, w)"""
    t = R[0, 0] + R[1, 1] + R[2, 2]
    if t > 0:
        s = math.sqrt(t + 1.0) * 2
        w = 0.25 * s
        x = (R[2, 1] - R[1, 2]) / s
        y = (R[0, 2] - R[2, 0]) / s
        z = (R[1, 0] - R[0, 1]) / s
    elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
        s = math.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2]) * 2
        w = (R[2, 1] - R[1, 2]) / s
        x = 0.25 * s
        y = (R[0, 1] + R[1, 0]) / s
        z = (R[0, 2] + R[2, 0]) / s
    elif R[1, 1] > R[2, 2]:
        s = math.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2]) * 2
        w = (R[0, 2] - R[2, 0]) / s
        x = (R[0, 1] + R[1, 0]) / s
        y = 0.25 * s
        z = (R[1, 2] + R[2, 1]) / s
    else:
        s = math.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1]) * 2
        w = (R[1, 0] - R[0, 1]) / s
        x = (R[0, 2] + R[2, 0]) / s
        y = (R[1, 2] + R[2, 1]) / s
        z = 0.25 * s
    q = np.array([x, y, z, w])
    return q / np.linalg.norm(q)


def look_at_matrix(eye, target, up_hint) -> np.ndarray:
    """camera_link（x 前方・y 左・z 上）の姿勢。x 軸が eye→target を向く。
    up_hint は画像の上方向の目安（x と平行なら別の軸にフォールバックしない: 設定ミスとして例外）。"""
    eye, target, up = (np.asarray(v, dtype=float) for v in (eye, target, up_hint))
    f = target - eye
    n = np.linalg.norm(f)
    if n < 1e-9:
        raise ValueError('eye と target が同じ点')
    x = f / n
    y = np.cross(up, x)
    ny = np.linalg.norm(y)
    if ny < 1e-6:
        raise ValueError('up_hint が視線方向と平行')
    y = y / ny
    z = np.cross(x, y)
    return np.stack([x, y, z], axis=1)


def _compose(a, b):
    (pa, Ra), (pb, Rb) = a, b
    return (pa + Ra @ pb, Ra @ Rb)


def camera_link_pose_in_base(placement: dict):
    R = look_at_matrix(placement['look_at']['eye'], placement['look_at']['target'],
                       placement['look_at']['up_hint'])
    return (np.array(placement['look_at']['eye'], dtype=float), R)


def camera_link_pose_in_world(placement: dict):
    base = placement['robot_base_in_world']
    base_pose = (np.array(base['xyz'], dtype=float), rpy_to_matrix(*base['rpy']))
    return _compose(base_pose, camera_link_pose_in_base(placement))


def frame_in_camera_link(profile: dict, frame: str):
    """camera_link から見た frame の姿勢（tf.links を辿る）。"""
    root = profile['frames']['camera_link']
    if frame == root:
        return np.zeros(3), np.eye(3)
    parent_of = {ln['child']: ln for ln in profile['tf']['links']}
    chain = []
    cur = frame
    while cur != root:
        ln = parent_of.get(cur)
        if ln is None:
            raise KeyError(f'{frame} は camera_link から辿れない')
        chain.append(ln)
        cur = ln['parent']
    pose = (np.zeros(3), np.eye(3))
    for ln in reversed(chain):
        pose = _compose(pose, (np.array(ln['xyz'], dtype=float), rpy_to_matrix(*ln['rpy'])))
    return pose


def sensor_pose_in_world(profile: dict, placement: dict):
    key = profile['tf']['gz_sensor_frame']
    off = frame_in_camera_link(profile, profile['frames'][key])
    return _compose(camera_link_pose_in_world(placement), off)


def world_to_base_offset(placement: dict):
    return np.array(placement['robot_base_in_world']['xyz'], dtype=float)
