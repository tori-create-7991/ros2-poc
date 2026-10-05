"""VLA の手先差分（7 次元）を、アームへ渡す形に直す ROS 非依存の純粋関数。

ros2server の vla_node（差分を受ける側）と vla_converter（アームへ渡す側）が共有する。
DDS は無認証なので、`/vla/action` に届くメッセージは信頼せず、型・有限値・大きさを検査し、
1 ステップの動きと作業空間を制限する（シミュ上のアームを偽の指令で大きく動かさないため）。

契約（標準型のみ。ros2server にカスタムメッセージは作れないので std_msgs/String に JSON を載せる）:
  /vla/action: {"seq": int>=0, "delta": [dx, dy, dz, droll, dpitch, dyaw], "gripper": 0..1}
               delta は base_link 基準、並進 [m]・回転 [rad]（ベース基準の増分）、gripper は 0（閉）〜1（開）
  /vla/ack:    {"seq": int, "status": "ok|rejected|ik_failed|timeout", "reason": str, "detail": {...}}
               detail は任意（記録・判定用。キーは DETAIL_KEYS、値の検査は check_detail）
"""
import json
import math
from dataclasses import dataclass

from ros2_poc_sim import scenario as S

ACTION_TOPIC = '/vla/action'
ACK_TOPIC = '/vla/ack'

MAX_MESSAGE_CHARS = 1024
MAX_ACK_CHARS = 4096         # ack は detail（関節 7 個・位置 3 点など）を載せるので指令より大きい
MAX_RAW_DELTA = 1e3          # これを超える値は異常入力として拒否（クランプ対象にしない）
MAX_LINEAR = 0.03            # 1 ステップの並進の上限 [m]（ノルム）
MAX_ANGULAR = 0.1            # 1 ステップの回転の上限 [rad]（ノルム）
# base_link 基準の作業空間 [m]。初期姿勢の手先は z ≈ 0.62。机や自分自身へ突っ込まない範囲に絞る
WORKSPACE = {'x': (-0.5, 0.5), 'y': (-0.5, 0.5), 'z': (0.05, 0.7)}
GRIPPER_THRESHOLD = 0.5
MAX_DETAIL_DURATION = 60.0   # ack の detail の指令時間の上限 [s]（scenario.MAX_TIME_FROM_START と同じ）
STATUSES = ('ok', 'rejected', 'ik_failed', 'timeout')
# ack の detail（vla_step.StepRunner.last_detail）。どれも任意
DETAIL_KEYS = ('target_joints', 'duration', 't_sent', 'ee_before', 'ee_cmd', 'ee_after', 'ee_error',
               'graph', 'clamped')


@dataclass(frozen=True)
class Action:
    seq: int
    delta: tuple      # (dx, dy, dz, droll, dpitch, dyaw)
    gripper: float    # 0..1


def _is_number(v):
    return not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v)


def _check_delta(values):
    if not isinstance(values, (list, tuple)) or len(values) != 6:
        raise ValueError(f'delta は 6 個の数値のリスト（{values!r}）')
    if not all(_is_number(v) and abs(v) <= MAX_RAW_DELTA for v in values):
        raise ValueError(f'delta に数値でない・有限でない・異常に大きい値がある（{values!r}）')
    return tuple(float(v) for v in values)


def _check_gripper(v):
    if not _is_number(v):
        raise ValueError(f'gripper は有限の数値（{v!r}）')
    return min(1.0, max(0.0, float(v)))


def parse_action(raw):
    """JSON 文字列を検証して Action にする。不正なら ValueError。"""
    if not isinstance(raw, str) or len(raw) > MAX_MESSAGE_CHARS:
        raise ValueError(f'メッセージが大きすぎる（{MAX_MESSAGE_CHARS} 文字超）')
    try:
        d = json.loads(raw)
    except ValueError as e:
        raise ValueError(f'JSON ではない: {e}') from e
    if not isinstance(d, dict):
        raise ValueError('JSON のオブジェクトではない')
    seq = d.get('seq')
    if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0:
        raise ValueError(f'seq は 0 以上の整数（{seq!r}）')
    return Action(seq, _check_delta(d.get('delta')), _check_gripper(d.get('gripper')))


