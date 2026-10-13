"""OneNote inline equations -> MathML.

An equation run's text is RichEdit's internal math form: the formula's
glyphs plus three structural noncharacters — U+FDD0 starts a math object,
U+FDEE separates its arguments, U+FDEF ends it. The text alone doesn't say
what each object is. That lives in the run's `TextRunData` (an
ArrayOfPropertyValues, one nested property set per text run, with run
boundaries in `TextRunIndex`): the run starting an object carries its type
(`0x1400344F`) and, where it has one, its operator or delimiter characters
(`0x10003453`, `0x10003454`). The type values are RichEdit's own math
object types (TOM's `tomBrackets` = 13, `tomNary` = 21, `tomStack` = 27,
`tomSuperscript` = 31, ...).

Only object types verified against a real equation (and OneNote's own
rendering of it) are converted; an equation using any other type returns
None so the caller keeps its placeholder rather than guessing at a layout.
"""
from __future__ import annotations

import bisect
import html
import struct
import unicodedata
from typing import Optional

_PRID_RICH_TEXT_UNICODE = 0x1C001C22
_PRID_TEXT_RUN_INDEX = 0x1C001E12
_PRID_TEXT_RUN_DATA = 0x40003499
_PRID_MATH_OBJECT_TYPE = 0x1400344F
_PRID_MATH_CHAR = 0x10003453
_PRID_MATH_CHAR2 = 0x10003454

_START, _SEP, _END = '﷐', '﷮', '﷯'

_BRACKETS = 13
_NARY = 21
_STACK = 27
_SUPERSCRIPT = 31
_VERIFIED_TYPES = {_BRACKETS, _NARY, _STACK, _SUPERSCRIPT}


def _raw_values(body) -> dict:
    out = {}
    for prid, data in zip(getattr(body, 'rgPrids', []), getattr(body, 'rgData', [])):
        out[prid.value & 0x7FFFFFFF] = data
    return out


def _bytes_of(data) -> bytes:
    data = getattr(data, 'Data', data)
    return bytes(data) if isinstance(data, (bytes, bytearray)) else b''


def _run_props(run_set) -> dict:
    values = _raw_values(run_set)
    props = {}
    kind = _bytes_of(values.get(_PRID_MATH_OBJECT_TYPE))
    if len(kind) >= 4:
        props['type'] = struct.unpack('<I', kind[:4])[0] & 0xFFFF
    for key, prid in (('char', _PRID_MATH_CHAR), ('char2', _PRID_MATH_CHAR2)):
        raw = _bytes_of(values.get(prid))
        if len(raw) >= 2:
            props[key] = chr(struct.unpack('<H', raw[:2])[0])
    return props


def _parse(text: str, run_ends: list, run_data: list):
    """Nested structure: a list of str (single characters) and dicts
    {'type', 'char', 'char2', 'args': [list, ...]}. None if malformed or an
    unverified object type is present."""
    root = []
    stack = []  # open objects
    offset = 0  # UTF-16 code-unit offset (run boundaries count in UTF-16)

    def current():
        return stack[-1]['args'][-1] if stack else root

    for ch in text:
        if ch == _START:
            run = bisect.bisect_right(run_ends, offset)
            props = _run_props(run_data[run]) if run < len(run_data) else {}
            if props.get('type') not in _VERIFIED_TYPES:
                return None
            obj = dict(props, args=[[]])
            current().append(obj)
            stack.append(obj)
        elif ch == _SEP:
            if not stack:
                return None
            stack[-1]['args'].append([])
        elif ch == _END:
            if not stack:
                return None
            stack.pop()
        elif ch != '\x00':
            current().append(ch)
        offset += 2 if ord(ch) > 0xFFFF else 1
    return None if stack else root


def _leaf_kind(ch: str) -> str:
    cat = unicodedata.category(ch)
    if cat.startswith('L'):
        return 'mi'
    if cat == 'Nd' or unicodedata.digit(ch, None) is not None:
        return 'mn'
    return 'mo'


def _render(items) -> str:
    out = []
    digits = []

    def flush_digits():
        if digits:
            out.append(f'<mn>{html.escape("".join(digits))}</mn>')
            digits.clear()

    for item in items:
        if isinstance(item, str):
            kind = _leaf_kind(item)
            if kind == 'mn':
                digits.append(item)
                continue
            flush_digits()
            out.append(f'<{kind}>{html.escape(item)}</{kind}>')
            continue
        flush_digits()
        out.append(_render_object(item))
    flush_digits()
    return ''.join(out)


def _row(items) -> str:
    return f'<mrow>{_render(items)}</mrow>'


def _render_object(obj) -> str:
    args = obj['args']
    kind = obj['type']
    if kind == _SUPERSCRIPT and len(args) == 2:
        return f'<msup>{_row(args[0])}{_row(args[1])}</msup>'
    if kind == _BRACKETS and len(args) == 1:
        opening = html.escape(obj.get('char', '('))
        closing = html.escape(obj.get('char2', ')'))
        return f'<mrow><mo>{opening}</mo>{_render(args[0])}<mo>{closing}</mo></mrow>'
    if kind == _STACK and len(args) == 2:
        return f'<mfrac linethickness="0">{_row(args[0])}{_row(args[1])}</mfrac>'
    if kind == _NARY and len(args) == 3:
        op = f'<mo>{html.escape(obj.get("char", chr(0x222B)))}</mo>'
        lower, upper, body = args
        if lower and upper:
            head = f'<munderover>{op}{_row(lower)}{_row(upper)}</munderover>'
        elif lower:
            head = f'<munder>{op}{_row(lower)}</munder>'
        elif upper:
            head = f'<mover>{op}{_row(upper)}</mover>'
        else:
            head = op
        return f'<mrow>{head}{_render(body)}</mrow>'
    raise ValueError('unexpected argument count')


def equation_mathml(body) -> Optional[str]:
    """MathML for an equation run's raw PropertySet `body`, or None when it
    can't be converted faithfully (missing data, or an object type not
    verified yet)."""
    values = _raw_values(body)
    raw_text = _bytes_of(values.get(_PRID_RICH_TEXT_UNICODE))
    run_data = values.get(_PRID_TEXT_RUN_DATA)
    raw_index = _bytes_of(values.get(_PRID_TEXT_RUN_INDEX))
    if not raw_text or not isinstance(run_data, list) or len(raw_index) % 4:
        return None
    text = raw_text.decode('utf-16-le', 'replace')
    run_ends = list(struct.unpack(f'<{len(raw_index) // 4}I', raw_index))
    tree = _parse(text, run_ends, run_data)
    if not tree:
        return None
    try:
        inner = _render(tree)
    except ValueError:
        return None
    return f'<math xmlns="http://www.w3.org/1998/Math/MathML">{inner}</math>'
