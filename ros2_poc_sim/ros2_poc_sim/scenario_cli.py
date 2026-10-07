"""シナリオ実行の補助コマンド（scripts/run-scenario.sh から ros2arm 内で呼ぶ）。

  scenario_cli doctor                 録画・合成に要る外部コマンド・フォント（Noto Sans CJK JP）があるか確かめる
  scenario_cli commands <scenario.yaml> [--repeat N] [--steps-out steps.json]
      1 行 1 ステップで「名前 TAB 送信先(lab|sim) TAB 待ち秒 TAB コマンド」を出す
  scenario_cli budget <run_dir>       記録の最大秒数（記録プロセスの安全弁）を出す
  scenario_cli wait <run_dir> <index> ステップ index の送信後、腕が止まって判定できる時刻まで待つ
  scenario_cli judge <run_dir>        記録を判定して result.json を書く（全 PASS で 0、FAIL で 1）
  scenario_cli perf <run_dir>...      run を RTF・所要時間の表で比べる
  scenario_cli vla-prepare <run_dir>  vla_steps.jsonl（run-vla.sh --record）から steps.json / events.jsonl /
                                      narration.json を作り、最後に指令を送ったステップの番号を出す。
                                      以降は wait / judge / compose をそのまま使う
  scenario_cli compose <run_dir>      desktop.mp4 / camera.mp4 と判定から scenario.mp4 を作る
                                      （下帯の日本語は narration.json、無ければシナリオの description）
終了コード: 0 / 1（FAIL あり）/ 2（環境・記録の問題）/ 64（シナリオ・引数の誤り）
"""
import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import yaml

from ros2_poc_sim import compose_video as C
from ros2_poc_sim import motion_judge as M
from ros2_poc_sim import perf as PERF
from ros2_poc_sim import narration as N
from ros2_poc_sim import scenario as S
from ros2_poc_sim import vla_record as V

# 動画の下帯に出す送信先の表示名（実際の振り分けは scripts/run-scenario.sh）
TARGET_LABELS = {S.LAB: 'ros2lab-a', S.SIM: 'ros2arm'}
BUDGET_MARGIN_SEC = 300.0


def _font_families():
    """fontconfig が知っているフォント名の一覧（fc-list が無ければ空）。"""
    if shutil.which('fc-list') is None:
        return ''
    return subprocess.run(['fc-list', ':', 'family'], capture_output=True, text=True).stdout


def cmd_doctor(a):
    problems = []
    for exe in ('ffmpeg', 'xdpyinfo'):
        if shutil.which(exe) is None:
            problems.append(f'{exe} が無い')
    if shutil.which('ffmpeg'):
        enc = subprocess.run(['ffmpeg', '-hide_banner', '-encoders'], capture_output=True, text=True).stdout
        flt = subprocess.run(['ffmpeg', '-hide_banner', '-filters'], capture_output=True, text=True).stdout
        if 'libx264' not in enc:
            problems.append('ffmpeg に libx264 エンコーダが無い')
        for f in ('ass', 'drawbox', 'hstack', 'tpad'):
            if f' {f} ' not in flt:
                problems.append(f'ffmpeg に {f} フィルタが無い')
    if a.font.lower() not in _font_families().lower():
        problems.append(f'フォントが無い: {a.font}（fc-list に無い）')
    for p in problems:
        print(p, file=sys.stderr)
    return 2 if problems else 0


def cmd_commands(a):
    try:
        steps = S.load_scenario(a.scenario, a.repeat)
    except (OSError, ValueError, yaml.YAMLError) as e:
        print(f'シナリオが読めない: {e}', file=sys.stderr)
        return 64
    if a.steps_out:
        Path(a.steps_out).write_text(
            json.dumps([s.to_dict() for s in steps], ensure_ascii=False, indent=1), encoding='utf-8')
    for s in steps:
        target, cmd = S.to_command(s)
        print(f'{s.name}\t{target}\t{S.wait_after_send(s):.1f}\t{cmd}')
    return 0


def _load_steps(d):
    return json.loads((Path(d) / 'steps.json').read_text(encoding='utf-8'))


def cmd_budget(a):
    """全ステップが上限まで止まらず、フレームも最大間隔で来た場合の秒数 + 余裕。"""
    total = BUDGET_MARGIN_SEC
    for st in _load_steps(a.run_dir):
        total += (M.settle_deadline(0.0, st['duration']) + 40.0      # 40 = 送信のタイムアウト 30 + 余裕
                  + M.STILL_WINDOW + M.SETTLE_WINDOW_SEC + 2 * M.FRAME_MAX_GAP_SEC)
    print(int(total))
    return 0


