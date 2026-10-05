"""動作シナリオ（YAML）の読み込み・検証と、送信コマンドの生成。

ROS に依存しない（Mac の pytest でも動く）。シナリオの書き方は docs/sim-scenario-recording.md。
ステップ種別は positions / waypoints / gripper / random のどれか 1 つ。random は読み込み時に
具体的な関節角の positions ステップへ展開する（seed 固定で再現できる）。
"""
import math
import random
import re
from dataclasses import dataclass, field

import yaml

ARM_TOPIC = '/crane_x7_arm_controller/joint_trajectory'
GRIPPER_ACTION = '/crane_x7_gripper_controller/gripper_cmd'

# crane_x7_description の URDF（xacro 展開後）の関節リミット [rad]
ARM_LIMITS = {
    'crane_x7_shoulder_fixed_part_pan_joint': (-2.7401669256310974, 2.7401669256310974),
    'crane_x7_shoulder_revolute_part_tilt_joint': (-1.6057029118347832, 1.6057029118347832),
    'crane_x7_upper_arm_revolute_part_twist_joint': (-2.7401669256310974, 2.7401669256310974),
    'crane_x7_upper_arm_revolute_part_rotate_joint': (-2.8099800957108707, 1.7453292519943296e-05),
    'crane_x7_lower_arm_fixed_part_joint': (-2.7401669256310974, 2.7401669256310974),
    'crane_x7_lower_arm_revolute_part_joint': (-1.5707963267948966, 1.5707963267948966),
    'crane_x7_wrist_joint': (-2.91469985083053, 2.91469985083053),
}
ARM_JOINTS = list(ARM_LIMITS)
GRIPPER_JOINT = 'crane_x7_gripper_finger_a_joint'
GRIPPER_LIMITS = (-0.08726646259971647, 1.5707963267948966)
# 公式 crane_x7_examples の pick_and_place と同じ開閉角
GRIPPER_OPEN = math.radians(60.0)
GRIPPER_CLOSE = 0.0

ARM_TOLERANCE = 0.05
GRIPPER_TOLERANCE = 0.1   # crane_x7_gripper_controller の goal_tolerance と同じ
RANDOM_SCALE = 0.5        # random はリミット幅の中央 50% だけを使う（机や自分への衝突を避ける）
SETTLE_SEC = 1.5          # 関節が静止してから判定するまでの待ち（カメラ映像は /joint_states より 1〜1.5s 遅れる。実測）
SETTLE_WINDOW_SEC = 1.0   # 静止判定に使う、判定時刻の後ろの区間（カメラは 2〜3fps なので別のフレームになる長さ）
NAME_RE = re.compile(r'^[A-Za-z0-9_\-]{1,40}$')
MAX_STEPS = 200
MAX_TIME_FROM_START = 60.0

ARM, GRIPPER = 'arm', 'gripper'


@dataclass
class Step:
    name: str
    kind: str                       # 'arm' | 'gripper'
    points: list                    # [(positions(tuple), time_from_start)]。gripper は [((angle,), 0.0)]
    expect: tuple                   # 判定に使う期待値（既定は最後の点）
    tolerance: float
    joints: list = field(default_factory=list)

    @property
    def duration(self):
        """送信完了から動作が終わるまでの秒数。gripper は send_goal が結果を待つので 0。"""
        return self.points[-1][1]

    @classmethod
    def from_dict(cls, d):
        return cls(d['name'], d['kind'],
                   [(tuple(p['positions']), p['time_from_start']) for p in d['points']],
                   tuple(d['expect']), d['tolerance'], list(d['joints']))

    def to_dict(self):
        return {'name': self.name, 'kind': self.kind, 'joints': list(self.joints),
                'points': [{'positions': list(p), 'time_from_start': t} for p, t in self.points],
                'expect': list(self.expect), 'tolerance': self.tolerance,
                'duration': self.duration}


def _num(v, where):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ValueError(f'{where}: 数値ではない（{v!r}）')
    return float(v)


