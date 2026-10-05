import base64
import json

import pytest

from ros2_poc_sim import vla_codec as C


def test_encode_matches_json_numpy_layout():
    data = bytes([1, 2, 3, 4, 5, 6])
    obj = C.encode(data, '|u1', (2, 3))
    assert obj == {'__numpy__': base64.b64encode(data).decode(), 'dtype': '|u1', 'shape': [2, 3]}


def test_roundtrip_floats():
    obj = C.encode_floats([0.5, -1.25, 3.0])
    assert obj['dtype'] == '<f8' and obj['shape'] == [3]
    assert C.decode_floats(obj) == [0.5, -1.25, 3.0]


def test_decode_rejects_wrong_length():
    obj = C.encode(b'\x00' * 5, '|u1', (2, 3))
    with pytest.raises(ValueError, match='バイト数'):
        C.decode(obj)


def test_decode_rejects_unknown_dtype_and_bad_shape():
    with pytest.raises(ValueError, match='dtype'):
        C.decode({'__numpy__': 'AAAA', 'dtype': '<c16', 'shape': [1]})
    for shape in ([0], [-1], [1.5], 'x', [True], [2] * 9):
        with pytest.raises(ValueError):
            C.decode({'__numpy__': 'AA==', 'dtype': '|u1', 'shape': shape})


def test_decode_rejects_bad_base64_and_non_dict():
    with pytest.raises(ValueError):
        C.decode({'__numpy__': '***', 'dtype': '|u1', 'shape': [1]})
    with pytest.raises(ValueError):
        C.decode([1, 2, 3])


def test_decode_caps_size():
    obj = C.encode(b'\x00' * 100, '|u1', (100,))
    with pytest.raises(ValueError, match='大きすぎ'):
        C.decode(obj, max_bytes=50)


def test_decode_floats_requires_finite_float_array():
    with pytest.raises(ValueError, match='dtype'):
        C.decode_floats(C.encode(b'\x01', '|u1', (1,)))
    nan = C.encode_floats([float('nan')])
    with pytest.raises(ValueError, match='有限'):
        C.decode_floats(nan)


def test_decode_floats_accepts_f4():
    import struct
    obj = C.encode(struct.pack('<2f', 1.5, -2.0), '<f4', (2,))
    assert C.decode_floats(obj) == [1.5, -2.0]


def test_json_serializable():
    assert json.loads(json.dumps(C.encode_floats([1.0])))['__numpy__']
