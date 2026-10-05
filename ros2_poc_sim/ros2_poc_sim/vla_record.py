"""VLA の実行記録（vla_steps.jsonl）を、シナリオと同じ判定・動画合成の入力に変える（ROS 非依存）。

run-vla.sh --record が vla_node に --record-file を渡し、vla_node がステップごとに 1 行ずつ書く。
scenario_cli vla-prepare がそれを steps.json / events.jsonl / narration.json に直し、以降は
scenario_cli wait → judge → compose（シナリオと同じ）が使う。
判定は「指令どおり動いたか」だけを見る（関節・映像・静止・手先が指令位置どおりか）。VLA の出力の良し悪しは見ない。
"""
import json
import math
import re
from pathlib import Path

from ros2_poc_sim import narration as N
from ros2_poc_sim import scenario as S
from ros2_poc_sim import vla_action as A

MAX_RECORDS = 100
MAX_INSTRUCTION = 200
EE_TOLERANCE = 0.01        # 手先の実測と指令位置の許容誤差 [m]（10 mm）
JOINT_TOLERANCE = S.ARM_TOLERANCE
MIN_SHOWN_M = 0.0005       # これ未満の手先の移動量は説明に出さない
SENT_STATUSES = ('ok', 'timeout')   # 腕へ指令を送った（rejected / ik_failed は送っていない）
PATH_RE = re.compile(r'^/[A-Za-z0-9_./-]{1,200}\.jsonl$')
FALLBACK_JOINTS = [0.0] * len(S.ARM_JOINTS)


def validate_record_path(path):
    """--record-file のパス検査。絶対パス・.jsonl・'..' なし・親ディレクトリが既にある。"""
    if not isinstance(path, str) or not PATH_RE.match(path) or '..' in Path(path).parts:
        raise ValueError(f'--record-file は /…/名前.jsonl の絶対パス（英数字・_・.・/・- のみ、.. なし）: {path!r}')
    if not Path(path).parent.is_dir():
        raise ValueError(f'--record-file の親ディレクトリが無い: {Path(path).parent}')
    return path


def make_record(info, instruction):
    """vla_loop.run_loop の on_step の info → vla_steps.jsonl の 1 行分（dict）。"""
    return {'index': info['index'], 'seq': info['seq'], 'instruction': instruction,
            'vector': info['vector'], 'status': info['status'], 'reason': info['reason'],
            'detail': info.get('detail'), 't_start': info['t_start']}


def append_record(path, record):
    with open(path, 'a', encoding='utf-8') as f:
        f.write(json.dumps(record, ensure_ascii=False) + '\n')
        f.flush()


def _is_num(v):
    return not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v)


def _check_record(rec, expect_index):
    if not isinstance(rec, dict):
        raise ValueError('記録の行がオブジェクトではない')
    if rec.get('index') != expect_index or isinstance(rec.get('index'), bool):
        raise ValueError(f'index が 0 から連続していない（{rec.get("index")!r}、期待 {expect_index}）')
    if isinstance(rec.get('seq'), bool) or not isinstance(rec.get('seq'), int):
        raise ValueError('seq が整数ではない')
    if rec.get('status') not in A.STATUSES:
        raise ValueError(f'status が不正: {rec.get("status")!r}')
    if not isinstance(rec.get('reason'), str) or not isinstance(rec.get('instruction'), str):
        raise ValueError('reason / instruction が文字列ではない')
    vec = rec.get('vector')
    if not isinstance(vec, list) or len(vec) != 7 or not all(_is_num(v) for v in vec):
        raise ValueError('vector は 7 個の有限の数値')
    if not _is_num(rec.get('t_start')):
        raise ValueError('t_start が数値ではない')
    detail = A.check_detail(rec['detail']) if rec.get('detail') is not None else {}
    return {**rec, 'detail': detail, 'instruction': N.clean(rec['instruction'], MAX_INSTRUCTION)}


