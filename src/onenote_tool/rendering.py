"""Turn decoded runs/tables/formatting into neutral, semantic HTML fragments.

Everything here produces CONTENT, not layout — no positioning, no page-
specific wrapper markup. See `models.PageResult.blocks` for the shape a
caller receives; this module is what fills in each block's `html`.
"""
from __future__ import annotations

import html
import re
from typing import Optional

from .stamps import _is_stamp
from .text import _as_bytes, _clean_text, _le_int, _norm_identity, decode_run_text

_TAG_RE = re.compile(r'<[^>]+>')


def _has_visible_content(html_str: str) -> bool:
    """False for a text block that renders as nothing — no text after
    stripping tags, and no embedded media (image/table/svg) that would
    still be visible with zero text."""
    if _TAG_RE.sub('', html_str).strip():
        return True
    return bool(re.search(r'<(img|svg|table)\b', html_str, re.IGNORECASE))


def _image_block_html(ref: str) -> str:
    """The `html` fragment for an image block — just the `<img>` itself; the
    caller supplies `ref` as the `src` (a placeholder token until it uploads
    the real bytes and substitutes the real URL)."""
    src = html.escape(ref, quote=True)
    return f'<img src="{src}" alt="">'


_RTU_MARKER = '﷟'  # noqa (U+FDDF hyperlink field marker in RichEditTextUnicode)

# Bare URLs written as plain text (no HYPERLINK field) are auto-linked too.
_URL_TLDS = (
    'com|org|net|edu|gov|mil|int|io|co|dev|me|tv|info|biz|app|gg|xyz|ai|ly|to|'
    'uk|de|fr|ee|ru|jp|cn|us|ca|nl|se|no|fi|pl|es|it|au|br|in|eu'
)
_URL_TOKEN_RE = re.compile(
    r'^(?:'
    r'https?://\S+'                                  # explicit scheme
    r'|www\.\S+\.\S+'                                # www.domain.tld...
    r'|(?:[a-z0-9][a-z0-9-]*\.)+(?:' + _URL_TLDS + r')(?:/\S*)?'  # domain.tld[/path]
    r')$',
    re.IGNORECASE,
)
_LEAD_PUNCT = '("\'<'
_TRAIL_PUNCT = ')].,;:!?"\'>'


def _color_from_bytes(value) -> Optional[str]:
    """OneNote colour prop: 4 bytes R,G,B,alpha. Alpha 0x00 = explicit colour;
    0xFF (automatic/none) = inherit -> return None so we don't override."""
    b = _as_bytes(value)
    if len(b) < 4 or b[3] != 0x00:
        return None
    return f'#{b[0]:02x}{b[1]:02x}{b[2]:02x}'


def _heading_level(style_id) -> Optional[int]:
    """OneNote built-in heading paragraph styles are 'h1'..'h6'."""
    s = str(style_id or '').replace('\x00', '').strip().lower()
    if len(s) == 2 and s[0] == 'h' and s[1] in '123456':
        return int(s[1])
    return None


def _style_flags(obj) -> dict:
    """Char-style fields off a jcidParagraphStyleObjectForText object."""
    v = obj.get('val', {})
    return {
        'bold': v.get('Bold') == 'True',
        'italic': v.get('Italic') == 'True',
        'underline': v.get('Underline') == 'True' or _le_int(v.get('UnderlineType'), 0) != 0,
        'strike': v.get('Strikethrough') == 'True',
        'color': _color_from_bytes(v.get('FontColor')),
        'highlight': _color_from_bytes(v.get('Highlight')),
    }


def _resolve_run_format(ps_ref, trf_ref, by_id) -> Optional[dict]:
    """Resolve a run's formatting. ParagraphStyle = the paragraph's base style
    (heading level + style defaults like the h1 blue); TextRunFormatting =
    the run's *manual* overrides (bold/italic/underline/colour/highlight the
    user applied). Overrides win. TextRunFormatting is a list of run-index
    spans; we apply the first (covers uniformly-styled runs)."""
    fmt = {'bold': False, 'italic': False, 'underline': False, 'strike': False,
           'color': None, 'highlight': None, 'heading': None}

    ps = by_id.get(_norm_identity(ps_ref[0] if isinstance(ps_ref, list) else ps_ref))
    if ps:
        base = _style_flags(ps)
        for key, cur in base.items():
            if cur:
                fmt[key] = cur
        fmt['heading'] = _heading_level(ps.get('val', {}).get('ParagraphStyleId'))

    tr_ref = (trf_ref[0] if trf_ref else None) if isinstance(trf_ref, list) else trf_ref
    trf = by_id.get(_norm_identity(tr_ref)) if tr_ref else None
    if trf:
        over = _style_flags(trf)
        for key in ('bold', 'italic', 'underline', 'strike'):
            if over[key]:
                fmt[key] = True
        if over['color']:
            fmt['color'] = over['color']
        if over['highlight']:
            fmt['highlight'] = over['highlight']

    return fmt if any(fmt.values()) else None


