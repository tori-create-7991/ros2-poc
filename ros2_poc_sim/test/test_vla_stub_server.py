import http.client
import json
import threading

import pytest

from ros2_poc_sim import vla_codec as C
from ros2_poc_sim import vla_stub_server as S

IMG = C.encode(b'\x00' * (8 * 8 * 3), '|u1', (8, 8, 3))


@pytest.mark.parametrize('text, expected', [
    ('move up', [0, 0, S.STEP, 0, 0, 0, 1.0]),
    ('Move DOWN', [0, 0, -S.STEP, 0, 0, 0, 1.0]),
    ('go left', [0, S.STEP, 0, 0, 0, 0, 1.0]),
    ('go right', [0, -S.STEP, 0, 0, 0, 0, 1.0]),
    ('forward', [S.STEP, 0, 0, 0, 0, 0, 1.0]),
    ('move backward', [-S.STEP, 0, 0, 0, 0, 0, 1.0]),
    ('back', [-S.STEP, 0, 0, 0, 0, 0, 1.0]),
    ('close the gripper', [0, 0, 0, 0, 0, 0, 0.0]),
    ('open the gripper', [0, 0, 0, 0, 0, 0, 1.0]),
    ('move up and left', [0, S.STEP, S.STEP, 0, 0, 0, 1.0]),
    ('do something', [0, 0, 0, 0, 0, 0, 1.0]),
    ('', [0, 0, 0, 0, 0, 0, 1.0]),
])
def test_action_for_is_deterministic_keywords(text, expected):
    assert S.action_for(text) == pytest.approx(expected)
    assert S.action_for(text) == S.action_for(text)


def test_action_for_matches_whole_words_only():
    assert S.action_for('cupboard setup')[:3] == [0, 0, 0]   # 'up' を含むだけの語は無視
    assert S.action_for('upward')[:3] == [0, 0, 0]


def test_action_for_close_beats_open_when_both():
    assert S.action_for('open then close')[6] == 0.0


def test_handle_act_valid_returns_ndarray_object():
    status, body = S.handle_act(json.dumps({'image': IMG, 'instruction': 'move up'}).encode())
    assert status == 200
    assert C.decode_floats(json.loads(body)) == pytest.approx([0, 0, S.STEP, 0, 0, 0, 1.0])


def test_handle_act_unnorm_key_is_accepted_and_ignored():
    status, body = S.handle_act(json.dumps({'image': IMG, 'instruction': 'up', 'unnorm_key': 'bridge_orig'}).encode())
    assert status == 200 and len(C.decode_floats(json.loads(body))) == 7


def test_handle_act_double_encoded():
    inner = json.dumps({'image': IMG, 'instruction': 'move up'})
    status, body = S.handle_act(json.dumps({'encoded': inner}).encode())
    assert status == 200
    outer = json.loads(body)
    assert isinstance(outer, str)
    assert C.decode_floats(json.loads(outer)) == pytest.approx([0, 0, S.STEP, 0, 0, 0, 1.0])


@pytest.mark.parametrize('payload', [
    b'not json', b'[]', b'{}', b'{"instruction":"up"}',
    json.dumps({'image': IMG}).encode(),
    json.dumps({'image': IMG, 'instruction': 5}).encode(),
    json.dumps({'image': C.encode(b'\x00' * 12, '|u1', (2, 2, 3)) | {'dtype': '<f4'}, 'instruction': 'up'}).encode(),
    json.dumps({'image': C.encode(b'\x00' * 8, '|u1', (2, 4)), 'instruction': 'up'}).encode(),
    json.dumps({'image': IMG, 'instruction': 'up', 'unnorm_key': 5}).encode(),
    json.dumps({'encoded': 5}).encode(),
    json.dumps({'encoded': '{}', 'extra': 1}).encode(),
])
def test_handle_act_invalid_returns_error_string_like_deploy_py(payload):
    status, body = S.handle_act(payload)
    assert status == 200 and json.loads(body) == 'error'


