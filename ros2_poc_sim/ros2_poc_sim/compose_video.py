"""記録（desktop.mp4 / camera.mp4）と判定結果を 1 本の mp4 に合成する ffmpeg 引数を作る。

左: Gazebo のデスクトップ（x11grab）、右: 仮想カメラ（判定後は差分領域の枠と手先の投影点）、
下帯: 「いま何をしていて次は何か」「判定」「送った命令」。時刻はすべて desktop.mp4 の開始時刻
（desktop_t0）基準。
下帯の文字は ASS 字幕（ass フィルタ = libass）で焼く。ffmpeg 6.1 の drawtext は日本語を含む行を途中で
切るので使わない。字幕は overlay/steps.ass に書き、命令文を filtergraph に直接埋め込まない。
フォントは fontconfig の名前（Noto Sans CJK JP）で指定する。
"""
from pathlib import Path

from ros2_poc_sim import narration as N

HEIGHT = 720
BAR = 170
FPS = 10
TAIL_SEC = 3.0   # 最後のステップの判定を表示し続ける秒数
FONT = 'Noto Sans CJK JP'
GREEN, RED, YELLOW = '0x33dd55', '0xff4040', 'yellow'
ASS_PATH = 'overlay/steps.ass'
PLAY_RES_X = 1920
MIN_TITLE_SEC = 0.5

# 下帯（HEIGHT〜HEIGHT+BAR）の行ごとの上端 y と文字の大きさ。PlayResY = 出力の高さにして 1 対 1 にする
_BAR_Y = {'Now': 728, 'Next': 766, 'Verdict': 800, 'Cmd': 840}
_STYLE_FMT = ('Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, '
              'Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, '
              'Shadow, Alignment, MarginL, MarginR, MarginV, Encoding')
# (名前, 大きさ, 文字色 &HAABBGGRR, 配置 7=左上 9=右上, 上端 y)
_STYLES = [
    ('Now', 28, '&H00FFFFFF', 7, _BAR_Y['Now']),
    ('Next', 24, '&H00CCCCCC', 7, _BAR_Y['Next']),
    ('Pending', 26, '&H0099DDFF', 7, _BAR_Y['Verdict']),
    ('Pass', 26, '&H0055DD33', 7, _BAR_Y['Verdict']),
    ('Fail', 26, '&H004040FF', 7, _BAR_Y['Verdict']),
    ('Cmd', 16, '&H00AAAAAA', 7, _BAR_Y['Cmd']),
    ('SumPass', 30, '&H0055DD33', 9, _BAR_Y['Now']),
    ('SumFail', 30, '&H004040FF', 9, _BAR_Y['Now']),
]


def ass_text(text):
    """ASS の特殊文字（{ } \\）と制御文字を無害にする（字幕のタグや改行を作らせない）。"""
    t = N.CONTROL_RE.sub(' ', str(text))
    return t.replace('\\', '＼').replace('{', '｛').replace('}', '｝')


def _ts(sec):
    cs = max(0, int(round(sec * 100)))
    return f'{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}'


def _style_line(font, name, size, color, align, y):
    return (f'Style: {name},{font},{size},{color},{color},&H00000000,&H00000000,'
            f'-1,0,0,0,100,100,0,0,1,2,0,{align},20,30,{y},1')


def ass_document(font, events):
    """events: [(開始秒, 終了秒, スタイル名, 文字列)]。"""
    head = ['[Script Info]', 'ScriptType: v4.00+', f'PlayResX: {PLAY_RES_X}', f'PlayResY: {HEIGHT + BAR}',
            'WrapStyle: 2', 'ScaledBorderAndShadow: yes', '',
            '[V4+ Styles]', _STYLE_FMT]
    head += [_style_line(ass_text(font), *st) for st in _STYLES]
    head += ['', '[Events]', 'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text']
    body = [f'Dialogue: 0,{_ts(a)},{_ts(b)},{style},,0,0,0,,{ass_text(text)}'
            for a, b, style, text in events if b > a and text]
    return '\n'.join(head + body) + '\n'


def verdict_line(r):
    if r['verdict'] == 'PASS':
        parts = []
        if r.get('joint_err') is not None:
            parts.append(f"joint err {r['joint_err']:.3f} rad")
        if r.get('changed_ratio') is not None:
            parts.append(f"change {r['changed_ratio']:.1%}")
        return f"STEP {r['index'] + 1} {r['name']}: PASS ({', '.join(parts)})"
    return f"STEP {r['index'] + 1} {r['name']}: FAIL - {', '.join(r.get('codes') or ['?'])}"


def _q(x):
    return f'{x:.3f}'


