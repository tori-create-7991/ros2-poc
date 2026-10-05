"""記録データから「指令どおり動いたか」をステップごとに判定する（numpy + ffmpeg のみ）。

ros2arm の cv2 は NumPy 2 と非互換なので使わない。条件（全部 OK で PASS）:
  1. 関節: 判定時刻の /joint_states と期待値の差が許容内
  2. 映像で動いた: 送信前と判定時刻のカメラフレームの差分画素率がしきい値以上
     （期待姿勢が送信前と同じなら「動いていないこと」に反転）
  3. 映像と姿勢の一致: 手先 TF を camera_info で投影した画素が差分領域（余白付き）の中
  4. 静止: 判定時刻と SETTLE_WINDOW 後のフレームの差分画素率が小さい
"""
import bisect
import csv
import json
import math
import subprocess
from pathlib import Path

import numpy as np

from ros2_poc_sim import scenario as S

DIFF_THRESH = 25           # 画素の変化とみなす差（0-255、各チャンネルの最大）
MOTION_MIN = 0.005         # アーム: 動いたとみなす変化画素率
GRIPPER_MOTION_MIN = 0.001  # グリッパは画面上で小さいので下げる
SETTLE_MAX = 0.002         # 静止とみなす変化画素率の上限
BBOX_MARGIN = 40           # 差分領域の外接矩形に足す余白 [px]
MAX_GAP_SEC = 1.5          # /joint_states・TF の最近傍がこれより離れていたら「記録なし」
FRAME_MAX_GAP_SEC = 10.0   # カメラフレームの許容間隔。負荷が高いと 1fps 以下、間隔 7 秒まで落ちる（実測）
STILL_EPS = 0.002          # 静止とみなす関節角の変化 [rad]（STILL_WINDOW の間の最大変化）
STILL_WINDOW = 0.8         # 静止判定の区間 [s]（/joint_states は負荷時 2Hz 程度まで落ちる）
SETTLE_TIMEOUT_FACTOR = 5  # 静止を待つ上限 = 指令時間 × これ + SETTLE_TIMEOUT_EXTRA（シミュは実時間より遅い）
SETTLE_TIMEOUT_EXTRA = 10.0
SETTLE_SEC = 1.5           # 関節が静止してから判定するまでの待ち（カメラ映像は /joint_states より 1〜1.5s 遅れる。実測）
SETTLE_WINDOW_SEC = 1.0    # 静止確認のフレームを、判定時刻からこれ以上あとに取る
# 記録の上限（DDS は無認証。巨大・高頻度の画像でディスク・メモリを使い切られないため。scenario_observer が使う）
MAX_PIXELS = 1920 * 1080
MAX_FPS = 30.0             # シミュは 1〜3fps
INCOMPLETE_MARK = 'camera.incomplete'   # observer が camera.mp4 を書き終えられなかった目印


def valid_camera_info(k, width, height, image_size=None):
    """CameraInfo を判定・合成に使えるか（無認証の DDS から来るので値を信用しない）。"""
    if not 0 < width * height <= MAX_PIXELS:
        return False
    if image_size is not None and (width, height) != image_size:
        return False
    return len(k) == 9 and all(math.isfinite(x) for x in k) and k[0] > 0 and k[4] > 0


def diff_mask(a, b, thresh=DIFF_THRESH):
    if a.shape != b.shape:
        raise ValueError(f'フレームの大きさが違う（{a.shape} と {b.shape}）')
    d = np.abs(a.astype(np.int16) - b.astype(np.int16))
    return d.max(axis=-1) > thresh if d.ndim == 3 else d > thresh


def changed_ratio(mask):
    return float(mask.mean()) if mask.size else 0.0


def bbox(mask, margin=BBOX_MARGIN):
    """変化画素の外接矩形 (x0, y0, x1, y1)（両端含む、画像内に収める）。変化なしは None。"""
    ys, xs = np.nonzero(mask)
    if xs.size == 0:
        return None
    h, w = mask.shape
    return (max(int(xs.min()) - margin, 0), max(int(ys.min()) - margin, 0),
            min(int(xs.max()) + margin, w - 1), min(int(ys.max()) + margin, h - 1))


def project(xyz, K):
    """カメラ光学座標の点 → 画素 (u, v)。カメラの後ろ（z <= 0）は None。"""
    x, y, z = xyz
    if z <= 1e-6:
        return None
    return (K[0] * x / z + K[2], K[4] * y / z + K[5])


def joint_error(actual, joints, expected):
    missing = [j for j in joints if j not in actual]
    if missing:
        raise ValueError(f'/joint_states に関節が無い: {missing}')
    return max(abs(actual[j] - e) for j, e in zip(joints, expected))


