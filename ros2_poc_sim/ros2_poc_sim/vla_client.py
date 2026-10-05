"""OpenVLA の REST サーバー（vla-scripts/deploy.py の POST /act）のクライアント。

契約は原文（2026-10-05 確認）: 入力 {"image": ndarray, "instruction": str, "unnorm_key": 省略可}、
出力は ndarray（json_numpy 形式）。docstring は {"action": ndarray} と書くが、実装は
`JSONResponse(action)` で ndarray そのものを返す。どちらも受ける。サーバーはエラー時も HTTP 200 で
文字列 "error" を返すので、型を検査して弾く。`{"encoded": <JSON 文字列>}` の二重エンコードにも対応する
（応答側。json_numpy を入れたくない相手向け）。
画像のリサイズだけ numpy を使う（ros2server は numpy を持つ。vla_stub_detect.py と同じ方針）。
"""
import json
import math
import re
import urllib.error
import urllib.parse
import urllib.request

from ros2_poc_sim import vla_codec as C

IMAGE_SIZE = 256
MAX_PIXELS = 1920 * 1080          # DDS は無認証。巨大な画像でメモリを使い切られない上限
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_INSTRUCTION_CHARS = 500
UNNORM_KEY_RE = re.compile(r'^[A-Za-z0-9_.\-]{1,100}$')
VECTOR_LEN = 7


class VlaError(Exception):
    """サーバーに繋がらない・応答が契約に合わない。"""


def validate_endpoint(url):
    """http / https の URL だけを許可する（file: などを受け付けない）。"""
    if not isinstance(url, str):
        raise ValueError('endpoint は文字列')
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        raise ValueError(f'endpoint は http(s)://host[:port]/path の形（{url!r}）')
    if parts.username or parts.password:
        raise ValueError('endpoint に認証情報を書かない')
    try:
        parts.port
    except ValueError as e:
        raise ValueError(f'endpoint のポートが不正: {e}') from e
    return url


def image_from_ros(encoding, height, width, data):
    """sensor_msgs/Image（rgb8）を (H, W, 3) の uint8 配列にする。"""
    import numpy as np
    if encoding != 'rgb8':
        raise ValueError(f'色画像は rgb8 のみ対応（受信: {encoding!r}）')
    if not (isinstance(height, int) and isinstance(width, int) and 0 < height * width <= MAX_PIXELS):
        raise ValueError(f'画像が大きすぎる・不正（{height}x{width}、上限 {MAX_PIXELS} 画素）')
    if len(data) != height * width * 3:
        raise ValueError(f'画像の大きさがデータと合わない（{height}x{width}、{len(data)} バイト）')
    return np.frombuffer(bytes(data), np.uint8).reshape(height, width, 3)


def resize_nearest(img, size=IMAGE_SIZE):
    """最近傍で size x size にする（OpenVLA の入力は 256x256x3。以降の前処理はサーバー側）。"""
    import numpy as np
    if img.ndim != 3 or img.shape[2] != 3 or img.dtype != np.uint8:
        raise ValueError(f'画像は (H, W, 3) の uint8（{img.shape}, {img.dtype}）')
    h, w = img.shape[:2]
    rows = np.arange(size) * h // size
    cols = np.arange(size) * w // size
    return np.ascontiguousarray(img[rows][:, cols])


def validate_request(instruction, unnorm_key=None):
    """instruction と unnorm_key を検査する（画像なしで引数だけ確かめたいとき用）。"""
    if (not isinstance(instruction, str) or not instruction.strip()
            or len(instruction) > MAX_INSTRUCTION_CHARS or any(ord(c) < 32 for c in instruction)):
        raise ValueError('instruction は 1〜500 文字の 1 行の文字列')
    if unnorm_key is not None and (not isinstance(unnorm_key, str) or not UNNORM_KEY_RE.match(unnorm_key)):
        raise ValueError(f'unnorm_key は英数字と _ . - の 1〜100 文字（{unnorm_key!r}）')


def build_payload(image, instruction, unnorm_key=None):
    """POST /act の JSON 本体を作る。image は (256, 256, 3) の uint8。"""
    validate_request(instruction, unnorm_key)
    if tuple(image.shape) != (IMAGE_SIZE, IMAGE_SIZE, 3):
        raise ValueError(f'image は {IMAGE_SIZE}x{IMAGE_SIZE}x3（{image.shape}）')
    payload = {'image': C.encode(image.tobytes(), '|u1', image.shape), 'instruction': instruction}
    if unnorm_key is not None:
        payload['unnorm_key'] = unnorm_key
    return payload


def _vector(v):
    if isinstance(v, dict) and '__numpy__' in v:
        return C.decode_floats(v)
    if isinstance(v, list) and all(not isinstance(x, bool) and isinstance(x, (int, float)) and math.isfinite(x) for x in v):
        return [float(x) for x in v]
    raise ValueError('応答が数値の配列でも json_numpy でもない')


def parse_response(body):
    """応答本体を 7 次元の float リストにする。契約に合わなければ ValueError。"""
    try:
        text = body.decode('utf-8') if isinstance(body, (bytes, bytearray)) else body
        value = json.loads(text)
        if isinstance(value, str):
            if value.strip().lower() == 'error':
                raise ValueError('サーバーがエラー（"error"）を返した。リクエストの形式を確認する')
            value = json.loads(value)     # 二重エンコード（json_numpy.dumps の文字列）
        if isinstance(value, dict) and 'action' in value:
            value = value['action']
        vec = _vector(value)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as e:
        raise ValueError(f'応答が JSON として読めない: {e}') from e
    if len(vec) != VECTOR_LEN:
        raise ValueError(f'応答は {VECTOR_LEN} 次元（{len(vec)} 次元）')
    return vec


def post_act(endpoint, payload, timeout=30.0):
    """POST /act して 7 次元のリストを返す。失敗は VlaError。"""
    validate_endpoint(endpoint)
    req = urllib.request.Request(endpoint, data=json.dumps(payload).encode('utf-8'),
                                 headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as e:
        raise VlaError(f'サーバーが HTTP {e.code} を返した') from e
    except (urllib.error.URLError, OSError) as e:
        raise VlaError(f'サーバーに繋がらない: {e}') from e
    if len(body) > MAX_RESPONSE_BYTES:
        raise VlaError(f'応答が大きすぎる（{MAX_RESPONSE_BYTES} バイト超）')
    try:
        return parse_response(body)
    except ValueError as e:
        raise VlaError(str(e)) from e