def build(*, results, narration, t0, cam_t0, cam_h, has_desktop=True, font=FONT):
    """(filtergraph, {相対パス: テキスト}) を返す。ffmpeg は run_dir を cwd にして実行する。

    results: judge_run の steps、narration: narration.narrate() の dict（title / steps[now, next, command]）。
    入力は 0 = desktop.mp4（has_desktop のとき）、最後 = camera.mp4。
    """
    if cam_h <= 0:
        raise ValueError(f'カメラの高さが不正（{cam_h}）')
    scale = HEIGHT / cam_h
    cam_in = 1 if has_desktop else 0
    off = cam_t0 - t0
    if off >= 0:
        cam = f'[{cam_in}:v]tpad=start_duration={_q(off)}:color=black'
    else:
        cam = f'[{cam_in}:v]trim=start={_q(-off)},setpts=PTS-STARTPTS'
    cam += f',fps={FPS},scale=-2:{HEIGHT},setsar=1'
    lines = narration.get('steps') or []
    events = []
    total = len(results)
    first_a = None
    for i, r in enumerate(results):
        nxt = results[i + 1].get('t_start') if i + 1 < total else None
        if 't_start' not in r:
            continue
        a = r['t_start'] - t0
        b = (nxt - t0) if nxt is not None else (r.get('t_end', r['t_start']) - t0 + TAIL_SEC)
        if first_a is None:
            first_a = a
        n = lines[i] if i < len(lines) else {'now': r['name'], 'next': '', 'command': ''}
        events.append((a, b, 'Now', f"ステップ {i + 1}/{total}  いま: {n['now']}"))
        events.append((a, b, 'Next', f"つぎ: {n['next']}"))
        events.append((a, b, 'Cmd', n.get('command', '')))
        ok, text = N.verdict_text(r)
        if 't_end' not in r:
            events.append((a, b, 'Fail', f'判定: {text}'))
            continue
        e = r['t_end'] - t0
        color = GREEN if ok else RED
        events.append((a, e, 'Pending', '判定: 動作の完了を待っています…'))
        events.append((e, b, 'Pass' if ok else 'Fail', f'判定: {text}'))
        if r.get('bbox'):
            x0, y0, x1, y1 = (v * scale for v in r['bbox'])
            cam += (f",drawbox=x={x0:.0f}:y={y0:.0f}:w={x1 - x0:.0f}:h={y1 - y0:.0f}:"
                    f"color={color}@0.9:t=4:enable='between(t,{_q(e)},{_q(b)})'")
        if r.get('ee_px'):
            u, v = (c * scale for c in r['ee_px'])
            cam += (f",drawbox=x={u - 7:.0f}:y={v - 7:.0f}:w=14:h=14:color={YELLOW}@0.9:t=fill:"
                    f"enable='between(t,{_q(e)},{_q(b)})'")
    title = narration.get('title')
    if title and first_a is not None and first_a > MIN_TITLE_SEC and lines:
        events.append((0.0, first_a, 'Now', title))
        events.append((0.0, first_a, 'Next', f"つぎ: {lines[0]['now']}"))
    if results and 't_end' in results[-1]:
        passed = sum(r['verdict'] == 'PASS' for r in results)
        e = results[-1]['t_end'] - t0
        events.append((e, e + TAIL_SEC, 'SumPass' if passed == total else 'SumFail',
                       f'総合 {passed}/{total} 合格'))
    graph = [cam + '[c]']
    if has_desktop:
        graph.insert(0, f'[0:v]fps={FPS},scale=-2:{HEIGHT},setsar=1[d]')
        stack = '[d][c]hstack=inputs=2'
    else:
        stack = '[c]null'
    # 記録は最後の判定の直後に止まるので、最後の判定と総合結果が読めるよう末尾のフレームを延ばす
    graph.append(','.join([stack, f'tpad=stop_mode=clone:stop_duration={TAIL_SEC:g}',
                           f'pad=iw:ih+{BAR}:0:0:color=black', f'ass={ASS_PATH}']) + '[out]')
    return ';'.join(graph), {ASS_PATH: ass_document(font, events).rstrip('\n')}


def ffmpeg_args(filtergraph, has_desktop=True, out='scenario.mp4'):
    inputs = (['-i', 'desktop.mp4'] if has_desktop else []) + ['-i', 'camera.mp4']
    return (['ffmpeg', '-y', '-v', 'error'] + inputs
            + ['-filter_complex', filtergraph, '-map', '[out]', '-c:v', 'libx264',
               '-preset', 'veryfast', '-crf', '23', '-pix_fmt', 'yuv420p', out])


def write_texts(run_dir, texts):
    for rel, body in texts.items():
        p = Path(run_dir) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body + '\n', encoding='utf-8')