def judge_step(*, joints, expected, tolerance, actual_before, actual_after,
               frame_before, frame_after, frame_settled, ee_xyz, K, motion_min):
    """1 ステップの判定。入力が欠けていたら、その条件を FAIL にして理由を残す。"""
    reasons, codes = [], []

    def fail(code, reason):
        codes.append(code)
        reasons.append(reason)

    res = {'joint_err': None, 'changed_ratio': None, 'settle_ratio': None,
           'ee_px': None, 'bbox': None, 'expect_motion': None}

    if actual_after is None:
        fail('no_joint_states', '判定時刻の /joint_states が無い')
    else:
        try:
            err = joint_error(actual_after, joints, expected)
            res['joint_err'] = err
            if err > tolerance:
                fail(f'joint_err {err:.3f}>{tolerance:.3f}', f'関節誤差 {err:.3f} rad > 許容 {tolerance:.3f}')
        except ValueError as e:
            fail('joint_missing', str(e))

    expect_motion = True
    if actual_before is not None:
        try:
            expect_motion = joint_error(actual_before, joints, expected) > tolerance
        except ValueError:
            pass
    res['expect_motion'] = expect_motion

    if frame_before is None or frame_after is None:
        fail('no_frame', '判定に使うカメラフレームが無い')
    else:
        mask = diff_mask(frame_before, frame_after)
        ratio = changed_ratio(mask)
        res['changed_ratio'] = ratio
        box = bbox(mask)
        res['bbox'] = box
        if expect_motion and ratio < motion_min:
            fail(f'no_motion {ratio:.2%}', f'映像に動きが無い（変化 {ratio:.2%} < {motion_min:.2%}）')
        if not expect_motion and ratio >= motion_min:
            fail(f'unexpected_motion {ratio:.2%}', f'動かないはずが映像が変化した（変化 {ratio:.2%}）')
        if expect_motion:
            px = project(ee_xyz, K) if ee_xyz is not None and K is not None else None
            res['ee_px'] = px
            if px is None:
                fail('no_ee_projection', '手先の位置を画像に投影できない（TF / camera_info なし、またはカメラの後ろ）')
            elif box is not None and not (box[0] <= px[0] <= box[2] and box[1] <= px[1] <= box[3]):
                fail('ee_outside_change', f'手先の投影点 ({px[0]:.0f}, {px[1]:.0f}) が映像の変化領域 {box} の外')
        if frame_settled is None:
            fail('no_settle_frame', '静止判定のフレームが無い')
        else:
            s = changed_ratio(diff_mask(frame_after, frame_settled))
            res['settle_ratio'] = s
            if s > SETTLE_MAX:
                fail(f'not_settled {s:.2%}', f'判定時刻にまだ動いている（変化 {s:.2%} > {SETTLE_MAX:.2%}）')

    res['verdict'] = 'FAIL' if reasons else 'PASS'
    res['reasons'] = reasons
    res['codes'] = codes   # 動画に焼き込む ASCII の短い理由（ffmpeg 6.1 の drawtext はマルチバイトで切れる）
    return res


# ---- 記録データの読み出し -------------------------------------------------

class Series:
    """時刻つき記録の最近傍検索。"""

    def __init__(self, records):
        self.records = sorted(records, key=lambda r: r['t'])
        self.ts = [r['t'] for r in self.records]

    def nearest(self, t, max_gap=MAX_GAP_SEC):
        if not self.ts:
            return None
        i = bisect.bisect_left(self.ts, t)
        cands = [k for k in (i - 1, i) if 0 <= k < len(self.ts)]
        k = min(cands, key=lambda k: abs(self.ts[k] - t))
        return self.records[k] if abs(self.ts[k] - t) <= max_gap else None

    def before(self, t, max_gap=MAX_GAP_SEC):
        """t 以前で最後の記録。"""
        i = bisect.bisect_right(self.ts, t) - 1
        if i < 0 or t - self.ts[i] > max_gap:
            return None
        return self.records[i]

    def after(self, t, max_gap=MAX_GAP_SEC):
        """t 以後で最初の記録。"""
        i = bisect.bisect_left(self.ts, t)
        if i >= len(self.ts) or self.ts[i] - t > max_gap:
            return None
        return self.records[i]


