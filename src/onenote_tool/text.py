"""Low-level text/identity decoding shared across the package.

No pyOneNote or Django dependency — pure string/byte transforms of whatever
a caller already extracted from a property dict.
"""
from __future__ import annotations

import re
from typing import Optional

_HEX_DIGITS = set('0123456789abcdefABCDEF')


def decode_run_text(value) -> str:
    """Recover OneNote run text from pyOneNote's mangled TextExtendedAscii.

    Two forms occur:
    - a hex-encoded byte string (decode the bytes, trying encodings in order);
    - the UTF-16LE byte stream packed into code units (re-encode LE, read latin1).
    Non-ASCII (e.g. Estonian ä/õ/ü) must not be forced through UTF-8 with
    replacement — that produced U+FFFD (visible as "m�rts").
    """
    if not isinstance(value, str) or not value:
        return ''

    raw = value.strip()
    if raw and len(raw) % 2 == 0 and all(c in _HEX_DIGITS for c in raw):
        try:
            data = bytes.fromhex(raw)
        except ValueError:
            data = None
        if data is not None:
            # UTF-16LE bytes must be read as UTF-16LE, not UTF-8. Try the most
            # likely text encodings strictly before falling back.
            for enc in ('utf-8', 'utf-16-le', 'latin-1'):
                try:
                    return data.decode(enc)
                except UnicodeDecodeError:
                    continue
            return data.decode('utf-8', 'replace')

    try:
        return value.encode('utf-16-le').decode('latin1')
    except Exception:
        return value


def _clean_text(text: str) -> str:
    # Drop stray NULs/control chars that survive the lossy decode.
    return ''.join(ch for ch in text if ch == '\n' or ch == '\t' or ord(ch) >= 0x20).strip()


def _clean_title(value) -> str:
    return str(value or '').replace('\x00', '').strip()


def _norm_identity(value) -> Optional[str]:
    m = re.search(r'\(([0-9a-f-]{36},\s*\d+)\)', str(value))
    return m.group(1).replace(' ', '') if m else None


def _extract_guid(value) -> str:
    """PictureContainer / identity look like ['<ExtendedGUID> (guid, n)']; take a key."""
    s = value[0] if isinstance(value, (list, tuple)) and value else value
    return str(s).strip()


def _as_bytes(value) -> bytes:
    """pyOneNote hands raw fixed-width props back as a bytes-repr string (b'\\x03\\x00')."""
    try:
        import ast
        if isinstance(value, (bytes, bytearray)):
            return bytes(value)
        if isinstance(value, str) and value.startswith("b'"):
            return bytes(ast.literal_eval(value))
    except Exception:
        pass
    return b''


def _le_int(value, default: int = 0) -> int:
    """Parse a OneNote little-endian int."""
    b = _as_bytes(value)
    return int.from_bytes(b[:4], 'little') if b else default
