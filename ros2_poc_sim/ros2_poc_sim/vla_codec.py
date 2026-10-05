"""OpenVLA の REST サーバー（vla-scripts/deploy.py）が使う json_numpy 形式の符号化（標準ライブラリのみ）。

ndarray は {"__numpy__": <base64>, "dtype": <descr>, "shape": [...]} の辞書で表す。
vla-server（スタブ）の Docker イメージにはこのファイルだけを入れるので、numpy や ROS に依存しない。
入力は信頼しない（HTTP / DDS の相手が不正な値を送りうる）。dtype は使うものだけを許可し、
大きさとバイト数を検査する。
"""
import base64
import binascii
import math
import struct

# 許可する dtype（descr）→ 1 要素のバイト数
ITEMSIZE = {'|u1': 1, '<f4': 4, '<f8': 8}
FLOAT_FORMAT = {'<f4': 'f', '<f8': 'd'}
MAX_DIMS = 4
DEFAULT_MAX_BYTES = 4 * 1024 * 1024


def encode(data, descr, shape):
    """バイト列を json_numpy 形式の辞書にする。"""
    return {'__numpy__': base64.b64encode(data).decode('ascii'), 'dtype': descr, 'shape': list(shape)}


def encode_floats(values):
    """float のリストを `<f8` の 1 次元配列として符号化する。"""
    values = [float(v) for v in values]
    return encode(struct.pack(f'<{len(values)}d', *values), '<f8', (len(values),))


def decode(obj, max_bytes=DEFAULT_MAX_BYTES):
    """辞書を検証して (descr, shape(tuple), data(bytes)) にする。不正なら ValueError。"""
    if not isinstance(obj, dict):
        raise ValueError(f'json_numpy の辞書ではない（{type(obj).__name__}）')
    descr = obj.get('dtype')
    if not isinstance(descr, str) or descr not in ITEMSIZE:
        raise ValueError(f'未対応の dtype: {descr!r}（対応: {sorted(ITEMSIZE)}）')
    shape = obj.get('shape')
    if (not isinstance(shape, (list, tuple)) or not 1 <= len(shape) <= MAX_DIMS
            or any(isinstance(n, bool) or not isinstance(n, int) or n < 1 for n in shape)):
        raise ValueError(f'shape が不正: {shape!r}')
    count = 1
    for n in shape:
        count *= n
        if count * ITEMSIZE[descr] > max_bytes:
            raise ValueError(f'配列が大きすぎる（{max_bytes} バイト超）')
    raw = obj.get('__numpy__')
    if not isinstance(raw, str):
        raise ValueError('__numpy__ が文字列ではない')
    try:
        data = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as e:
        raise ValueError(f'base64 が不正: {e}') from e
    if len(data) != count * ITEMSIZE[descr]:
        raise ValueError(f'バイト数が shape と合わない（{len(data)} != {count * ITEMSIZE[descr]}）')
    return descr, tuple(shape), data


def decode_floats(obj, max_bytes=DEFAULT_MAX_BYTES):
    """float 配列（`<f4` / `<f8`）を平坦なリストにする。非有限値は拒否する。"""
    descr, _shape, data = decode(obj, max_bytes)
    fmt = FLOAT_FORMAT.get(descr)
    if fmt is None:
        raise ValueError(f'float 配列ではない（dtype {descr}）')
    n = len(data) // ITEMSIZE[descr]
    values = list(struct.unpack(f'<{n}{fmt}', data))
    if not all(math.isfinite(v) for v in values):
        raise ValueError('有限でない値を含む')
    return values
