"""Decode and render OneNote ink/drawing objects (best-effort).

Microsoft doesn't document these object types at all — see
`onenote_tool.parser`'s `_INK_JCID_RAW` comment (a Microsoft Open
Specifications team member said outright "we have decided not to document
these drawing objects"). But the object model and point encoding ARE
reverse-engineered and published by github.com/msiemens/onenote.rs
(`crates/parser/src/onenote/ink.rs`, `.../one/property_set/ink_*.rs`,
`.../shared/multi_byte.rs`) — this module follows that reference
implementation's jcid/property IDs (confirmed to match this codebase's own
independently-found `_INK_JCID_RAW` values) and the point-path encoding,
which turns out to be the genuinely-Microsoft-published **Ink Serialized
Format** (ISF, from the Tablet PC SDK) "multi-byte encoding of signed
numbers" — Microsoft just never cross-referenced ISF from the OneNote specs.

Object graph, one ink drawing:
    InkContainer -[InkData]-> InkDataNode -[InkStrokes]-> InkStrokeNode(s)
                                                                |
                                                    [InkStrokeProperties]
                                                                v
                                                     StrokePropertiesNode

An InkContainer with no InkData instead has [ContentChildNodes] pointing at
nested InkContainers (a group of drawings) — recurse.

Rendering is necessarily best-effort: pen pressure/tilt, color, and exact
line dynamics are not reproduced — every stroke is rendered as a plain
constant-width polyline. Good enough to make a drawing recognizable instead
of invisible.
"""
from __future__ import annotations

import struct
import uuid
from dataclasses import dataclass
from typing import Optional

JCID_INK_CONTAINER = 0x00060014  # public: onenote_tool.recovery groups top-level containers by this
_JCID_INK_CONTAINER = JCID_INK_CONTAINER
_JCID_INK_DATA_NODE = 0x0002003B
_JCID_INK_STROKE_NODE = 0x00020047
_JCID_STROKE_PROPERTIES_NODE = 0x00120048

_PRID_INK_DATA = 0x20003415
_PRID_CONTENT_CHILD_NODES = 0x24001C1F
_PRID_INK_SCALING_X = 0x14001C46
_PRID_INK_SCALING_Y = 0x14001C47
_PRID_INK_STROKES = 0x24003416
_PRID_INK_BOUNDING_BOX = 0x1C003418
_PRID_INK_PATH = 0x1C00340B
_PRID_INK_STROKE_PROPERTIES = 0x20003409
_PRID_INK_DIMENSIONS = 0x1C00340A
_PRID_INK_WIDTH = 0x1400340D
_PRID_INK_COLOR = 0x1400340F

# MS-ISF dimension identifiers (mixed-endian GUID byte layout — `.bytes_le`
# is exactly the Windows/COM in-memory GUID representation), from
# onenote.rs's `parse_ink_path`.
_DIM_GUID_X = uuid.UUID('598a6a8f-52c0-4ba0-93af-af357411a561').bytes_le
_DIM_GUID_Y = uuid.UUID('b53f9f75-04e0-4498-a7ee-c30dbb5a9011').bytes_le

_MAX_NESTING_DEPTH = 16


def _decode_multi_byte_uint(data: bytes, index: int) -> tuple:
    """[MS-ISF] multi-byte unsigned integer: 7 payload bits per byte,
    little-endian, continuation while the top bit is set."""
    value = 0
    count = 0
    while True:
        if index + count >= len(data):
            raise ValueError('multi-byte integer truncated')
        if count >= 10:
            raise ValueError('multi-byte integer too wide')
        byte = data[index + count]
        value |= (byte & 0x7F) << (count * 7)
        count += 1
        if not (byte & 0x80):
            break
    return value, count


def decode_multi_byte_signed(data: bytes) -> list:
    """[MS-ISF] "Multi-byte Encoding of Signed Numbers": the stream opens
    with a multi-byte uint whose value, halved, is the element count (the
    low bit is a leftover sign bit on the length itself — always 0 in
    practice, per onenote.rs); each following multi-byte uint's low bit is
    the sign of that value, the remaining bits its magnitude."""
    length, offset = _decode_multi_byte_uint(data, 0)
    length >>= 1
    values = []
    index = offset
    for _ in range(length):
        raw, consumed = _decode_multi_byte_uint(data, index)
        index += consumed
        values.append(-(raw >> 1) if raw & 1 else (raw >> 1))
    return values