def peek_seq(raw):
    """不正な /vla/action からでも読める seq があれば返す（rejected の ack を返すため）。無ければ None。"""
    if not isinstance(raw, str) or len(raw) > MAX_MESSAGE_CHARS:
        return None
    try:
        d = json.loads(raw)
    except ValueError:
        return None
    seq = d.get('seq') if isinstance(d, dict) else None
    return seq if isinstance(seq, int) and not isinstance(seq, bool) and seq >= 0 else None


def reject_ack_for(raw, error):
    """不正な /vla/action に返す rejected の ack（JSON 文字列）。seq が読めなければ None（返せない）。"""
    seq = peek_seq(raw)
    if seq is None:
        return None
    return format_ack(seq, 'rejected', f'不正な指令: {error}'[:200])


def action_from_vector(seq, vec):
    """VLA が返す 7 次元（dx, dy, dz, droll, dpitch, dyaw, gripper）から Action を作る。"""
    if not isinstance(vec, (list, tuple)) or len(vec) != 7:
        raise ValueError(f'VLA の出力は 7 次元（{len(vec) if hasattr(vec, "__len__") else vec!r}）')
    if not all(_is_number(v) for v in vec):
        raise ValueError('VLA の出力に有限でない値がある')
    return Action(seq, _check_delta(list(vec[:6])), _check_gripper(vec[6]))


def format_action(a):
    return json.dumps({'seq': a.seq, 'delta': list(a.delta), 'gripper': a.gripper})


def _finite_list(v, n, where):
    if not isinstance(v, (list, tuple)) or len(v) != n or not all(_is_number(x) for x in v):
        raise ValueError(f'{where} は {n} 個の有限の数値のリスト')
    return [float(x) for x in v]


def check_detail(detail):
    """ack の detail を検査して、検査済みの dict を返す。不正なら ValueError（未知のキーも不正）。"""
    if not isinstance(detail, dict):
        raise ValueError('detail はオブジェクト')
    unknown = set(detail) - set(DETAIL_KEYS)
    if unknown:
        raise ValueError(f'detail の不明なキー {sorted(unknown)}')
    out = {}
    for k, v in detail.items():
        if k == 'target_joints':
            out[k] = _finite_list(v, len(S.ARM_JOINTS), k)
            if any(not S.ARM_LIMITS[j][0] <= x <= S.ARM_LIMITS[j][1] for j, x in zip(S.ARM_JOINTS, out[k])):
                raise ValueError('target_joints が関節リミットの外')
        elif k in ('ee_before', 'ee_cmd'):
            out[k] = _finite_list(v, 3, k)
        elif k == 'ee_after':
            out[k] = None if v is None else _finite_list(v, 3, k)
        elif k == 'duration':
            if not _is_number(v) or not 0 < v <= MAX_DETAIL_DURATION:
                raise ValueError(f'duration は 0 より大きく {MAX_DETAIL_DURATION} 秒以下')
            out[k] = float(v)
        elif k == 't_sent':
            if not _is_number(v) or v <= 0:
                raise ValueError('t_sent は正の有限の数値（UNIX 時刻）')
            out[k] = float(v)
        elif k == 'ee_error':
            if v is not None and (not _is_number(v) or v < 0):
                raise ValueError('ee_error は 0 以上の有限の数値か null')
            out[k] = None if v is None else float(v)
        elif k == 'graph':
            if v not in ('open', 'close', None):
                raise ValueError(f'graph は open / close / null（{v!r}）')
            out[k] = v
        else:   # clamped
            if not isinstance(v, bool):
                raise ValueError('clamped は真偽値')
            out[k] = v
    return out


def format_ack(seq, status, reason='', detail=None):
    if status not in STATUSES:
        raise ValueError(f'status が不正: {status!r}')
    d = {'seq': seq, 'status': status, 'reason': reason}
    if detail is not None:
        d['detail'] = check_detail(detail)
    return json.dumps(d, ensure_ascii=False)


