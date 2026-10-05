"""記録（desktop.mp4 / camera.mp4）と判定結果を 1 本の mp4 に合成する ffmpeg 引数を作る。

左: Gazebo のデスクトップ（x11grab）、右: 仮想カメラ（判定後は差分領域の枠と手先の投影点）、
下帯: 送信中の命令文と判定結果。時刻はすべて desktop.mp4 の開始時刻（desktop_t0）基準。
文字列は textfile（expansion=none）で渡し、命令文を filtergraph に直接埋め込まない。
文字は ASCII に限る（ffmpeg 6.1 の drawtext はマルチバイト文字を含む行を途中で切る）。
"""
import textwrap
from pathlib import Path

HEIGHT = 720
BAR = 170
FPS = 10
WRAP = 130
TAIL_SEC = 3.0   # 最後のステップの判定を表示し続ける秒数
FONT = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
GREEN, RED, YELLOW = '0x33dd55', '0xff4040', 'yellow'


def wrap_lines(text, width=WRAP, max_lines=3):
    lines = textwrap.wrap(text, width, break_long_words=True, break_on_hyphens=False) or ['']
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][:width - 3] + '...'
    return lines


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


def build(*, results, commands, t0, cam_t0, cam_h, has_desktop=True, font=FONT):
    """(filtergraph, {相対パス: テキスト}) を返す。ffmpeg は run_dir を cwd にして実行する。

    results: judge_run の steps、commands: ステップごとの (送信先, 表示行のリスト)。
    入力は 0 = desktop.mp4（has_desktop のとき）、最後 = camera.mp4。
    """
    texts = {}
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
    draw_t = []
    total = len(results)
    for i, r in enumerate(results):
        nxt = results[i + 1].get('t_start') if i + 1 < total else None
        if 't_start' not in r:
            continue
        a = r['t_start'] - t0
        b = (nxt - t0) if nxt is not None else (r.get('t_end', r['t_start']) - t0 + TAIL_SEC)
        target, lines = commands[i]
        path = f'overlay/step{i:03d}_cmd.txt'
        body = [x for line in lines for x in wrap_lines(line, max_lines=2)][:3]
        texts[path] = '\n'.join([f"STEP {i + 1}/{total}  {r['name']}  [{target}]"] + body)
        draw_t.append(f"drawtext=fontfile={font}:expansion=none:textfile={path}:fontsize=20:"
                      f"fontcolor=white:line_spacing=6:x=20:y=h-{BAR}+10:"
                      f"enable='between(t,{_q(a)},{_q(b)})'")
        if 't_end' not in r:
            path = f'overlay/step{i:03d}_verdict.txt'
            texts[path] = verdict_line(r)
            draw_t.append(f"drawtext=fontfile={font}:expansion=none:textfile={path}:fontsize=24:"
                          f"fontcolor={RED}:x=20:y=h-40:enable='between(t,{_q(a)},{_q(b)})'")
            continue
        e = r['t_end'] - t0
        color = GREEN if r['verdict'] == 'PASS' else RED
        path = f'overlay/step{i:03d}_verdict.txt'
        texts[path] = verdict_line(r)
        draw_t.append(f"drawtext=fontfile={font}:expansion=none:textfile={path}:fontsize=24:"
                      f"fontcolor={color}:x=20:y=h-40:enable='between(t,{_q(e)},{_q(b)})'")
        if r.get('bbox'):
            x0, y0, x1, y1 = (v * scale for v in r['bbox'])
            cam += (f",drawbox=x={x0:.0f}:y={y0:.0f}:w={x1 - x0:.0f}:h={y1 - y0:.0f}:"
                    f"color={color}@0.9:t=4:enable='between(t,{_q(e)},{_q(b)})'")
        if r.get('ee_px'):
            u, v = (c * scale for c in r['ee_px'])
            cam += (f",drawbox=x={u - 7:.0f}:y={v - 7:.0f}:w=14:h=14:color={YELLOW}@0.9:t=fill:"
                    f"enable='between(t,{_q(e)},{_q(b)})'")
    if results and 't_end' in results[-1]:
        passed = sum(r['verdict'] == 'PASS' for r in results)
        path = 'overlay/summary.txt'
        texts[path] = f'RESULT {passed}/{total} PASS'
        color = GREEN if passed == total else RED
        e = results[-1]['t_end'] - t0
        draw_t.append(f"drawtext=fontfile={font}:expansion=none:textfile={path}:fontsize=32:"
                      f"fontcolor={color}:x=w-tw-30:y=h-50:enable='gte(t,{_q(e)})'")
    graph = [cam + '[c]']
    if has_desktop:
        graph.insert(0, f'[0:v]fps={FPS},scale=-2:{HEIGHT},setsar=1[d]')
        stack = '[d][c]hstack=inputs=2'
    else:
        stack = '[c]null'
    graph.append(','.join([stack, f'pad=iw:ih+{BAR}:0:0:color=black'] + draw_t) + '[out]')
    return ';'.join(graph), texts


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