def read_records(path):
    """vla_steps.jsonl を検査しながら読む。空・不正・多すぎるなら ValueError。"""
    recs = []
    for n, line in enumerate(Path(path).read_text(encoding='utf-8').splitlines(), 1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except ValueError as e:
            raise ValueError(f'{n} 行目が JSON ではない: {e}') from e
        recs.append(_check_record(raw, len(recs)))
        if len(recs) > MAX_RECORDS:
            raise ValueError(f'ステップが多すぎる（{MAX_RECORDS} 超）')
    if not recs:
        raise ValueError('ステップが 1 つも記録されていない（VLA がステップを実行する前に止まった）')
    return recs


def move_text(before, cmd):
    """手先の指令の移動量（base_link 基準）を日本語にする。例: 「下へ 2.0cm」。"""
    names = (('前', '後ろ'), ('左', '右'), ('上', '下'))   # x+ = 前、y+ = 左、z+ = 上（REP-103）
    parts = []
    for (plus, minus), b, c in zip(names, before, cmd):
        d = c - b
        if abs(d) >= MIN_SHOWN_M:
            parts.append(f'{plus if d > 0 else minus}へ {abs(d) * 100:.1f}cm')
    return '、'.join(parts) if parts else 'ほぼ動かさない'


def _now_text(rec):
    d = rec['detail']
    ok = rec['status'] in SENT_STATUSES
    if 'ee_before' in d and 'ee_cmd' in d:
        move = move_text(d['ee_before'], d['ee_cmd'])
    else:
        dx, dy, dz = rec['vector'][:3]
        move = move_text((0, 0, 0), (dx, dy, dz))
    text = f'VLA の出力で手先を動かす: {move}'
    if d.get('graph') == 'open':
        text += '、グリッパを開く'
    elif d.get('graph') == 'close':
        text += '、グリッパを閉じる'
    if d.get('clamped'):
        text += '（上限にクランプ）'
    if not ok:
        text += f'（{rec["status"]}: {N.clean(rec["reason"], 30)}）'
    return N.clean(text, N.MAX_LINE)


def _command_text(rec):
    vec = ' '.join(f'{v:.4f}' for v in rec['vector'])
    return f'VLA "{rec["instruction"]}" -> [{vec}]'


def prepare(path):
    """vla_steps.jsonl → {'steps': steps.json, 'events': events.jsonl の行, 'narration': narration.json,
    'sent': 指令を送ったステップの番号（rejected / ik_failed は送っていない）}。"""
    recs = read_records(path)
    steps, events, now = [], [], []
    for rec in recs:
        d = rec['detail']
        sent = rec['status'] in SENT_STATUSES and 'target_joints' in d
        q = d['target_joints'] if 'target_joints' in d else FALLBACK_JOINTS
        duration = d.get('duration', 1.0)
        name = f'vla_{rec["index"] + 1:03d}'
        text = _now_text(rec)
        step = S.Step(name, S.ARM, [(tuple(q), duration)], tuple(q), JOINT_TOLERANCE,
                      list(S.ARM_JOINTS), text).to_dict()
        # 手先が指令位置どおりか（motion_judge.judge_step が見る）。実測が取れていなければ FAIL にする
        step['ee_error'] = d.get('ee_error')
        step['ee_tolerance'] = EE_TOLERANCE
        steps.append(step)
        events.append({'index': rec['index'], 'name': name, 'target': 'sim', 't_start': rec['t_start'],
                       't_sent': d.get('t_sent', rec['t_start']), 'rc': 0 if sent else 1})
        now.append(text)
    narration = {'title': N.clean(f'VLA への指示: 「{recs[0]["instruction"]}」（{len(recs)} ステップ）', N.MAX_LINE),
                 'steps': [{'now': now[i], 'next': now[i + 1] if i + 1 < len(now) else N.END_TEXT,
                            'command': N.clean(_command_text(rec), N.MAX_COMMAND)}
                           for i, rec in enumerate(recs)]}
    sent = [e['index'] for e in events if e['rc'] == 0]
    return {'steps': steps, 'events': events, 'narration': narration, 'sent': sent}
