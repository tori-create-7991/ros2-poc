import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np
import pytest

from ros2_poc_sim import vla_client as V
from ros2_poc_sim import vla_codec as C


def test_resize_nearest_shape_and_values():
    img = np.arange(4 * 6 * 3, dtype=np.uint8).reshape(4, 6, 3)
    out = V.resize_nearest(img, 2)
    assert out.shape == (2, 2, 3) and out.dtype == np.uint8
    assert (out[0, 0] == img[0, 0]).all() and (out[1, 1] == img[2, 3]).all()
    big = V.resize_nearest(np.zeros((480, 640, 3), np.uint8))
    assert big.shape == (V.IMAGE_SIZE, V.IMAGE_SIZE, 3)


def test_resize_rejects_non_rgb():
    with pytest.raises(ValueError):
        V.resize_nearest(np.zeros((4, 4), np.uint8))
    with pytest.raises(ValueError):
        V.resize_nearest(np.zeros((4, 4, 3), np.float32))


def test_image_from_ros_rgb8():
    data = bytes(range(12))
    img = V.image_from_ros('rgb8', 2, 2, data)
    assert img.shape == (2, 2, 3) and img[1, 1, 2] == 11
    with pytest.raises(ValueError, match='rgb8'):
        V.image_from_ros('bgr8', 2, 2, data)
    with pytest.raises(ValueError, match='大きさ'):
        V.image_from_ros('rgb8', 2, 2, data[:-1])
    with pytest.raises(ValueError, match='大きすぎ'):
        V.image_from_ros('rgb8', 5000, 5000, b'')


def test_build_payload_matches_deploy_py_contract():
    img = np.zeros((V.IMAGE_SIZE, V.IMAGE_SIZE, 3), np.uint8)
    p = V.build_payload(img, 'move up', unnorm_key='bridge_orig')
    assert p['instruction'] == 'move up' and p['unnorm_key'] == 'bridge_orig'
    descr, shape, data = C.decode(p['image'])
    assert (descr, shape, len(data)) == ('|u1', (256, 256, 3), 256 * 256 * 3)
    assert 'unnorm_key' not in V.build_payload(img, 'move up')
    json.dumps(p)


@pytest.mark.parametrize('instruction', ['', '   ', 'x' * 501, 'a\nb', 123, None])
def test_build_payload_rejects_bad_instruction(instruction):
    with pytest.raises(ValueError):
        V.build_payload(np.zeros((256, 256, 3), np.uint8), instruction)


@pytest.mark.parametrize('key', ['', 'a b', 'x' * 101, '../x', 5])
def test_build_payload_rejects_bad_unnorm_key(key):
    with pytest.raises(ValueError):
        V.build_payload(np.zeros((256, 256, 3), np.uint8), 'go', unnorm_key=key)


VEC = [0.01, 0.0, -0.02, 0.0, 0.0, 0.1, 1.0]


def test_parse_response_variants():
    arr = C.encode_floats(VEC)
    assert V.parse_response(json.dumps(arr)) == VEC                      # deploy.py の実際の応答（ndarray そのもの）
    assert V.parse_response(json.dumps({'action': arr})) == VEC          # docstring の形
    assert V.parse_response(json.dumps({'action': VEC})) == VEC
    assert V.parse_response(json.dumps(VEC)) == VEC
    assert V.parse_response(json.dumps(json.dumps(arr))) == VEC          # 二重エンコード
    assert V.parse_response(json.dumps(arr).encode()) == VEC             # bytes


@pytest.mark.parametrize('body', [
    '"error"', 'error', 'null', '[]', '[1,2,3]', json.dumps(C.encode_floats([1.0] * 6)),
    json.dumps({'action': 'x'}), json.dumps([0] * 6 + ['a']), json.dumps([True] * 7),
    '[0,0,0,0,0,0,NaN]', '{"x":1}', '"{not json"',
])
def test_parse_response_rejects(body):
    with pytest.raises(ValueError):
        V.parse_response(body)


@pytest.mark.parametrize('url', [
    'file:///etc/passwd', 'ftp://h/act', 'http://', 'http://h:99999/act', 'http://u:p@h/act', '//h/act', 'h:8000/act',
])
def test_validate_endpoint_rejects(url):
    with pytest.raises(ValueError):
        V.validate_endpoint(url)


def test_validate_endpoint_ok():
    assert V.validate_endpoint('http://vla-server:8000/act') == 'http://vla-server:8000/act'
    assert V.validate_endpoint('https://example.com/act') == 'https://example.com/act'


class _Handler(BaseHTTPRequestHandler):
    reply = (200, b'')

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
        type(self).received = (self.path, self.headers.get('Content-Type'), json.loads(body))
        code, data = type(self).reply
        self.send_response(code)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


@pytest.fixture
def server():
    srv = HTTPServer(('127.0.0.1', 0), type('H', (_Handler,), {}))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv, f'http://127.0.0.1:{srv.server_port}/act'
    srv.shutdown()
    srv.server_close()


def test_post_act_roundtrip(server):
    srv, url = server
    srv.RequestHandlerClass.reply = (200, json.dumps(C.encode_floats(VEC)).encode())
    payload = V.build_payload(np.zeros((256, 256, 3), np.uint8), 'go')
    assert V.post_act(url, payload, timeout=5) == VEC
    path, ctype, body = srv.RequestHandlerClass.received
    assert path == '/act' and ctype == 'application/json' and body['instruction'] == 'go'


def test_post_act_errors(server):
    srv, url = server
    payload = V.build_payload(np.zeros((256, 256, 3), np.uint8), 'go')
    srv.RequestHandlerClass.reply = (500, b'boom')
    with pytest.raises(V.VlaError, match='500'):
        V.post_act(url, payload, timeout=5)
    srv.RequestHandlerClass.reply = (200, b'"error"')
    with pytest.raises(V.VlaError, match='error'):
        V.post_act(url, payload, timeout=5)
    with pytest.raises(V.VlaError):
        V.post_act('http://127.0.0.1:1/act', payload, timeout=2)   # 接続できない


def test_post_act_caps_response_size(server):
    srv, url = server
    srv.RequestHandlerClass.reply = (200, b' ' * (V.MAX_RESPONSE_BYTES + 10))
    with pytest.raises(V.VlaError, match='大きすぎ'):
        V.post_act(url, V.build_payload(np.zeros((256, 256, 3), np.uint8), 'go'), timeout=5)


def test_validate_request_checks_arguments_without_an_image():
    V.validate_request('move up', 'bridge_orig')
    V.validate_request('move up')
    with pytest.raises(ValueError):
        V.validate_request('')
    with pytest.raises(ValueError):
        V.validate_request('go', '../x')


def test_post_act_does_not_follow_redirects(server):
    srv, url = server

    class Redirect(_Handler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length', 0)))
            self.send_response(302)
            self.send_header('Location', 'http://127.0.0.1:1/elsewhere')
            self.send_header('Content-Length', '0')
            self.end_headers()

    srv.RequestHandlerClass = Redirect
    with pytest.raises(V.VlaError, match='302'):
        V.post_act(url, V.build_payload(np.zeros((256, 256, 3), np.uint8), 'go'), timeout=5)