@pytest.fixture
def server():
    srv = S.make_server('127.0.0.1', 0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def _post(port, path, body, headers=None):
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
    conn.request('POST', path, body=body, headers=headers or {'Content-Type': 'application/json'})
    r = conn.getresponse()
    data = r.read()
    conn.close()
    return r.status, data


def test_http_act_end_to_end(server):
    status, data = _post(server, '/act', json.dumps({'image': IMG, 'instruction': 'move down'}))
    assert status == 200
    assert C.decode_floats(json.loads(data)) == pytest.approx([0, 0, -S.STEP, 0, 0, 0, 1.0])


def test_http_rejects_other_paths_and_methods(server):
    assert _post(server, '/other', b'{}')[0] == 404
    conn = http.client.HTTPConnection('127.0.0.1', server, timeout=5)
    conn.request('GET', '/act')
    assert conn.getresponse().status in (404, 405, 501)
    conn.close()


def test_http_body_size_cap(server):
    status, _ = _post(server, '/act', b'x' * (S.MAX_BODY_BYTES + 1))
    assert status == 413


def test_http_missing_or_bad_content_length(server):
    conn = http.client.HTTPConnection('127.0.0.1', server, timeout=5)
    conn.putrequest('POST', '/act')
    conn.putheader('Content-Length', 'abc')
    conn.endheaders()
    assert conn.getresponse().status == 400
    conn.close()


def _imported_modules(path):
    """ソースが import するものを 'ros2_poc_sim.<名前>' / '標準ライブラリのモジュール名' のリストで返す。"""
    import ast
    out = []
    for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
        if isinstance(node, ast.Import):
            out += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.module == 'ros2_poc_sim':
                out += [f'ros2_poc_sim.{a.name}' for a in node.names]   # from ros2_poc_sim import X は X まで見る
            else:
                out.append(node.module or '')
    return out


def test_stub_imports_only_the_standard_library_and_the_codec():
    """Dockerfile.vla-server は vla_codec.py と vla_stub_server.py しか入れない。増えたら起動時に初めて落ちるので、ここで止める。"""
    import sys
    from pathlib import Path
    pkg = Path(S.__file__).parent
    for name, allowed_local in (('vla_stub_server.py', {'ros2_poc_sim.vla_codec'}), ('vla_codec.py', set())):
        for m in _imported_modules(pkg / name):
            ok = m in allowed_local or (not m.startswith('ros2_poc_sim') and m.split('.')[0] in sys.stdlib_module_names)
            assert ok, f'{name} が許可されていないモジュールを import している: {m}'


def test_import_guard_catches_a_forbidden_local_import(tmp_path):
    bad = tmp_path / 'bad.py'
    bad.write_text('import json\nfrom ros2_poc_sim import vla_converter\nimport numpy\n', encoding='utf-8')
    assert _imported_modules(bad) == ['json', 'ros2_poc_sim.vla_converter', 'numpy']


def test_client_and_stub_agree_on_the_contract(server):
    """vla_client が作るリクエストを vla_stub_server が受け、返った 7 次元をクライアントが読める。"""
    import numpy as np

    from ros2_poc_sim import vla_client as V
    img = V.resize_nearest(np.zeros((480, 640, 3), np.uint8))
    vec = V.post_act(f'http://127.0.0.1:{server}/act', V.build_payload(img, 'move up', unnorm_key='bridge_orig'), timeout=5)
    assert vec == pytest.approx([0, 0, S.STEP, 0, 0, 0, 1.0])


def test_handle_act_never_raises_on_hostile_payloads():
    for image in ({'__numpy__': 'AAAA', 'dtype': [1], 'shape': [1]}, {'__numpy__': 5, 'dtype': '|u1', 'shape': [1]},
                  {'__numpy__': 'AAAA', 'dtype': {}, 'shape': {}}, 'x', 5, None):
        status, body = S.handle_act(json.dumps({'image': image, 'instruction': 'up'}).encode())
        assert status == 200 and json.loads(body) == 'error'