@dataclass
class _InkDimension:
    guid: bytes
    limit_lower: int
    limit_upper: int


def _parse_ink_dimensions(blob: bytes) -> list:
    dims = []
    for i in range(0, len(blob) - 31, 32):
        chunk = blob[i:i + 32]
        lower, upper = struct.unpack('<ii', chunk[16:24])
        dims.append(_InkDimension(chunk[:16], lower, upper))
    return dims


def _raw_value(prop: dict, prid_value: int):
    """Look up a property by its raw numeric PropertyID, bypassing
    pyOneNote's `get_properties()` name-table filter (see
    `onenote_tool._patches`'s `_get_properties` patch, which keeps the raw
    PropertySet body on each prop dict as `'body'` for exactly this)."""
    body = prop.get('body')
    if body is None:
        return None
    for prid, val in zip(body.rgPrids, body.rgData):
        if prid.value == prid_value:
            return val
    return None


def _raw_bytes(prop: dict, prid_value: int) -> Optional[bytes]:
    val = _raw_value(prop, prid_value)
    if val is None:
        return None
    data = getattr(val, 'Data', None)  # PrtFourBytesOfLengthFollowedByData
    if data is not None:
        return data
    if isinstance(val, (bytes, bytearray)):
        return bytes(val)
    return None


def _raw_float(prop: dict, prid_value: int) -> Optional[float]:
    b = _raw_bytes(prop, prid_value)
    return struct.unpack('<f', b)[0] if b and len(b) == 4 else None


def _raw_identity_list(prop: dict, prid_value: int, norm_identity) -> list:
    val = _raw_value(prop, prid_value)
    if not isinstance(val, list):
        return []
    out = []
    for item in val:
        ident = norm_identity(str(item))
        if ident:
            out.append(ident)
    return out


def _accumulate(deltas: list) -> list:
    total = 0
    out = []
    for d in deltas:
        total += d
        out.append(total)
    return out


def _color_from_bytes(b: Optional[bytes]) -> Optional[str]:
    """OneNote colour prop: 4 bytes R,G,B,alpha. Alpha 0x00 = explicit colour;
    0xFF (automatic/none) = inherit -> None (same convention as
    `onenote_tool.rendering`'s `_color_from_bytes`, duplicated here in this
    dependency-free module rather than imported, to avoid a circular
    import — `onenote_tool.parser` is the one that imports THIS module)."""
    if not b or len(b) < 4 or b[3] != 0x00:
        return None
    return f'#{b[0]:02x}{b[1]:02x}{b[2]:02x}'


def _collect_stroke(by_id: dict, stroke_id: str, scale_x: float, scale_y: float, norm_identity):
    prop = by_id.get(stroke_id)
    if prop is None or prop.get('jcid_raw') != _JCID_INK_STROKE_NODE:
        return [], None, None
    path_blob = _raw_bytes(prop, _PRID_INK_PATH)
    props_ids = _raw_identity_list(prop, _PRID_INK_STROKE_PROPERTIES, norm_identity)
    props_prop = by_id.get(props_ids[0]) if props_ids else None
    if path_blob is None or props_prop is None:
        return [], None, None

    dims_blob = _raw_bytes(props_prop, _PRID_INK_DIMENSIONS)
    if not dims_blob:
        return [], None, None
    dims = _parse_ink_dimensions(dims_blob)
    idx_x = next((i for i, d in enumerate(dims) if d.guid == _DIM_GUID_X), None)
    idx_y = next((i for i, d in enumerate(dims) if d.guid == _DIM_GUID_Y), None)
    if idx_x is None or idx_y is None or not dims:
        return [], None, None

    try:
        values = decode_multi_byte_signed(path_blob)
    except ValueError:
        return [], None, None
    dim_count = len(dims)
    if dim_count == 0 or len(values) % dim_count != 0:
        return [], None, None
    dim_len = len(values) // dim_count
    # Per [MS-ISF], each dimension's decoded values are DIFFERENTIAL, not
    # absolute — confirmed empirically: a third ("pressure", 0-32767
    # declared range) dimension's raw decoded values go negative, which is
    # invalid for a pressure reading, while their running total never
    # leaves that range. Same accumulation applies to X/Y.
    xs = _accumulate(values[idx_x * dim_len:(idx_x + 1) * dim_len])
    ys = _accumulate(values[idx_y * dim_len:(idx_y + 1) * dim_len])
    points = [(x * scale_x, y * scale_y) for x, y in zip(xs, ys)]

    width = _raw_float(props_prop, _PRID_INK_WIDTH) or 30.0
    color = _color_from_bytes(_raw_bytes(props_prop, _PRID_INK_COLOR))
    return points, width * ((abs(scale_x) + abs(scale_y)) / 2), color


