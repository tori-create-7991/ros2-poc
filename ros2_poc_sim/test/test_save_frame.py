import importlib.util
import struct
import zlib
from pathlib import Path

import numpy as np

spec = importlib.util.spec_from_file_location(
    'save_frame', Path(__file__).resolve().parents[1] / 'scripts' / 'save_frame.py')
sf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sf)


def _decode(png: bytes):
    assert png[:8] == b'\x89PNG\r\n\x1a\n'
    pos, idat, ihdr = 8, b'', None
    while pos < len(png):
        n = struct.unpack('>I', png[pos:pos + 4])[0]
        tag = png[pos + 4:pos + 8]
        data = png[pos + 8:pos + 8 + n]
        assert struct.unpack('>I', png[pos + 8 + n:pos + 12 + n])[0] == zlib.crc32(tag + data) & 0xffffffff
        if tag == b'IHDR':
            ihdr = struct.unpack('>IIBBBBB', data)
        elif tag == b'IDAT':
            idat += data
        pos += 12 + n
    w, h, depth, ctype = ihdr[0], ihdr[1], ihdr[2], ihdr[3]
    ch = 3 if ctype == 2 else 1
    raw = zlib.decompress(idat)
    rows = [np.frombuffer(raw[y * (1 + w * ch) + 1:(y + 1) * (1 + w * ch)], np.uint8) for y in range(h)]
    return w, h, ctype, np.stack(rows).reshape(h, w, ch)


def test_rgb_roundtrip():
    arr = np.arange(2 * 3 * 3, dtype=np.uint8).reshape(2, 3, 3)
    w, h, ctype, back = _decode(sf.encode_png(arr))
    assert (w, h, ctype) == (3, 2, 2)
    assert (back == arr).all()


def test_gray_roundtrip():
    arr = np.array([[0, 128, 255]], dtype=np.uint8)
    w, h, ctype, back = _decode(sf.encode_png(arr))
    assert (w, h, ctype) == (3, 1, 0)
    assert back[..., 0].tolist() == [[0, 128, 255]]


def test_image_to_array_encodings():
    rgb = bytes([1, 2, 3, 4, 5, 6])
    assert sf.image_to_array('rgb8', 1, 2, rgb).tolist() == [[[1, 2, 3], [4, 5, 6]]]
    assert sf.image_to_array('bgr8', 1, 2, rgb).tolist() == [[[3, 2, 1], [6, 5, 4]]]
    d = np.array([[0, 1000, 2000]], dtype='<u2').tobytes()
    assert sf.image_to_array('16UC1', 1, 3, d).tolist() == [[0, 127, 255]]
    f = np.array([[0.0, float('inf'), 2.0]], dtype='<f4').tobytes()
    assert sf.image_to_array('32FC1', 1, 3, f).tolist() == [[0, 0, 255]]