def parse_ack_full(raw):
    """/vla/ack の JSON を (seq, status, reason, detail | None) にする。不正なら ValueError。"""
    if not isinstance(raw, str) or len(raw) > MAX_ACK_CHARS:
        raise ValueError('ack が大きすぎる')
    try:
        d = json.loads(raw)
    except ValueError as e:
        raise ValueError(f'JSON ではない: {e}') from e
    seq, status, reason = (d.get('seq'), d.get('status'), d.get('reason')) if isinstance(d, dict) else (None,) * 3
    if isinstance(seq, bool) or not isinstance(seq, int) or status not in STATUSES or not isinstance(reason, str):
        raise ValueError(f'ack の形が不正: {raw[:80]!r}')
    detail = None
    if d.get('detail') is not None:
        try:
            detail = check_detail(d['detail'])
        except ValueError:
            detail = None    # 版違いなどで detail だけ不正でも ack（seq・status）は受ける。判定は手先の誤差なしで続く
    return seq, status, reason, detail


def parse_ack(raw):
    """/vla/ack の JSON を (seq, status, reason) にする。不正なら ValueError。detail は parse_ack_full。"""
    return parse_ack_full(raw)[:3]


def _scale_to(values, limit):
    norm = math.sqrt(sum(v * v for v in values))
    if norm <= limit:
        return tuple(values), False
    k = limit / norm
    return tuple(v * k for v in values), True


def clamp_delta(delta):
    """並進・回転それぞれのノルムを上限に収める（向きは保つ）。(delta, クランプしたか) を返す。"""
    lin, c1 = _scale_to(delta[:3], MAX_LINEAR)
    ang, c2 = _scale_to(delta[3:], MAX_ANGULAR)
    return lin + ang, c1 or c2


def _quat_mul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz)


def _normalize(q):
    n = math.sqrt(sum(v * v for v in q))
    if n == 0.0:
        raise ValueError('零クォータニオン')
    return tuple(v / n for v in q)


def rpy_to_quat(roll, pitch, yaw):
    cr, sr = math.cos(roll / 2), math.sin(roll / 2)
    cp, sp = math.cos(pitch / 2), math.sin(pitch / 2)
    cy, sy = math.cos(yaw / 2), math.sin(yaw / 2)
    return (sr * cp * cy - cr * sp * sy,
            cr * sp * cy + sr * cp * sy,
            cr * cp * sy - sr * sp * cy,
            cr * cp * cy + sr * sp * sy)


def apply_delta(position, quat, delta):
    """現在の手先姿勢（base_link 基準の位置と (x, y, z, w) クォータニオン）に差分を足す。

    並進は base_link 基準の加算。回転は base_link 基準の増分（左から掛ける）。
    OpenVLA の差分がどの座標系かはロボットごとに違い、CRANE-X7 では未検証（docs に明記する）。
    """
    pos = tuple(p + d for p, d in zip(position, delta[:3]))
    q = _normalize(_quat_mul(rpy_to_quat(*delta[3:]), _normalize(quat)))
    return pos, q


def clamp_to_workspace(position):
    """作業空間の箱に収める。(position, クランプしたか) を返す。"""
    out = tuple(min(hi, max(lo, v)) for v, (lo, hi) in zip(position, (WORKSPACE['x'], WORKSPACE['y'], WORKSPACE['z'])))
    return out, out != tuple(position)


def gripper_target(value, threshold=GRIPPER_THRESHOLD, open_when_high=True):
    """gripper の値（0..1）を 'open' / 'close' にする。値の意味の向きは OpenVLA の学習データで違う（未検証）。"""
    high = value >= threshold
    return 'open' if high == open_when_high else 'close'


def gripper_angle(name):
    if name == 'open':
        return S.GRIPPER_OPEN
    if name == 'close':
        return S.GRIPPER_CLOSE
    raise ValueError(f'gripper は open / close（{name!r}）')