def _collect_ink_data(by_id: dict, ink_data_id: str, scale_x: float, scale_y: float, norm_identity):
    prop = by_id.get(ink_data_id)
    if prop is None or prop.get('jcid_raw') != _JCID_INK_DATA_NODE:
        return []
    stroke_ids = _raw_identity_list(prop, _PRID_INK_STROKES, norm_identity)
    strokes = []
    for sid in stroke_ids:
        points, width, color = _collect_stroke(by_id, sid, scale_x, scale_y, norm_identity)
        if points:
            strokes.append((points, width, color))
    return strokes


def _collect_container(by_id: dict, container_id: str, scale_x: float, scale_y: float, depth: int, norm_identity):
    if depth > _MAX_NESTING_DEPTH:
        return []
    prop = by_id.get(container_id)
    if prop is None or prop.get('jcid_raw') != _JCID_INK_CONTAINER:
        return []
    sx = _raw_float(prop, _PRID_INK_SCALING_X) or scale_x
    sy = _raw_float(prop, _PRID_INK_SCALING_Y) or scale_y

    ink_data_ids = _raw_identity_list(prop, _PRID_INK_DATA, norm_identity)
    if ink_data_ids:
        return _collect_ink_data(by_id, ink_data_ids[0], sx, sy, norm_identity)

    strokes = []
    for child_id in _raw_identity_list(prop, _PRID_CONTENT_CHILD_NODES, norm_identity):
        strokes.extend(_collect_container(by_id, child_id, sx, sy, depth + 1, norm_identity))
    return strokes


_DEFAULT_STROKE_COLOR = '#1a1a1a'


def _strokes_to_svg(strokes: list) -> Optional[str]:
    xs = [p[0] for pts, _, _ in strokes for p in pts]
    ys = [p[1] for pts, _, _ in strokes for p in pts]
    if not xs:
        return None
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    pad = max(1.0, (max_x - min_x) * 0.03, (max_y - min_y) * 0.03)
    vb_x, vb_y = min_x - pad, min_y - pad
    vb_w, vb_h = (max_x - min_x) + 2 * pad, (max_y - min_y) + 2 * pad
    if vb_w <= 0 or vb_h <= 0:
        return None

    parts = [
        f'<svg viewBox="{vb_x:.1f} {vb_y:.1f} {vb_w:.1f} {vb_h:.1f}" '
        f'xmlns="http://www.w3.org/2000/svg" style="width:100%;height:auto;display:block;background:#fff">'
    ]
    for points, width, color in strokes:
        w = max(width, 1.0)
        c = color or _DEFAULT_STROKE_COLOR
        if len(points) < 2:
            x, y = points[0]
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{w / 2:.1f}" fill="{c}"/>')
            continue
        d = 'M ' + ' L '.join(f'{x:.1f},{y:.1f}' for x, y in points)
        parts.append(
            f'<path d="{d}" fill="none" stroke="{c}" stroke-width="{w:.1f}" '
            f'stroke-linecap="round" stroke-linejoin="round"/>'
        )
    parts.append('</svg>')
    return ''.join(parts)


def render_ink_containers(by_id: dict, container_ids: list, norm_identity) -> Optional[str]:
    """`container_ids`: identity strings of a page's top-level InkContainer
    objects. Returns an inline SVG fragment with every recovered stroke
    (auto-fit viewBox — absolute coordinate units aren't documented, so
    strokes are rendered at their relative scale, not matched to page
    pixels), or None if nothing could be decoded (caller keeps its existing
    "unsupported" placeholder in that case)."""
    all_strokes = []
    for cid in container_ids:
        all_strokes.extend(_collect_container(by_id, cid, 1.0, 1.0, 0, norm_identity))
    return _strokes_to_svg(all_strokes)
