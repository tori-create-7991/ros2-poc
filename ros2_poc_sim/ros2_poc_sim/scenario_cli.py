"""シナリオ実行の補助コマンド（scripts/run-scenario.sh から ros2arm 内で呼ぶ）。

  scenario_cli commands <scenario.yaml> [--repeat N] [--steps-out steps.json]
      1 行 1 ステップで「名前 TAB 送信先 TAB 待ち秒 TAB コマンド」を出す
  scenario_cli wait <run_dir> <index>  ステップ index の送信後、腕が止まって判定できる時刻まで待つ
  scenario_cli judge <run_dir>     記録を判定して result.json を書く（全 PASS で 0、FAIL で 1）
  scenario_cli compose <run_dir>   desktop.mp4 / camera.mp4 と判定から scenario.mp4 を作る
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from ros2_poc_sim import compose_video as C
from ros2_poc_sim import motion_judge as M
from ros2_poc_sim import scenario as S


def cmd_commands(a):
    try:
        steps = S.load_scenario(a.scenario, a.repeat)
    except (OSError, ValueError) as e:
        print(f'シナリオが読めない: {e}', file=sys.stderr)
        return 64
    if a.steps_out:
        Path(a.steps_out).write_text(
            json.dumps([s.to_dict() for s in steps], ensure_ascii=False, indent=1), encoding='utf-8')
    for s in steps:
        target, cmd = S.to_command(s)
        print(f'{s.name}\t{target}\t{S.wait_after_send(s):.1f}\t{cmd}')
    return 0


def cmd_wait(a):
    """observer が書く joints.jsonl を見て、静止 + 判定用の後続フレームぶんまで待つ。"""
    d = Path(a.run_dir)
    st = json.loads((d / 'steps.json').read_text(encoding='utf-8'))[a.index]
    ev = {e['index']: e for e in M.read_jsonl(d / 'events.jsonl')}.get(a.index)
    if ev is None:
        print(f'events.jsonl にステップ {a.index} が無い', file=sys.stderr)
        return 2
    deadline = M.settle_deadline(ev['t_sent'], st['duration'])
    while True:
        now = time.time()
        t = M.settle_time(M.read_jsonl(d / 'joints.jsonl'), st['joints'], st['expect'],
                          st['tolerance'], ev['t_sent'], st['duration'])
        if t is not None:
            # 判定時刻（静止 + SETTLE_SEC）と静止判定のフレーム（+ SETTLE_WINDOW_SEC）が記録されるまで
            rest = t + S.SETTLE_SEC + S.SETTLE_WINDOW_SEC + M.MAX_GAP_SEC - now
            if rest > 0:
                time.sleep(rest)
            print(f'静止 {t - ev["t_sent"]:.1f}s 後')
            return 0
        if now > deadline:
            print(f'静止しない（{deadline - ev["t_sent"]:.0f}s 待った）。判定で FAIL になる', file=sys.stderr)
            time.sleep(S.SETTLE_SEC + S.SETTLE_WINDOW_SEC + M.MAX_GAP_SEC)
            return 0
        time.sleep(0.3)


def cmd_judge(a):
    res = M.judge_run(a.run_dir)
    Path(a.run_dir, 'result.json').write_text(
        json.dumps(res, ensure_ascii=False, indent=1), encoding='utf-8')
    for r in res['steps']:
        print(C.verdict_line(r))
        for reason in r.get('reasons') or []:
            print(f'    - {reason}')
    print(f"RESULT {res['passed']}/{res['total']} PASS")
    return 0 if res['verdict'] == 'PASS' else 1


def _first_frame_t(run_dir):
    frames = M.read_frames_csv(Path(run_dir) / 'camera_frames.csv')
    return frames[0]['t'] if frames else None


def cmd_compose(a):
    d = Path(a.run_dir)
    res = json.loads((d / 'result.json').read_text(encoding='utf-8'))
    steps = [S.Step.from_dict(x) for x in json.loads((d / 'steps.json').read_text(encoding='utf-8'))]
    info = json.loads((d / 'camera_info.json').read_text(encoding='utf-8'))
    cam_t0 = _first_frame_t(d)
    if cam_t0 is None or not (d / 'camera.mp4').exists():
        print('camera.mp4 / camera_frames.csv が無いので合成できない', file=sys.stderr)
        return 2
    has_desktop = (d / 'desktop.mp4').exists() and (d / 'desktop_t0.txt').exists()
    t0 = float((d / 'desktop_t0.txt').read_text().strip()) if has_desktop else cam_t0
    graph, texts = C.build(results=res['steps'], commands=[(S.to_command(s)[0], S.display_lines(s)) for s in steps],
                           t0=t0, cam_t0=cam_t0, cam_h=info['height'],
                           has_desktop=has_desktop, font=a.font)
    C.write_texts(d, texts)
    (d / 'overlay' / 'filtergraph.txt').write_text(graph + '\n', encoding='utf-8')
    if not has_desktop:
        print('desktop.mp4 が無いので、カメラ映像だけで合成する', file=sys.stderr)
    return subprocess.run(C.ffmpeg_args(graph, has_desktop), cwd=d).returncode


def main(argv=None):
    p = argparse.ArgumentParser(prog='scenario_cli')
    sub = p.add_subparsers(dest='cmd', required=True)
    c = sub.add_parser('commands')
    c.add_argument('scenario')
    c.add_argument('--repeat', type=int)
    c.add_argument('--steps-out')
    w = sub.add_parser('wait')
    w.add_argument('run_dir')
    w.add_argument('index', type=int)
    j = sub.add_parser('judge')
    j.add_argument('run_dir')
    m = sub.add_parser('compose')
    m.add_argument('run_dir')
    m.add_argument('--font', default=C.FONT)
    a = p.parse_args(argv)
    return {'commands': cmd_commands, 'wait': cmd_wait, 'judge': cmd_judge,
            'compose': cmd_compose}[a.cmd](a)


if __name__ == '__main__':
    sys.exit(main())
