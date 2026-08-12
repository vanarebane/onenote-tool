from onenote_tool.text import _as_bytes, _le_int


def test_as_bytes_passes_through_real_bytes():
    assert _as_bytes(b'\x16\x00') == b'\x16\x00'


def test_as_bytes_decodes_pyonenote_repr_string():
    # Real shapes seen from pyOneNote: hex-escape runs, printable text mixed
    # with a trailing NUL, all-zero/all-0xff runs.
    assert _as_bytes(r"b'\x16\x00'") == b'\x16\x00'
    assert _as_bytes(r"b'\xe2\xef\xd9\x00'") == b'\xe2\xef\xd9\x00'
    assert _as_bytes("b'Calibri\\x00'") == b'Calibri\x00'
    assert _as_bytes(r"b'\x00\x00\x00\xff'") == b'\x00\x00\x00\xff'


def test_as_bytes_invalid_input_returns_empty():
    assert _as_bytes(None) == b''
    assert _as_bytes(42) == b''
    assert _as_bytes('not a byte repr') == b''
    assert _as_bytes("b'unterminated") == b''


def test_le_int_reads_little_endian():
    assert _le_int(r"b'\x02\x00\x00\x00'") == 2
    assert _le_int(b'\x01\x00', default=99) == 1


def test_le_int_falls_back_to_default_on_empty():
    assert _le_int(None, default=7) == 7
    assert _le_int('', default=3) == 3