def cmd_wait(a, now=time.time, sleep=time.sleep):
    """observer が書く joints.jsonl / camera_frames.csv を見て、判定に要る記録が揃うまで待つ。"""
    d = Path(a.run_dir)
    st = _load_steps(d)[a.index]
    ev = {e['index']: e for e in M.read_jsonl(d / 'events.jsonl')}.get(a.index)
    if ev is None:
        print(f'events.jsonl にステップ {a.index} が無い', file=sys.stderr)
        return 2
    decided = None
    while True:
        t = now()
        if decided is None:
            decided = M.judge_time(M.read_jsonl(d / 'joints.jsonl'), st, ev, now=t)
            if decided is not None:
                t_end, settled = decided
                # judge はこの判定時刻を使う（待った時刻と判定に使うフレームを一致させる）
                with (d / 'wait.jsonl').open('a', encoding='utf-8') as f:
                    f.write(json.dumps({'index': a.index, 't_end': t_end, 'settled': settled}) + '\n')
                if settled:
                    print(f'静止 {t_end - M.SETTLE_SEC - ev["t_sent"]:.1f}s 後')
                else:
                    print(f'静止しない（{t_end - ev["t_sent"]:.0f}s 待った）。判定で FAIL になる', file=sys.stderr)
        if decided is not None:
            # 判定に使うフレーム（判定時刻以後の 1 枚と、その後の静止確認の 1 枚）が記録されるまで待つ
            t_end = decided[0]
            frames = M.Series(M.read_frames_csv(d / 'camera_frames.csv'))
            if M.judge_frames(frames, ev['t_start'], t_end)[2] is not None:
                return 0
            if t > t_end + M.SETTLE_WINDOW_SEC + 2 * M.FRAME_MAX_GAP_SEC:
                print('判定に使うカメラフレームが来ない。判定で FAIL になる', file=sys.stderr)
                return 0
        sleep(0.3)


def cmd_judge(a):
    problems = M.recording_problems(a.run_dir)
    if problems:
        for p in problems:
            print(f'記録が欠けているので判定しない: {p}', file=sys.stderr)
        return 2
    res = M.judge_run(a.run_dir)
    Path(a.run_dir, 'result.json').write_text(
        json.dumps(res, ensure_ascii=False, indent=1), encoding='utf-8')
    for r in res['steps']:
        print(C.verdict_line(r))
        for reason in r.get('reasons') or []:
            print(f'    - {reason}')
    if (res.get('performance') or {}).get('rtf') is None:
        print('RTF を算出できない（clock.csv に /clock が記録されていない、または区間が短い）。判定には影響しない',
              file=sys.stderr)
    print(f"RESULT {res['passed']}/{res['total']} PASS")
    return 0 if res['verdict'] == 'PASS' else 1


def cmd_perf(a):
    """複数の run を RTF・所要時間の表で比べる（--light や CPU 増設の効果の確認用）。"""
    print(PERF.format_table([PERF.load_run(d) for d in a.run_dirs]))
    return 0


def _first_frame_t(run_dir):
    frames = M.read_frames_csv(Path(run_dir) / 'camera_frames.csv')
    return frames[0]['t'] if frames else None


def _command_of(step):
    """動画の下帯に小さく出す命令（ASCII）。送信先と、送る値だけを見せる。全文は commands.log。"""
    target, _ = S.to_command(step)
    return f"[{TARGET_LABELS[target]}] " + ' '.join(x.strip() for x in S.display_lines(step))


def _scenario_title(d):
    """run-scenario.sh が run_dir に置く scenario.yaml の description（無ければ空）。"""
    try:
        return S.scenario_title(yaml.safe_load((d / 'scenario.yaml').read_text(encoding='utf-8')))
    except (OSError, yaml.YAMLError):
        return ''


def _load_narration(d):
    """narration.json（vla-prepare が書く）を読む。無ければ None。中身は文字列に直して無害化する。"""
    p = d / 'narration.json'
    if not p.exists():
        return None
    raw = json.loads(p.read_text(encoding='utf-8'))
    if not isinstance(raw, dict) or not isinstance(raw.get('steps'), list):
        raise ValueError('narration.json は {title, steps: [...]} で書く')
    steps = [{'now': N.clean(x.get('now', ''), N.MAX_LINE), 'next': N.clean(x.get('next', ''), N.MAX_LINE),
              'command': N.clean(x.get('command', ''), N.MAX_COMMAND)}
             for x in raw['steps'] if isinstance(x, dict)]
    return {'title': N.clean(raw.get('title', ''), N.MAX_LINE), 'steps': steps}


