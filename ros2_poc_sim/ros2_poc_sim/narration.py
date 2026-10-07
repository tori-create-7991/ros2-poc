"""動画に出す日本語の説明文（ROS 非依存）。

「いま何をしていて、次は何か」と判定結果を日本語にする。シナリオのステップは
describe_step が自動で作る（Step.description があればそれを使う）。VLA の実行は
vla_record が narration.json を直接書く。判定コード（motion_judge の codes）の日本語化もここ。
"""
import math
import re

from ros2_poc_sim import scenario as S

CONTROL_RE = re.compile(r'[\x00-\x1f\x7f-\x9f  ]')
MAX_LINE = 60          # 「いま」「つぎ」の最大文字数（全角 28px で動画の幅に収まる範囲）
MAX_COMMAND = 150      # 命令（ASCII・小さく出す）の最大文字数
END_TEXT = '終了（判定を表示します）'
GRIPPER_ANGLE_EPS = 0.05


def clean(text, limit=None):
    """制御文字を空白にして前後を整え、limit を超えたら末尾を … にする。"""
    t = CONTROL_RE.sub(' ', str(text)).strip()
    if limit is not None and len(t) > limit:
        t = t[:limit - 1] + '…'
    return t


def describe_step(step):
    """ステップ 1 つの「いま」の説明。"""
    if step.description:
        return clean(step.description, MAX_LINE)
    if step.kind == S.GRIPPER:
        angle = step.points[0][0][0]
        if abs(angle - S.GRIPPER_OPEN) < GRIPPER_ANGLE_EPS:
            return 'グリッパを開く'
        if abs(angle - S.GRIPPER_CLOSE) < GRIPPER_ANGLE_EPS:
            return 'グリッパを閉じる'
        return f'グリッパを {math.degrees(angle):.0f} 度にする'
    joints = len(step.joints) or len(S.ARM_JOINTS)
    if step.origin == 'random':
        return clean(f'ランダムな姿勢「{step.name}」へ動かす（{joints} 関節、{step.duration:g} 秒）', MAX_LINE)
    if len(step.points) > 1:
        return clean(f'経由 {len(step.points)} 点を順に通る「{step.name}」（{step.duration:g} 秒）', MAX_LINE)
    return clean(f'姿勢「{step.name}」へ動かす（{joints} 関節、{step.duration:g} 秒）', MAX_LINE)


def narrate(steps, title='', command_of=None):
    """narration.json の中身。steps は Step のリスト、command_of(step) は命令の短い表示（ASCII）。"""
    now = [describe_step(s) for s in steps]
    out = []
    for i, s in enumerate(steps):
        cmd = clean(command_of(s), MAX_COMMAND) if command_of else ''
        out.append({'now': now[i], 'next': now[i + 1] if i + 1 < len(now) else END_TEXT, 'command': cmd})
    return {'title': clean(title, MAX_LINE), 'steps': out}


def _split(code):
    name, _, rest = code.partition(' ')
    return name, rest


def code_text(code):
    """motion_judge の判定コード（ASCII）→ 日本語の理由。"""
    name, rest = _split(str(code))
    if name == 'joint_err':
        a, _, b = rest.partition('>')
        return f'関節の誤差が大きい（{a} rad > 許容 {b} rad）' if b else '関節の誤差が大きい'
    texts = {
        'no_joint_states': '/joint_states が取れない',
        'joint_missing': '関節の値が欠けている',
        'no_frame': 'カメラの映像が取れない',
        'no_ee_projection': '手先を画像に投影できない',
        'ee_outside_change': '手先が映像の変化した領域の外にある',
        'no_settle_frame': '静止の確認に使う映像が取れない',
        'joints_not_still': '関節が静止しなかった',
        'ee_delta_unknown': '手先の実測位置が取れない',
    }
    if name in texts:
        return texts[name]
    if name == 'no_motion':
        return f'映像に動きが無い（変化 {rest}）'
    if name == 'unexpected_motion':
        return f'動かないはずが映像が変化した（変化 {rest}）'
    if name == 'not_settled':
        return f'判定の時点でまだ動いていた（変化 {rest}）'
    if name == 'send_failed':
        return f'指令を送れなかった（{rest}）' if rest else '指令を送れなかった'
    if name == 'ee_delta_mismatch':
        a, _, b = rest.partition('>')
        return f'手先が指令の位置から外れた（誤差 {a} > 許容 {b}）' if b else '手先が指令の位置から外れた'
    return clean(code, 60)


def verdict_text(r):
    """判定 1 件 → (合格か, 日本語の 1 行)。"""
    if r.get('verdict') == 'PASS':
        parts = []
        if r.get('joint_err') is not None:
            parts.append(f"関節の誤差 {r['joint_err']:.3f} rad")
        if r.get('changed_ratio') is not None:
            parts.append(f"映像の変化 {r['changed_ratio']:.1%}")
        if r.get('ee_error') is not None:
            parts.append(f"手先の誤差 {r['ee_error'] * 1000:.1f} mm")
        return True, '合格 — ' + '・'.join(parts) if parts else '合格'
    codes = r.get('codes') or ['?']
    return False, '不合格 — ' + '、'.join(clean(code_text(c), 80) for c in codes)
