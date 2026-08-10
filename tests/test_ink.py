from onenote_tool.ink import _accumulate, _color_from_bytes, decode_multi_byte_signed


def test_decode_multi_byte_signed_small_values():
    # length=2 elements -> encoded as (2*2)=4 in one byte (0x04); then value
    # 3 (positive: raw=(3<<1)|0=6 -> 0x06) and value -2 (negative:
    # raw=(2<<1)|1=5 -> 0x05), each fitting in one byte (< 0x80, no
    # continuation bit).
    data = bytes([0x04, 0x06, 0x05])
    assert decode_multi_byte_signed(data) == [3, -2]


def test_decode_multi_byte_signed_multi_byte_continuation():
    # One element, value 200: raw = (200<<1)|0 = 400, which needs 2 bytes
    # (400 = 0b1_1001_0000 -> low 7 bits 0b0010000=16 with continuation bit
    # set (0x90), then remaining 3 in the final byte (0x03)). Length=1
    # element -> length*2=2, fits in one byte (0x02).
    data = bytes([0x02, 0x90, 0x03])
    assert decode_multi_byte_signed(data) == [200]


def test_decode_multi_byte_signed_empty_list():
    # length=0 elements -> length*2=0, single byte 0x00, nothing follows.
    assert decode_multi_byte_signed(bytes([0x00])) == []


def test_accumulate_running_sum():
    assert _accumulate([1, 2, 3]) == [1, 3, 6]
    assert _accumulate([5, -2, -1]) == [5, 3, 2]
    assert _accumulate([]) == []


def test_color_from_bytes_explicit_alpha_zero():
    # R=255,G=0,B=0, alpha=0x00 (explicit colour).
    assert _color_from_bytes(bytes([255, 0, 0, 0x00])) == '#ff0000'


def test_color_from_bytes_automatic_alpha_is_none():
    assert _color_from_bytes(bytes([255, 0, 0, 0xFF])) is None


def test_color_from_bytes_too_short_is_none():
    assert _color_from_bytes(bytes([1, 2])) is None
    assert _color_from_bytes(None) is None