def cmd_compose(a):
    d = Path(a.run_dir)
    res = json.loads((d / 'result.json').read_text(encoding='utf-8'))
    steps = [S.Step.from_dict(x) for x in _load_steps(d)]
    info = json.loads((d / 'camera_info.json').read_text(encoding='utf-8'))
    cam_t0 = _first_frame_t(d)
    if cam_t0 is None or not (d / 'camera.mp4').exists() or not info.get('height'):
        print('camera.mp4 / camera_frames.csv / camera_info.json が無いか不正なので合成できない', file=sys.stderr)
        return 2
    has_desktop = (d / 'desktop.mp4').exists() and (d / 'desktop_t0.txt').exists()
    t0 = float((d / 'desktop_t0.txt').read_text().strip()) if has_desktop else cam_t0
    narration = _load_narration(d)
    if narration is None:
        narration = N.narrate(steps, _scenario_title(d), _command_of)
    graph, texts = C.build(results=res['steps'], narration=narration, t0=t0, cam_t0=cam_t0,
                           cam_h=info['height'], has_desktop=has_desktop, font=a.font)
    C.write_texts(d, texts)
    (d / 'overlay' / 'filtergraph.txt').write_text(graph + '\n', encoding='utf-8')
    if not has_desktop:
        print('desktop.mp4 が無いので、カメラ映像だけで合成する', file=sys.stderr)
    return 0 if subprocess.run(C.ffmpeg_args(graph, has_desktop), cwd=d).returncode == 0 else 2


def cmd_vla_prepare(a):
    d = Path(a.run_dir)
    try:
        prepared = V.prepare(d / 'vla_steps.jsonl')
    except (OSError, ValueError) as e:
        print(f'vla_steps.jsonl から判定の入力を作れない: {e}', file=sys.stderr)
        return 2
    (d / 'steps.json').write_text(json.dumps(prepared['steps'], ensure_ascii=False, indent=1), encoding='utf-8')
    (d / 'narration.json').write_text(json.dumps(prepared['narration'], ensure_ascii=False, indent=1),
                                      encoding='utf-8')
    with (d / 'events.jsonl').open('w', encoding='utf-8') as f:
        for e in prepared['events']:
            f.write(json.dumps(e) + '\n')
    print(f"VLA ステップ {len(prepared['steps'])} 個、うち指令を送ったのは {len(prepared['sent'])} 個", file=sys.stderr)
    # 標準出力は「最後に指令を送ったステップの番号」だけ（run-vla.sh が scenario_cli wait に渡す。送ったステップが無ければ空）
    print(prepared['sent'][-1] if prepared['sent'] else '')
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog='scenario_cli')
    sub = p.add_subparsers(dest='cmd', required=True)
    o = sub.add_parser('doctor')
    o.add_argument('--font', default=C.FONT, help='fontconfig のフォント名')
    c = sub.add_parser('commands')
    c.add_argument('scenario')
    c.add_argument('--repeat', type=int)
    c.add_argument('--steps-out')
    b = sub.add_parser('budget')
    b.add_argument('run_dir')
    w = sub.add_parser('wait')
    w.add_argument('run_dir')
    w.add_argument('index', type=int)
    j = sub.add_parser('judge')
    j.add_argument('run_dir')
    pf = sub.add_parser('perf')
    pf.add_argument('run_dirs', nargs='+')
    v = sub.add_parser('vla-prepare')
    v.add_argument('run_dir')
    m = sub.add_parser('compose')
    m.add_argument('run_dir')
    m.add_argument('--font', default=C.FONT, help='fontconfig のフォント名')
    a = p.parse_args(argv)
    handler = {'doctor': cmd_doctor, 'commands': cmd_commands, 'budget': cmd_budget, 'wait': cmd_wait,
               'judge': cmd_judge, 'compose': cmd_compose, 'vla-prepare': cmd_vla_prepare, 'perf': cmd_perf}[a.cmd]
    try:
        return handler(a)
    except Exception as e:   # noqa: BLE001 — 想定外の失敗は「FAIL」(1) ではなく環境・記録の問題 (2) にする
        print(f'scenario_cli {a.cmd} が失敗した: {type(e).__name__}: {e}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
