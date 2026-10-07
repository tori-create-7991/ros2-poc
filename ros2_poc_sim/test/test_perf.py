import json

import pytest

from ros2_poc_sim import perf as P


def _csv(tmp_path, rows, header='wall,sim'):
    p = tmp_path / 'clock.csv'
    p.write_text(header + '\n' + ''.join(f'{w},{s}\n' for w, s in rows), encoding='utf-8')
    return p


def test_rtf_is_delta_sim_over_delta_wall(tmp_path):
    rows = P.read_clock_csv(_csv(tmp_path, [(100, 0), (105, 2.5), (110, 5)]))
    assert P.rtf(rows) == pytest.approx(0.5)


def test_missing_or_short_clock_gives_none_without_error(tmp_path):
    assert P.read_clock_csv(tmp_path / 'none.csv') == []
    assert P.rtf([]) is None
    assert P.rtf([(1.0, 0.0)]) is None
    assert P.rtf([(1.0, 0.0), (1.2, 0.1)]) is None   # 区間が短すぎる
    assert P.rtf_min([(1.0, 0.0)]) is None


def test_read_clock_skips_bad_rows_and_backwards_wall(tmp_path):
    p = tmp_path / 'clock.csv'
    p.write_text('wall,sim\n1,0\nx,y\n3,1\n2,9\n5,2\n', encoding='utf-8')
    assert P.read_clock_csv(p) == [(1.0, 0.0), (3.0, 1.0), (5.0, 2.0)]


def test_rtf_min_picks_slowest_window():
    rows = [(float(i), (i if i < 10 else 10 + (i - 10) * 0.2)) for i in range(0, 21)]
    assert P.rtf_min(rows, window=5) == pytest.approx(0.2)


def test_sim_at_interpolates_and_rejects_out_of_range():
    rows = [(10.0, 0.0), (20.0, 4.0)]
    assert P.sim_at(rows, 15.0) == pytest.approx(2.0)
    assert P.sim_at(rows, 20.0) == pytest.approx(4.0)
    assert P.sim_at(rows, 5.0) is None
    assert P.sim_at([], 5.0) is None


def test_camera_fps():
    assert P.camera_fps([0.0, 1.0, 2.0, 3.0]) == pytest.approx(1.0)
    assert P.camera_fps([0.0]) is None
    assert P.camera_fps([2.0, 2.0]) is None


def test_summarize_steps_and_totals():
    rows = [(100.0, 0.0), (110.0, 5.0), (120.0, 10.0)]
    steps = [{'index': 0, 'name': 'a', 't_start': 102.0, 't_end': 112.0},
             {'index': 1, 'name': 'b', 't_start': 0, 't_end': 5.0}]   # 送れなかったステップ（t_start=0）は除く
    s = P.summarize(rows, [101.0, 111.0], steps)
    assert s['rtf'] == pytest.approx(0.5)
    assert s['wall_sec'] == pytest.approx(11.0)
    assert s['camera_fps'] == pytest.approx(0.1)
    assert s['steps'] == [{'index': 0, 'name': 'a', 'wall_sec': 10.0, 'sim_sec': 5.0}]


def test_summarize_without_clock_is_all_none_but_wall():
    s = P.summarize([], [1.0, 2.0], [{'index': 0, 'name': 'a', 't_start': 1.5, 't_end': 4.0}])
    assert s['rtf'] is None and s['rtf_min'] is None
    assert s['wall_sec'] == pytest.approx(3.0)
    assert s['steps'][0]['sim_sec'] is None


def test_format_table_and_load_run(tmp_path):
    d = tmp_path / '20261007-1'
    d.mkdir()
    (d / 'run_meta.json').write_text(json.dumps({'label': 'light', 'cpus': 6}), encoding='utf-8')
    (d / 'result.json').write_text(json.dumps({'verdict': 'PASS', 'performance': {'rtf': 0.5, 'wall_sec': 30.0}}),
                                   encoding='utf-8')
    r = P.load_run(d)
    assert (r['label'], r['cpus'], r['rtf'], r['camera_fps']) == ('light', 6, 0.5, None)
    out = P.format_table([r, P.load_run(tmp_path / 'missing')])
    assert 'light' in out and '0.50' in out and out.splitlines()[0].startswith('run')