def _apply_marks(inner: str, fmt: Optional[dict]) -> str:
    """Wrap inline HTML in marks matching a resolved run format."""
    if not fmt:
        return inner
    if fmt.get('bold'):
        inner = f'<strong>{inner}</strong>'
    if fmt.get('italic'):
        inner = f'<em>{inner}</em>'
    if fmt.get('underline'):
        inner = f'<u>{inner}</u>'
    if fmt.get('strike'):
        inner = f'<s>{inner}</s>'
    if fmt.get('color'):
        inner = f'<span style="color:{fmt["color"]}">{inner}</span>'
    if fmt.get('highlight'):
        inner = f'<span style="background-color:{fmt["highlight"]}">{inner}</span>'
    return inner


def _placeholder_text(desc: str, reason: str = '') -> str:
    txt = f'[Here should be: {desc}'
    if reason:
        txt += f' — {reason[:120]}'
    return txt + ']'


def _placeholder_run(desc: str, reason: str = '') -> dict:
    """A visible, editable marker left where an element couldn't be imported."""
    txt = _placeholder_text(desc, reason)
    return {'t': txt, 'h': html.escape(txt), 'rid': None, 'placeholder': True}


def _placeholder_blocks(desc: str, reason: str = '') -> list:
    """A single-block placeholder page/section body — see `models.PageResult.blocks`."""
    inner = html.escape(_placeholder_text(desc, reason))
    return [{'kind': 'text', 'html': f'<p>{inner}</p>'}]


def _href_for(token: str) -> str:
    return token if token.lower().startswith(('http://', 'https://')) else 'https://' + token


def _linkify(text: str) -> str:
    """Escape text and wrap any token that verifies as a URL in an <a href>."""
    out = []
    for token in re.split(r'(\s+)', text):
        if not token or token.isspace():
            out.append(html.escape(token))
            continue
        lead = ''
        while token and token[0] in _LEAD_PUNCT:
            lead += token[0]
            token = token[1:]
        trail = ''
        while token and token[-1] in _TRAIL_PUNCT:
            trail = token[-1] + trail
            token = token[:-1]
        if token and _URL_TOKEN_RE.match(token):
            href = html.escape(_href_for(token), quote=True)
            out.append(f'{html.escape(lead)}<a href="{href}">{html.escape(token)}</a>{html.escape(trail)}')
        else:
            out.append(html.escape(lead + token + trail))
    return ''.join(out)


def _rtu_to_inline_html(rtu: str):
    """Parse a RichEditTextUnicode run into (plain_text, inline_html).

    Links are stored as `﷟HYPERLINK "url"displaytext`; the display text
    runs to the next marker (exact for standalone links, mildly over-captured
    for inline ones — all text and the URL are preserved either way).
    """
    s = str(rtu).replace('\x00', '')
    segments = s.split(_RTU_MARKER)
    plain_parts = [segments[0]]
    html_parts = [_linkify(segments[0])]
    for seg in segments[1:]:
        m = re.match(r'\s*HYPERLINK\s+"([^"]*)"(.*)', seg, re.DOTALL)
        if m:
            url, disp = m.group(1), m.group(2)
            plain_parts.append(disp)
            display = html.escape(disp) or html.escape(url)
            html_parts.append(f'<a href="{html.escape(url, quote=True)}">{display}</a>')
        else:
            plain_parts.append(seg)
            html_parts.append(_linkify(seg))
    plain = _clean_text(''.join(plain_parts))
    inline = ''.join(html_parts).strip()
    return plain, inline


def _youtube_embed_html(yt: dict) -> str:
    vid = yt['videoId']
    url = yt['url']
    title = yt.get('title') or url
    thumb_ref = yt.get('thumb_ref')
    thumb_src = thumb_ref or f'https://img.youtube.com/vi/{vid}/hqdefault.jpg'
    esc = lambda v: html.escape(v, quote=True)
    return (
        f'<div class="onenote-youtube-embed" data-youtube-id="{esc(vid)}" '
        f'data-url="{esc(url)}" data-title="{esc(title)}" data-thumbnail="{esc(thumb_ref or "")}">'
        f'<a class="onenote-youtube-title" href="{esc(url)}" target="_blank" rel="noopener noreferrer">{html.escape(title)}</a>'
        f'<img class="onenote-youtube-thumb" src="{esc(thumb_src)}" alt="{html.escape(title)}"></div>'
    )


_BULLET_CHARS = set('•◦▪‣·∙-–—*o')


def _list_is_ordered(fmt: str) -> bool:
    """A jcidNumberListNode's NumberListFormat: a bullet glyph -> unordered,
    anything else (digit/letter/roman format codes) -> ordered."""
    chars = [c for c in str(fmt) if ord(c) >= 0x20]
    if not chars:
        return False
    return not any(c in _BULLET_CHARS for c in chars)