def judge_frames(frames, t_start, t_end):
    """判定に使う 3 枚（送信前・判定時刻・静止確認）の記録。

    低フレームレートでも判定時刻より前の（まだ動いている）フレームを使わないよう、
    判定時刻「以後」の最初のフレームと、その SETTLE_WINDOW 以上あとのフレームを使う。
    """
    before = frames.before(t_start, FRAME_MAX_GAP_SEC)
    after = frames.after(t_end, FRAME_MAX_GAP_SEC)
    settled = None
    if after is not None:
        settled = frames.after(max(t_end + SETTLE_WINDOW_SEC, after['t'] + 1e-6), FRAME_MAX_GAP_SEC)
    return before, after, settled


def read_jsonl(path):
    p = Path(path)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue   # 停止時に途中で切れた最終行は捨てる
    return out


def read_frames_csv(path):
    p = Path(path)
    if not p.exists():
        return []
    with p.open(encoding='utf-8') as f:
        return [{'n': int(r['n']), 't': float(r['t'])} for r in csv.DictReader(f)]


def settle_time(records, joints, expected, tolerance, t_sent, duration, t_limit=None, now=math.inf):
    """送信後に腕が止まった時刻（t_limit までに止まらなければ None）。

    シミュは実時間より遅い（RTF < 1）ので、指令の time_from_start ではなく /joint_states の静止で決める。
    「STILL_WINDOW の間の変化が STILL_EPS 以下」かつ「期待値に届いた、または指令時間が過ぎた」最初の時刻。
    指令が届く前の静止（送信直後）を拾わないよう、期待値に届くか指令時間が過ぎるまでは待つ。
    t_limit（既定は settle_deadline）より後の記録は見ない。上限を過ぎてから止まった場合に、
    次のステップの区間で判定してしまわないため。
    実行中（now が有限）は、区間 STILL_WINDOW が過ぎるまで判断を保留する（事後の判定と結果を揃える）。
    """
    if t_limit is None:
        t_limit = settle_deadline(t_sent, duration)
    rs = sorted((r for r in records if t_sent <= r['t'] <= t_limit + STILL_WINDOW), key=lambda r: r['t'])
    ts = [r['t'] for r in rs]
    for i, r in enumerate(rs):
        if r['t'] > t_limit:
            break
        pos = dict(zip(r['name'], r['position']))
        if any(j not in pos for j in joints):
            continue
        reached = max(abs(pos[j] - e) for j, e in zip(joints, expected)) <= tolerance
        if not reached and r['t'] < t_sent + duration:
            continue
        k = bisect.bisect_right(ts, r['t'] + STILL_WINDOW)   # 区間の末尾（rs[i:k]）
        if k == len(rs) and now < r['t'] + STILL_WINDOW:
            return None   # 実行中: 区間がまだ終わっていない
        if ts[k - 1] - r['t'] < STILL_WINDOW * 0.6:
            if k == len(rs):
                return None   # 記録の末尾: まだ区間ぶんの記録が無い
            continue          # 記録の途中の欠け（負荷で /joint_states が途切れる）: この時刻は判定しない
        still = True
        for x in rs[i + 1:k]:
            p = dict(zip(x['name'], x['position']))
            if any(abs(p.get(j, pos[j]) - pos[j]) > STILL_EPS for j in joints):
                still = False
                break
        if still:
            return r['t']
    return None


def settle_deadline(t_sent, duration):
    return t_sent + duration * SETTLE_TIMEOUT_FACTOR + SETTLE_TIMEOUT_EXTRA


def judge_time(records, step, event, now=math.inf, t_next=math.inf):
    """判定時刻 (t_end, 静止したか)。まだ決められない（静止待ちで上限前）なら None。

    scenario_cli wait（実行中、now = 現在時刻）と judge_run（事後、now = ∞）が同じ規則を使う。
    静止したら 静止時刻 + SETTLE_SEC、上限まで止まらなければ上限時刻（静止条件で FAIL になる）。
    """
    # 次のステップを送った後の記録では判定しない（wait が使えず固定の待ちに切り替わった場合）
    deadline = min(settle_deadline(event['t_sent'], step['duration']), t_next)
    t = settle_time(records, step['joints'], step['expect'], step['tolerance'],
                    event['t_sent'], step['duration'], deadline, now)
    if t is not None:
        return t + SETTLE_SEC, True
    if now >= deadline + STILL_WINDOW:
        return deadline, False
    return None


def joints_at(series, t):
    r = series.nearest(t)
    return None if r is None else dict(zip(r['name'], r['position']))


def frame_at(video, n, width, height, run=subprocess.run):
    """mp4 の n 番目（0 始まり）のフレームを rgb24 の numpy 配列で返す。取れなければ None。"""
    cmd = ['ffmpeg', '-v', 'error', '-i', str(video), '-vf', f'select=eq(n\\,{int(n)})',
           '-fps_mode', 'passthrough', '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-']
    out = run(cmd, capture_output=True, timeout=60).stdout
    if len(out) != width * height * 3:
        return None
    return np.frombuffer(out, np.uint8).reshape(height, width, 3)