def _arm_positions(v, where):
    if not isinstance(v, (list, tuple)) or len(v) != len(ARM_JOINTS):
        raise ValueError(f'{where}: 関節角は {len(ARM_JOINTS)} 個のリストで書く（{v!r}）')
    out = []
    for i, (x, joint) in enumerate(zip(v, ARM_JOINTS)):
        x = _num(x, f'{where}[{i}]')
        lo, hi = ARM_LIMITS[joint]
        if not lo <= x <= hi:
            raise ValueError(f'{where}[{i}]: {joint} のリミット [{lo:.3f}, {hi:.3f}] の外（{x}）')
        out.append(x)
    return tuple(out)


def _time(v, where):
    t = _num(v, where)
    if not 0 < t <= MAX_TIME_FROM_START:
        raise ValueError(f'{where}: time_from_start は 0 より大きく {MAX_TIME_FROM_START} 以下（{t}）')
    return t


def _gripper_angle(v, where):
    if v == 'open':
        return GRIPPER_OPEN
    if v == 'close':
        return GRIPPER_CLOSE
    x = _num(v, where)
    lo, hi = GRIPPER_LIMITS
    if not lo <= x <= hi:
        raise ValueError(f'{where}: グリッパのリミット [{lo:.3f}, {hi:.3f}] の外（{x}）')
    return x


def _parse_step(d, idx):
    where = f'steps[{idx}]'
    if not isinstance(d, dict):
        raise ValueError(f'{where}: マッピングで書く')
    name = str(d.get('name', f'step{idx + 1}'))
    if not NAME_RE.match(name):
        raise ValueError(f'{where}: name は英数字・_・- で 40 文字以内（{name!r}）')
    kinds = [k for k in ('positions', 'waypoints', 'gripper', 'random') if k in d]
    if len(kinds) != 1:
        raise ValueError(f'{where}: positions / waypoints / gripper / random のどれか 1 つだけを書く（{kinds}）')
    kind = kinds[0]
    unknown = set(d) - {'name', 'tolerance', 'expect', 'time_from_start', kind}
    if unknown:
        raise ValueError(f'{where}: 不明なキー {sorted(unknown)}')

    if kind == 'gripper':
        angle = _gripper_angle(d['gripper'], f'{where}.gripper')
        expect = (_gripper_angle(d['expect'], f'{where}.expect'),) if 'expect' in d else (angle,)
        tol = _num(d.get('tolerance', GRIPPER_TOLERANCE), f'{where}.tolerance')
        return [Step(name, GRIPPER, [((angle,), 0.0)], expect, tol, [GRIPPER_JOINT])]

    tol = _num(d.get('tolerance', ARM_TOLERANCE), f'{where}.tolerance')
    if kind == 'random':
        r = d['random']
        if not isinstance(r, dict):
            raise ValueError(f'{where}.random: {{n, seed, time_from_start}} で書く')
        n = r.get('n', 1)
        if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= 50:
            raise ValueError(f'{where}.random.n: 1〜50 の整数（{n!r}）')
        rng = random.Random(r.get('seed', 0))
        t = _time(r.get('time_from_start', 3), f'{where}.random.time_from_start')
        steps = []
        for i in range(n):
            pos = []
            for joint in ARM_JOINTS:
                lo, hi = ARM_LIMITS[joint]
                mid, half = (lo + hi) / 2, (hi - lo) / 2 * RANDOM_SCALE
                pos.append(round(rng.uniform(mid - half, mid + half), 4))
            pos = tuple(pos)
            steps.append(Step(f'{name}_{i + 1}', ARM, [(pos, t)], pos, tol, list(ARM_JOINTS)))
        return steps

    if kind == 'positions':
        points = [(_arm_positions(d['positions'], f'{where}.positions'),
                   _time(d.get('time_from_start', 3), f'{where}.time_from_start'))]
    else:
        wps = d['waypoints']
        if not isinstance(wps, list) or not wps:
            raise ValueError(f'{where}.waypoints: 1 点以上のリストで書く')
        points, prev = [], 0.0
        for j, wp in enumerate(wps):
            w = f'{where}.waypoints[{j}]'
            if not isinstance(wp, dict) or set(wp) != {'positions', 'time_from_start'}:
                raise ValueError(f'{w}: {{positions, time_from_start}} で書く')
            t = _time(wp['time_from_start'], f'{w}.time_from_start')
            if t <= prev:
                raise ValueError(f'{w}: time_from_start は単調増加にする（{t} <= {prev}）')
            points.append((_arm_positions(wp['positions'], f'{w}.positions'), t))
            prev = t
    expect = _arm_positions(d['expect'], f'{where}.expect') if 'expect' in d else points[-1][0]
    return [Step(name, ARM, points, expect, tol, list(ARM_JOINTS))]