def _render_list(items: list) -> str:
    """items: [{'ordered': bool, 'level': int, 'html': str}] -> nested <ul>/<ol>.
    Consecutive items at a deeper level nest; a type change at the same level
    closes and reopens (<li><p>..</p></li>)."""
    out = []
    stack = []  # (ordered, level)
    for it in items:
        lvl, ordered = it['level'], it['ordered']
        while stack and stack[-1][1] > lvl:
            out.append('</ol>' if stack[-1][0] else '</ul>')
            stack.pop()
        if stack and stack[-1][1] == lvl and stack[-1][0] != ordered:
            out.append('</ol>' if stack[-1][0] else '</ul>')
            stack.pop()
        if not stack or stack[-1][1] < lvl:
            out.append('<ol>' if ordered else '<ul>')
            stack.append((ordered, lvl))
        out.append(f'<li>{it["html"]}</li>')
    while stack:
        out.append('</ol>' if stack.pop()[0] else '</ul>')
    return ''.join(out)


def _runs_to_paragraphs(runs: list) -> str:
    """Each run is a dict {'t': plain text, 'h': inline html or None, 'yt': ...,
    'list': {'ordered', 'level'} or absent}."""
    parts = []
    pending = []  # consecutive list items -> one <ul>/<ol>

    # Trim leading/trailing blank spacer runs (keep only blanks between content).
    core = list(runs)
    while core and core[0].get('blank'):
        core.pop(0)
    while core and core[-1].get('blank'):
        core.pop()

    def flush():
        if pending:
            parts.append(_render_list(pending))
            pending.clear()

    for run in core:
        if run.get('blank'):
            flush()
            parts.append('<p></p>')
            continue
        if run.get('yt'):
            flush()
            parts.append(_youtube_embed_html(run['yt']))
            continue
        inner = run['h'] if run.get('h') else _linkify(_clean_text(run['t']))
        if not inner:
            continue
        fmt = run.get('fmt')
        inner = _apply_marks(inner, fmt)
        li = run.get('list')
        if li:
            pending.append({'ordered': li['ordered'], 'level': li['level'], 'html': f'<p>{inner}</p>'})
            continue
        flush()
        level = fmt.get('heading') if fmt else None
        if level:
            tag = f'h{min(level, 3)}'  # h1..h3 only, matching common editor support
            parts.append(f'<{tag}>{inner}</{tag}>')
        else:
            parts.append(f'<p>{inner}</p>')
    flush()
    return ''.join(parts) or '<p></p>'


_CHILD_KEYS = ('ElementChildNodesOfVersionHistory', 'ContentChildNodesOfPageManifest', 'StructureElementChildNodes')


def _child_ids(prop) -> list:
    """Ordered child object ids referenced by a node (rows/cells/content)."""
    out = []
    val = prop.get('val', {}) if prop else {}
    for key in _CHILD_KEYS:
        refs = val.get(key)
        if isinstance(refs, list):
            out.extend(_norm_identity(r) for r in refs)
    return [x for x in out if x]


def _resolve_cell_html(nid, by_id, consumed, depth=0, seen=None) -> str:
    """Recursively render a table cell's content (text + links) from the object
    tree, recording the run identities consumed so they aren't re-emitted."""
    seen = seen if seen is not None else set()
    if depth > 8 or not nid or nid in seen:
        return ''
    seen.add(nid)
    prop = by_id.get(nid)
    if not prop:
        return ''
    parts = []
    if prop.get('type') == 'jcidRichTextOENode':
        consumed.add(nid)
        val = prop.get('val', {})
        rtu = val.get('RichEditTextUnicode')
        if isinstance(rtu, str) and _RTU_MARKER in rtu:
            _, inline = _rtu_to_inline_html(rtu)
            if inline:
                parts.append(inline)
        else:
            raw = val.get('TextExtendedAscii')
            text = _clean_text(decode_run_text(raw)) if raw is not None else ''
            if text and not _is_stamp(text):
                parts.append(_linkify(text))
    for cid in _child_ids(prop):
        child = _resolve_cell_html(cid, by_id, consumed, depth + 1, seen)
        if child:
            parts.append(child)
    return ' '.join(parts)


def _resolve_table_grid(table_prop, by_id, consumed) -> list:
    """Walk table -> rows -> cells -> content into a 2D grid of cell HTML.

    Relies on the patched object-stream read so references resolve distinctly.
    """
    grid = []
    for rid in _child_ids(table_prop):
        row = by_id.get(rid)
        if not row or row.get('type') != 'jcidTableRowNode':
            continue
        grid.append([_resolve_cell_html(cid, by_id, consumed) for cid in _child_ids(row)])
    return grid


def _table_html(grid: list) -> str:
    """grid is rows of inline-HTML strings (or None) -> a <table> fragment."""
    rows_html = []
    for row in grid:
        cells = ''.join(f'<td><p>{cell or ""}</p></td>' for cell in row)
        rows_html.append(f'<tr>{cells}</tr>')
    return f'<table>{"".join(rows_html)}</table>'
