"""シナリオ実行の性能指標（RTF・所要時間・カメラ fps）。ROS 非依存の純関数。

clock.csv（observer が約 1Hz で記録）: wall,sim（壁時計 [s] と /clock の sim time [s]）
RTF = Δsim / Δwall。1.0 未満はシミュが実時間より遅い。
"""
import bisect
import csv
import json
from pathlib import Path

MIN_SPAN_SEC = 1.0   # これより短い区間の RTF は誤差が大きいので出さない


def read_clock_csv(path):
    """clock.csv を [(wall, sim)] で返す。無い・壊れた行は捨てる。wall が戻る行も捨てる。"""
    p = Path(path)
    if not p.exists():
        return []
    rows = []
    with p.open(encoding='utf-8') as f:
        for r in csv.DictReader(f):
            try:
                w, s = float(r['wall']), float(r['sim'])
            except (KeyError, TypeError, ValueError):
                continue
            if rows and w <= rows[-1][0]:
                continue
            rows.append((w, s))
    return rows


def rtf(rows, t0=None, t1=None):
    """壁時計 [t0, t1] の区間の RTF。区間が MIN_SPAN_SEC 未満・データ不足なら None。"""
    sel = [r for r in rows if (t0 is None or r[0] >= t0) and (t1 is None or r[0] <= t1)]
    if len(sel) < 2 or sel[-1][0] - sel[0][0] < MIN_SPAN_SEC:
        return None
    return (sel[-1][1] - sel[0][1]) / (sel[-1][0] - sel[0][0])


def rtf_min(rows, window=5):
    """window 行ごとの区間 RTF の最小。窓が取れなければ None。"""
    vals = [rtf(rows[i:i + window + 1]) for i in range(0, max(len(rows) - window, 0))]
    vals = [v for v in vals if v is not None]
    return min(vals) if vals else None


def sim_at(rows, wall):
    """wall 時点の sim time を線形補間する。範囲外・データ無しは None。"""
    if not rows or wall < rows[0][0] or wall > rows[-1][0]:
        return None
    i = bisect.bisect_left([r[0] for r in rows], wall)
    if rows[i][0] == wall:
        return rows[i][1]
    (w0, s0), (w1, s1) = rows[i - 1], rows[i]
    return s0 + (s1 - s0) * (wall - w0) / (w1 - w0)


def camera_fps(frame_times):
    """フレーム受信時刻 [s] の列から平均 fps。2 枚未満・区間 0 なら None。"""
    if len(frame_times) < 2 or frame_times[-1] <= frame_times[0]:
        return None
    return (len(frame_times) - 1) / (frame_times[-1] - frame_times[0])


def _r(v, nd=3):
    return None if v is None else round(v, nd)


def summarize(clock_rows, frame_times, steps):
    """result.json の performance。steps は t_start / t_end（壁時計）を持つ判定結果のリスト。"""
    out_steps = []
    for st in steps:
        t0, t1 = st.get('t_start'), st.get('t_end')
        if not t0 or t1 is None:
            continue
        s0, s1 = sim_at(clock_rows, t0), sim_at(clock_rows, t1)
        out_steps.append({'index': st.get('index'), 'name': st.get('name'),
                          'wall_sec': _r(t1 - t0),
                          'sim_sec': None if s0 is None or s1 is None else _r(s1 - s0)})
    ts = [st['t_start'] for st in steps if st.get('t_start')]
    te = [st['t_end'] for st in steps if st.get('t_end') is not None]
    wall = (max(te) - (frame_times[0] if frame_times else min(ts))) if te and (frame_times or ts) else None
    return {'rtf': _r(rtf(clock_rows)), 'rtf_min': _r(rtf_min(clock_rows)),
            'wall_sec': _r(wall), 'camera_fps': _r(camera_fps(frame_times)), 'steps': out_steps}


def load_run(run_dir):
    """比較表用に run_dir の run_meta.json と result.json の performance を読む。"""
    d = Path(run_dir)
    meta, res = {}, {}
    for name, dst in (('run_meta.json', meta), ('result.json', res)):
        p = d / name
        if p.exists():
            try:
                dst.update(json.loads(p.read_text(encoding='utf-8')))
            except json.JSONDecodeError:
                pass
    perf = res.get('performance') or {}
    return {'run': d.name, 'label': meta.get('label') or '-', 'cpus': meta.get('cpus'),
            'verdict': res.get('verdict', '-'), **{k: perf.get(k) for k in ('rtf', 'rtf_min', 'wall_sec', 'camera_fps')}}


def format_table(rows):
    cols = [('run', 'run'), ('label', 'label'), ('cpus', 'cpus'), ('rtf', 'RTF'), ('rtf_min', 'RTF最小'),
            ('wall_sec', 'wall[s]'), ('camera_fps', 'cam[fps]'), ('verdict', '判定')]

    def cell(v):
        return '-' if v is None else (f'{v:.2f}' if isinstance(v, float) else str(v))
    table = [[h for _, h in cols]] + [[cell(r.get(k)) for k, _ in cols] for r in rows]
    widths = [max(len(row[i]) for row in table) for i in range(len(cols))]
    return '\n'.join('  '.join(c.ljust(w) for c, w in zip(row, widths)).rstrip() for row in table)
