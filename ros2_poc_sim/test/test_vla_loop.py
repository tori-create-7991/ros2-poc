import pytest

from ros2_poc_sim import vla_action as A
from ros2_poc_sim import vla_client as V
from ros2_poc_sim import vla_loop as L

VEC = [0.0, 0.0, 0.02, 0.0, 0.0, 0.0, 1.0]


def _loop(steps=3, image=lambda: 'img', query=lambda img: VEC, ack=lambda seq: (seq, 'ok', ''), **kw):
    published, logs = [], []
    code, results = L.run_loop(steps, image, query, published.append, ack, logs.append, **kw)
    return code, results, published, logs


def test_all_steps_ok():
    code, results, published, logs = _loop(3)
    assert code == L.EXIT_OK
    assert [a.seq for a in published] == [1, 2, 3]
    assert [r['status'] for r in results] == ['ok'] * 3
    assert all(isinstance(a, A.Action) and a.gripper == 1.0 for a in published)
    assert any('3/3' in line for line in logs)


def test_start_seq():
    _code, _results, published, _ = _loop(2, start_seq=10)
    assert [a.seq for a in published] == [10, 11]


def test_stops_at_first_failed_step():
    seen = []

    def ack(seq):
        seen.append(seq)
        return (seq, 'ik_failed', 'IK の解がない') if seq == 2 else (seq, 'ok', '')

    code, results, published, _ = _loop(5, ack=ack)
    assert code == L.EXIT_STEP_FAILED
    assert seen == [1, 2] and len(published) == 2
    assert results[-1]['status'] == 'ik_failed'


def test_no_image_is_environment_error():
    code, results, published, logs = _loop(2, image=lambda: None)
    assert code == L.EXIT_ENV and published == [] and any('画像' in line for line in logs)


def test_vla_server_error_is_environment_error():
    def query(_img):
        raise V.VlaError('サーバーに繋がらない')

    code, _results, published, logs = _loop(2, query=query)
    assert code == L.EXIT_ENV and published == [] and any('サーバーに繋がらない' in line for line in logs)


@pytest.mark.parametrize('bad', [[0.0] * 6, [0.0] * 6 + [float('nan')]])
def test_bad_vla_output_is_environment_error(bad):
    code, _results, published, _ = _loop(1, query=lambda _img: bad)
    assert code == L.EXIT_ENV and published == []


def test_no_ack_is_environment_error():
    code, results, published, logs = _loop(2, ack=lambda seq: None)
    assert code == L.EXIT_ENV and len(published) == 1
    assert any('ack' in line for line in logs)


def test_ack_with_other_seq_is_ignored_by_waiter_contract():
    # wait_ack(seq) は seq 一致の ack だけを返す責務。違う seq が返ってきたら不整合として環境エラーにする
    code, _results, _published, _ = _loop(1, ack=lambda seq: (seq + 1, 'ok', ''))
    assert code == L.EXIT_ENV


@pytest.mark.parametrize('steps', [0, -1, 101, True, 1.5, '3'])
def test_rejects_bad_steps(steps):
    with pytest.raises(ValueError):
        L.run_loop(steps, lambda: 'img', lambda i: VEC, lambda a: None, lambda s: None, lambda m: None)