def parse_scenario(data, repeat=None):
    """YAML を読んだ dict → Step のリスト（random 展開・repeat 適用済み）。"""
    if not isinstance(data, dict) or not isinstance(data.get('steps'), list) or not data['steps']:
        raise ValueError('シナリオは steps: に 1 つ以上のステップを書く')
    unknown = set(data) - {'steps', 'repeat', 'description'}
    if unknown:
        raise ValueError(f'不明なキー {sorted(unknown)}')
    n = data.get('repeat', 1) if repeat is None else repeat
    if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= 20:
        raise ValueError(f'repeat は 1〜20 の整数（{n!r}）')
    base = [s for i, d in enumerate(data['steps']) for s in _parse_step(d, i)]
    if len(base) * n > MAX_STEPS:
        raise ValueError(f'展開後のステップ数が多すぎる（{len(base) * n} > {MAX_STEPS}）')
    if n == 1:
        return base
    out = []
    for k in range(1, n + 1):
        for s in base:
            out.append(Step(f'{s.name}#{k}', s.kind, s.points, s.expect, s.tolerance, s.joints))
    return out


def load_scenario(path, repeat=None):
    with open(path, encoding='utf-8') as f:
        return parse_scenario(yaml.safe_load(f), repeat)


def fmt(v):
    s = f'{v:.6f}'.rstrip('0').rstrip('.')
    return '0' if s in ('', '-0') else s


def _duration_msg(t):
    sec = int(t)
    return f'{{sec: {sec}, nanosec: {int(round((t - sec) * 1e9))}}}'


def to_command(step):
    """(送信先コンテナ, コマンド文字列)。アームは ros2lab-a から README と同じ topic pub、
    グリッパは ros2lab に control_msgs が無いので ros2arm から action send_goal。"""
    if step.kind == GRIPPER:
        goal = (f'{{command: {{name: [{GRIPPER_JOINT}], '
                f'position: [{fmt(step.points[0][0][0])}]}}}}')
        return 'ros2arm', (f'ros2 action send_goal {GRIPPER_ACTION} '
                           f'control_msgs/action/ParallelGripperCommand "{goal}"')
    pts = ', '.join(f'{{positions: [{", ".join(fmt(x) for x in p)}], '
                    f'time_from_start: {_duration_msg(t)}}}' for p, t in step.points)
    msg = f'{{joint_names: [{", ".join(ARM_JOINTS)}], points: [{pts}]}}'
    # --once だと送信直後に終了し、相手側の Discovery が終わっていないと落ちることがある（実測）。
    # 0.5 秒間隔で 3 回送る。コントローラは同じ目標の軌道に置き換えるだけなので最終姿勢は変わらない
    return 'ros2lab-a', (f'ros2 topic pub -w 1 --times 3 -r 2 {ARM_TOPIC} '
                         f'trajectory_msgs/msg/JointTrajectory "{msg}"')


def display_lines(step):
    """動画の下帯に出す短い命令表示（関節名は省き、送る値だけを見せる）。全文は commands.log。"""
    if step.kind == GRIPPER:
        return [f'$ ros2 action send_goal {GRIPPER_ACTION} control_msgs/action/ParallelGripperCommand',
                f'    {GRIPPER_JOINT} -> {fmt(step.points[0][0][0])} rad']
    pts = '  '.join(f'[{", ".join(fmt(x) for x in p)}] @{fmt(t)}s' for p, t in step.points)
    return [f'$ ros2 topic pub -w 1 --times 3 -r 2 {ARM_TOPIC} trajectory_msgs/msg/JointTrajectory',
            f'    positions: {pts}']


def wait_after_send(step):
    """送信完了からの最低の待ち秒（目安）。実際は scenario_cli wait が /joint_states の静止まで待つ。"""
    return step.duration + SETTLE_SEC + SETTLE_WINDOW_SEC + 0.5
