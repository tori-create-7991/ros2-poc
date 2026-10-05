#!/usr/bin/env python3
"""OpenVLA の REST サーバー（vla-scripts/deploy.py）の代わりのスタブ（標準ライブラリのみ。GPU 不要）。

POST /act に deploy.py と同じ形のリクエストを受け、指示文のキーワードで決まる固定の 7 次元
（dx, dy, dz, droll, dpitch, dyaw, gripper）を返す。画像は形（H, W, 3 の uint8）だけ検査し、中身は見ない。
本物と同じく、リクエストが不正でも HTTP 200 で文字列 "error" を返す（クライアントの検査を試すため）。
vla-server の Docker イメージには vla_codec.py とこのファイルだけを入れる。

キーワード（単語として一致。小文字化して比較。複数あれば足し合わせる）:
  up / down: z ±STEP、left / right: y ±STEP、forward: x +STEP、back / backward: x -STEP
  close: gripper 0、open: gripper 1（既定 1。close が優先）
  base_link 基準（x 前、y 左、z 上）。STEP は 1 ステップの並進 [m]。
"""
import argparse
import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ros2_poc_sim import vla_codec as C

STEP = 0.02
MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_IMAGE_BYTES = 1024 * 1024
MAX_DRAIN_BYTES = 16 * 1024 * 1024    # 上限超過の本文を読み捨てる量（切断でクライアントが壊れないように）
REQUEST_TIMEOUT_SEC = 10
MAX_INSTRUCTION_CHARS = 500
UNNORM_MAX = 100

MOVES = {
    'up': (0, 0, STEP), 'down': (0, 0, -STEP), 'left': (0, STEP, 0), 'right': (0, -STEP, 0),
    'forward': (STEP, 0, 0), 'back': (-STEP, 0, 0), 'backward': (-STEP, 0, 0),
}
WORD_RE = re.compile(r'[a-z]+')


def action_for(instruction):
    """指示文から 7 次元を決める（決定論的。同じ指示は同じ値）。"""
    words = WORD_RE.findall(instruction.lower())
    pos = [0.0, 0.0, 0.0]
    for w in words:
        for i, v in enumerate(MOVES.get(w, (0, 0, 0))):
            pos[i] += v
    gripper = 0.0 if 'close' in words else 1.0
    return pos + [0.0, 0.0, 0.0, gripper]


def _parse(payload):
    """リクエストの辞書を検証して instruction を返す。不正なら ValueError。"""
    image, instruction = payload.get('image'), payload.get('instruction')
    descr, shape, _ = C.decode(image, max_bytes=MAX_IMAGE_BYTES)
    if descr != '|u1' or len(shape) != 3 or shape[2] != 3:
        raise ValueError(f'image は (H, W, 3) の uint8（{descr}, {shape}）')
    if not isinstance(instruction, str) or len(instruction) > MAX_INSTRUCTION_CHARS:
        raise ValueError('instruction は文字列')
    key = payload.get('unnorm_key')
    if key is not None and (not isinstance(key, str) or len(key) > UNNORM_MAX):
        raise ValueError('unnorm_key は文字列')
    return instruction


def handle_act(body):
    """POST /act の本文（bytes）を処理して (HTTP ステータス, 応答本文の文字列) を返す。"""
    error = (200, json.dumps('error'))
    try:
        payload = json.loads(body)
        if not isinstance(payload, dict):
            return error
        double = 'encoded' in payload
        if double:
            if len(payload) != 1 or not isinstance(payload['encoded'], str):
                return error
            payload = json.loads(payload['encoded'])
            if not isinstance(payload, dict):
                return error
        instruction = _parse(payload)
    except Exception:   # deploy.py の裸の except と同じ。どんな不正入力でも HTTP 200 + "error" にする
        return error
    encoded = json.dumps(C.encode_floats(action_for(instruction)))
    return 200, json.dumps(encoded) if double else encoded


class Handler(BaseHTTPRequestHandler):
    timeout = REQUEST_TIMEOUT_SEC
    server_version = 'vla-stub'

    def _reply(self, status, text=''):
        data = text.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self.path != '/act':
            return self._reply(404)
        try:
            length = int(self.headers.get('Content-Length', ''))
            if length < 0:
                raise ValueError
        except ValueError:
            self.close_connection = True
            return self._reply(400)
        if length > MAX_BODY_BYTES:
            self.rfile.read(min(length, MAX_DRAIN_BYTES))
            self.close_connection = True
            return self._reply(413)
        status, text = handle_act(self.rfile.read(length))
        self._reply(status, text)

    def do_GET(self):
        self._reply(404)

    def log_message(self, fmt, *args):     # 本文は出さない（リクエスト行だけ）
        sys.stderr.write('vla-stub: ' + fmt % args + '\n')


def make_server(host, port):
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    return srv


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--host', default='127.0.0.1', help='待ち受けアドレス（コンテナ内では 0.0.0.0。ホストへの公開は 127.0.0.1 限定にする）')
    ap.add_argument('--port', type=int, default=8000)
    a = ap.parse_args(argv)
    srv = make_server(a.host, a.port)
    print(f'vla-stub: http://{a.host}:{srv.server_address[1]}/act で待ち受け', flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