def recording_problems(run_dir, run=subprocess.run):
    """判定の前提になる記録が揃っているか。欠けていれば理由のリスト（判定ではなく環境・記録の問題）。"""
    d = Path(run_dir)
    problems = []
    if (d / INCOMPLETE_MARK).exists():
        problems.append('camera.mp4 を書き終えられなかった（observer の停止が間に合わなかった）')
    info_p = d / 'camera_info.json'
    info = json.loads(info_p.read_text(encoding='utf-8')) if info_p.exists() else None
    if info is None:
        problems.append('camera_info.json が無い（画像に合う CameraInfo が届かなかった）')
    frames = read_frames_csv(d / 'camera_frames.csv')
    if not frames:
        problems.append('camera_frames.csv にフレームが無い')
    if info is not None and frames and not problems:
        # 先頭と末尾（最後のステップの判定に使う）のフレームが読めること。末尾が欠けていると
        # 判定は「静止確認のフレームが無い」で FAIL になり、記録の問題と区別できない
        for rec in (frames[0], frames[-1]):
            if frame_at(d / 'camera.mp4', rec['n'], info['width'], info['height'], run=run) is None:
                problems.append(f"camera.mp4 からフレーム {rec['n']} を読めない（書き出しが途中で止まった可能性）")
                break
    return problems


def judge_run(run_dir, run=subprocess.run):
    """run_dir の記録を読んで result.json の中身を返す。"""
    d = Path(run_dir)
    # 実行中の scenario_cli wait が決めた判定時刻（あればそれを正とし、待った時刻と判定時刻を一致させる）
    decided = {w['index']: w for w in read_jsonl(d / 'wait.jsonl')}
    steps = json.loads((d / 'steps.json').read_text(encoding='utf-8'))
    events = {e['index']: e for e in read_jsonl(d / 'events.jsonl')}
    joints = Series(read_jsonl(d / 'joints.jsonl'))
    ee = Series(read_jsonl(d / 'ee.jsonl'))
    frames = Series(read_frames_csv(d / 'camera_frames.csv'))
    info_p = d / 'camera_info.json'
    info = json.loads(info_p.read_text(encoding='utf-8')) if info_p.exists() else None
    K = info['k'] if info else None
    cache = {}

    def frame(rec):
        if rec is None or info is None:
            return None
        n = rec['n']
        if n not in cache:
            cache[n] = frame_at(d / 'camera.mp4', n, info['width'], info['height'], run=run)
        return cache[n]

    results = []
    for i, st in enumerate(steps):
        ev = events.get(i)
        base = {'index': i, 'name': st['name'], 'kind': st['kind'], 'expect': st['expect']}
        if ev is None or ev.get('rc', 1) != 0:
            rc = None if ev is None else ev.get('rc')
            results.append({**base, 'verdict': 'FAIL', 'reasons': [f'命令が送れていない（rc={rc}）'],
                            'codes': [f'send_failed rc={rc}'],
                            **({'t_start': ev['t_start'], 't_sent': ev['t_sent']} if ev else {})})
            continue
        if i in decided:
            t_end, settled = decided[i]['t_end'], decided[i]['settled']
        else:
            nxt = events.get(i + 1)
            t_end, settled = judge_time(joints.records, st, ev,
                                        t_next=nxt['t_start'] if nxt else math.inf)
        fb, fa, fs = judge_frames(frames, ev['t_start'], t_end)
        ee_rec = ee.nearest(t_end)
        r = judge_step(
            joints=st['joints'], expected=st['expect'], tolerance=st['tolerance'],
            actual_before=joints_at(joints, ev['t_start']), actual_after=joints_at(joints, t_end),
            frame_before=frame(fb), frame_after=frame(fa), frame_settled=frame(fs),
            ee_xyz=None if ee_rec is None else ee_rec['xyz'], K=K,
            motion_min=GRIPPER_MOTION_MIN if st['kind'] == S.GRIPPER else MOTION_MIN)
        if not settled:
            r['verdict'] = 'FAIL'
            r['reasons'].append('/joint_states が静止しなかった（待ちの上限まで動き続けた、または記録が無い）')
            r['codes'].append('joints_not_still')
        results.append({**base, **r, 't_start': ev['t_start'], 't_sent': ev['t_sent'], 't_end': t_end})
    passed = sum(r['verdict'] == 'PASS' for r in results)
    return {'passed': passed, 'total': len(results),
            'verdict': 'PASS' if results and passed == len(results) else 'FAIL', 'steps': results}
