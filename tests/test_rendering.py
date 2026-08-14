from onenote_tool.models import PageImage
from onenote_tool.rendering import (
    _apply_marks,
    _cell_merge_span,
    _font_size_pt,
    _has_visible_content,
    _image_block_html,
    _linkify,
    _media_block_html,
    _paragraph_alignment,
    _placeholder_blocks,
    _render_list,
    _resolve_cell_html,
    _resolve_run_format,
    _resolve_table_grid,
    _runs_to_paragraphs,
    _table_borders_visible,
    _table_column_widths_px,
    _table_html,
)


class _FakePrid:
    def __init__(self, value):
        self.value = value


class _FakeBody:
    """Stands in for pyOneNote's real PropertySet body — just enough shape
    (`rgPrids`/`rgData` in parallel, `.value` on each prid) for `_cell_merge_span`
    to walk, same as the real object `_patches._get_properties` keeps on
    `prop['body']`."""

    def __init__(self, pairs):
        self.rgPrids = [_FakePrid(k) for k, _ in pairs]
        self.rgData = [v for _, v in pairs]


class _FakeBlob:
    """Stands in for pyOneNote's `PrtFourBytesOfLengthFollowedByData` — a
    variable-length property's raw bytes live on `.Data`, not in `rgData`
    directly (unlike a fixed-size property like the merge span, see
    `_FakeBody` above)."""

    def __init__(self, data):
        self.Data = data


def test_apply_marks_bold_italic():
    fmt = {'bold': True, 'italic': True, 'underline': False, 'strike': False, 'color': None, 'highlight': None}
    assert _apply_marks('hello', fmt) == '<em><strong>hello</strong></em>'


def test_apply_marks_color_and_highlight():
    fmt = {'bold': False, 'italic': False, 'underline': False, 'strike': False, 'color': '#ff0000', 'highlight': '#ffff00'}
    out = _apply_marks('hi', fmt)
    assert 'color:#ff0000' in out
    assert 'background-color:#ffff00' in out


def test_apply_marks_no_format_is_passthrough():
    assert _apply_marks('plain', None) == 'plain'


def test_apply_marks_font_size_and_family():
    fmt = {'bold': False, 'italic': False, 'underline': False, 'strike': False,
           'color': None, 'highlight': None, 'font_size': 36, 'font_family': 'Consolas'}
    out = _apply_marks('hi', fmt)
    assert 'font-size:36pt' in out
    assert 'font-family:Consolas' in out


def test_font_size_pt_half_points_to_points():
    assert _font_size_pt(b'H\x00') == 36    # 0x0048 half-points = 36pt
    assert _font_size_pt(b'\x12\x00') == 9  # 0x0012 half-points = 9pt


def test_font_size_pt_zero_or_missing_is_none():
    assert _font_size_pt(b'\x00\x00') is None
    assert _font_size_pt(None) is None


def test_resolve_run_format_font_size_trf_overrides_paragraph_default():
    guid = '11111111-1111-1111-1111-111111111111'
    ps_ref = [f'<ExtendedGUID> ({guid}, 1)']
    trf_ref = [f'<ExtendedGUID> ({guid}, 2)']
    by_id = {
        f'{guid},1': {'val': {'FontSize': b'\x16\x00'}},  # paragraph default, 11pt
        f'{guid},2': {'val': {'FontSize': b'\x18\x00'}},  # explicit override, 12pt
    }
    fmt = _resolve_run_format(ps_ref, trf_ref, by_id)
    assert fmt['font_size'] == 12


def test_resolve_run_format_no_trf_override_falls_back_to_paragraph_font_size():
    # Real bug, verified against a real page (Test/Rich content/"Section
    # page 1"): a 'cite'-styled paragraph's own genuinely-different FontSize
    # (9pt vs. an ordinary body paragraph's 11pt) lives ONLY on
    # ParagraphStyle, with no TextRunFormatting override at all — since the
    # run's real size already equals its OWN paragraph's default, OneNote
    # correctly wrote no per-run override. Reading font size/family ONLY
    # from TextRunFormatting (the old behavior this replaces) silently lost
    # that distinguishing size. OneNote's own MHT export confirms every
    # paragraph carries an explicit font-size, not just "special" ones, so
    # ParagraphStyle's own FontSize/Font is now the base (same pattern as
    # bold/italic/color/highlight below), with TextRunFormatting still
    # overriding when it differs.
    guid = '22222222-2222-2222-2222-222222222222'
    ps_ref = [f'<ExtendedGUID> ({guid}, 1)']
    trf_ref = [f'<ExtendedGUID> ({guid}, 2)']
    by_id = {
        f'{guid},1': {'val': {'FontSize': b'\x16\x00', 'Bold': 'True'}},
        f'{guid},2': {'val': {}},
    }
    fmt = _resolve_run_format(ps_ref, trf_ref, by_id)
    assert fmt['font_size'] == 11
    assert fmt['font_family'] is None
    assert fmt['bold'] is True


def test_media_block_html_audio_and_video():
    assert _media_block_html('audio', 'onenote-file://1') == '<audio controls src="onenote-file://1"></audio>'
    assert _media_block_html('video', 'onenote-file://2') == '<video controls src="onenote-file://2"></video>'


def test_paragraph_alignment_center_and_right():
    assert _paragraph_alignment(b'\x01') == 'center'
    assert _paragraph_alignment(b'\x02') == 'right'


def test_paragraph_alignment_absent_or_unknown_is_none():
    assert _paragraph_alignment(None) is None
    assert _paragraph_alignment(b'\x00') is None  # left is the unwritten default
    assert _paragraph_alignment(b'\x09') is None  # unrecognized byte -> not guessed at


def test_runs_to_paragraphs_applies_alignment():
    runs = [{'t': 'Righty', 'h': None, 'align': 'right'}]
    assert _runs_to_paragraphs(runs) == '<p style="text-align:right">Righty</p>'


def test_runs_to_paragraphs_no_alignment_is_plain_p():
    runs = [{'t': 'Lefty', 'h': None}]
    assert _runs_to_paragraphs(runs) == '<p>Lefty</p>'


def test_linkify_wraps_bare_url():
    out = _linkify('see https://example.com/path for details')
    assert '<a href="https://example.com/path">https://example.com/path</a>' in out


def test_linkify_leaves_plain_text_alone():
    out = _linkify('just some words here')
    assert '<a' not in out


def test_runs_to_paragraphs_heading():
    runs = [{'t': 'Title', 'h': None, 'fmt': {'heading': 1}}]
    out = _runs_to_paragraphs(runs)
    assert out == '<h1>Title</h1>'


def test_runs_to_paragraphs_plain_text():
    runs = [{'t': 'Hello world', 'h': None}]
    assert _runs_to_paragraphs(runs) == '<p>Hello world</p>'


def test_runs_to_paragraphs_blank_line_preserved_between_content():
    runs = [
        {'t': 'first', 'h': None},
        {'t': '', 'h': '', 'blank': True},
        {'t': 'second', 'h': None},
    ]
    out = _runs_to_paragraphs(runs)
    assert out == '<p>first</p><p></p><p>second</p>'


def test_runs_to_paragraphs_leading_trailing_blanks_trimmed():
    runs = [
        {'t': '', 'h': '', 'blank': True},
        {'t': 'content', 'h': None},
        {'t': '', 'h': '', 'blank': True},
    ]
    assert _runs_to_paragraphs(runs) == '<p>content</p>'


def test_runs_to_paragraphs_empty_falls_back_to_empty_paragraph():
    assert _runs_to_paragraphs([]) == '<p></p>'


def test_render_list_simple_unordered():
    items = [
        {'ordered': False, 'level': 0, 'html': '<p>one</p>'},
        {'ordered': False, 'level': 0, 'html': '<p>two</p>'},
    ]
    assert _render_list(items) == '<ul><li><p>one</p></li><li><p>two</p></li></ul>'


def test_render_list_nested_level():
    items = [
        {'ordered': False, 'level': 0, 'html': '<p>top</p>'},
        {'ordered': False, 'level': 1, 'html': '<p>nested</p>'},
    ]
    out = _render_list(items)
    assert out == '<ul><li><p>top</p></li><ul><li><p>nested</p></li></ul></ul>'
    assert out.count('<ul>') == out.count('</ul>')


def test_table_html_basic_grid():
    # Cells are already block-wrapped HTML (see _resolve_table_grid) — _table_html
    # uses them as-is, it doesn't add its own <p>.
    grid = [
        [{'html': '<p>a</p>', 'color': None}, {'html': '<p>b</p>', 'color': None}],
        [{'html': '<p>c</p>', 'color': None}, {'html': '<p>d</p>', 'color': None}],
    ]
    out = _table_html(grid)
    assert out == '<table><tr><td><p>a</p></td><td><p>b</p></td></tr><tr><td><p>c</p></td><td><p>d</p></td></tr></table>'


def test_table_html_cell_background_color():
    grid = [[{'html': '<p>a</p>', 'color': '#e2efd9'}]]
    out = _table_html(grid)
    assert out == '<table><tr><td style="background-color:#e2efd9"><p>a</p></td></tr></table>'


def test_table_html_empty_cell_falls_back_to_empty_paragraph():
    grid = [[{'html': '', 'color': None}]]
    out = _table_html(grid)
    assert out == '<table><tr><td><p></p></td></tr></table>'


def test_table_html_colspan_attribute():
    grid = [[{'html': '<p>a</p>', 'color': None, 'colspan': 2}]]
    out = _table_html(grid)
    assert out == '<table><tr><td colspan="2"><p>a</p></td></tr></table>'


def test_table_html_colspan_one_omits_attribute():
    grid = [[{'html': '<p>a</p>', 'color': None, 'colspan': 1}]]
    out = _table_html(grid)
    assert out == '<table><tr><td><p>a</p></td></tr></table>'


def test_table_borders_visible_true():
    prop = {'val': {'TableBordersVisible': 'True'}}
    assert _table_borders_visible(prop) is True


def test_table_borders_visible_false():
    prop = {'val': {'TableBordersVisible': 'False'}}
    assert _table_borders_visible(prop) is False


def test_table_borders_visible_absent_is_none():
    assert _table_borders_visible({'val': {}}) is None
    assert _table_borders_visible(None) is None


def test_table_column_widths_px_decodes_real_shape():
    # Real raw bytes for a 3-column table (count byte + 3 little-endian
    # float32 "half-inch units", the same convention pyOneNote's own
    # `half_inch_size_to_pixels` uses elsewhere) — verified against a real
    # merged-cell table (widths ~123/261/249px).
    import struct
    floats = struct.pack('<3f', 2.5586109161376953, 5.433963298797607, 5.188507556915283)
    raw = bytes([3]) + floats
    prop = {'body': _FakeBody([(0x1C001D66, _FakeBlob(raw))])}
    assert _table_column_widths_px(prop) == [122.8, 260.8, 249.0]


def test_table_column_widths_px_absent_is_none():
    assert _table_column_widths_px({'body': _FakeBody([])}) is None
    assert _table_column_widths_px(None) is None


def test_table_column_widths_px_implausible_count_is_ignored():
    raw = bytes([250]) + b'\x00' * 4  # count byte says 250 columns, no data to back it
    prop = {'body': _FakeBody([(0x1C001D66, _FakeBlob(raw))])}
    assert _table_column_widths_px(prop) is None


def test_table_html_with_visible_borders_and_widths():
    import struct
    raw = bytes([1]) + struct.pack('<f', 1.0)  # 1 column, 48px
    table_prop = {
        'val': {'TableBordersVisible': 'True'},
        'body': _FakeBody([(0x1C001D66, _FakeBlob(raw))]),
    }
    grid = [[{'html': '<p>a</p>', 'color': None, 'colspan': 1}]]
    out = _table_html(grid, table_prop)
    assert out == (
        '<table style="border-collapse:collapse">'
        '<colgroup><col style="width:48.0px"></colgroup>'
        '<tr><td style="border:1px solid #999"><p>a</p></td></tr></table>'
    )


def test_table_html_hidden_borders_gets_no_border_style():
    table_prop = {'val': {'TableBordersVisible': 'False'}}
    grid = [[{'html': '<p>a</p>', 'color': None, 'colspan': 1}]]
    out = _table_html(grid, table_prop)
    assert out == '<table><tr><td><p>a</p></td></tr></table>'


def test_cell_merge_span_absent_property_returns_none():
    prop = {'body': _FakeBody([(0x08001C04, b'\x01')])}  # some unrelated property (Bold)
    assert _cell_merge_span(prop) is None


def test_cell_merge_span_no_body_returns_none():
    assert _cell_merge_span({'val': {}}) is None
    assert _cell_merge_span(None) is None


def test_cell_merge_span_reads_raw_unnamed_property():
    # Real raw PropertyID (0x140035DD) pyOneNote's own name table doesn't
    # know at all — decodes as "Unknown" and is dropped by get_properties(),
    # so this can only be read via the raw `body`, same as ink.py's
    # undocumented ink properties.
    prop = {'body': _FakeBody([(0x140035DD, b'\x02\x00\x00\x00')])}
    assert _cell_merge_span(prop) == 2


def test_cell_merge_span_zero_means_absorbed():
    prop = {'body': _FakeBody([(0x140035DD, b'\x00\x00\x00\x00')])}
    assert _cell_merge_span(prop) == 0


def test_cell_merge_span_implausible_value_is_ignored():
    prop = {'body': _FakeBody([(0x140035DD, (999).to_bytes(4, 'little'))])}
    assert _cell_merge_span(prop) is None


def test_resolve_table_grid_merged_cell_gets_colspan_and_absorbed_cell_is_dropped():
    table_guid = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
    row_guid = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'
    first_guid = 'cccccccc-cccc-cccc-cccc-cccccccccccc'
    merged_guid = 'dddddddd-dddd-dddd-dddd-dddddddddddd'
    absorbed_guid = 'eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee'
    by_id = {
        f'{table_guid},1': {
            'type': 'jcidTableNode',
            'val': {'ElementChildNodesOfVersionHistory': [f'<ExtendedGUID> ({row_guid}, 1)']},
        },
        f'{row_guid},1': {
            'type': 'jcidTableRowNode',
            'val': {'ElementChildNodesOfVersionHistory': [
                f'<ExtendedGUID> ({first_guid}, 1)',
                f'<ExtendedGUID> ({merged_guid}, 1)',
                f'<ExtendedGUID> ({absorbed_guid}, 1)',
            ]},
        },
        f'{first_guid},1': {'type': 'jcidTableCellNode', 'val': {}},
        f'{merged_guid},1': {
            'type': 'jcidTableCellNode', 'val': {},
            'body': _FakeBody([(0x140035DD, b'\x02\x00\x00\x00')]),
        },
        f'{absorbed_guid},1': {
            'type': 'jcidTableCellNode', 'val': {},
            'body': _FakeBody([(0x140035DD, b'\x00\x00\x00\x00')]),
        },
    }
    grid = _resolve_table_grid(by_id[f'{table_guid},1'], by_id, set())
    assert len(grid[0]) == 2  # absorbed cell dropped
    assert grid[0][0]['colspan'] == 1
    assert grid[0][1]['colspan'] == 2


def test_has_visible_content_with_text():
    assert _has_visible_content('<p>hello</p>') is True


def test_has_visible_content_empty_paragraph_is_not_visible():
    assert _has_visible_content('<p></p>') is False


def test_has_visible_content_image_counts_as_visible_even_with_no_text():
    assert _has_visible_content('<img src="x">') is True


def test_image_block_html_escapes_ref():
    out = _image_block_html('onenote-image://1')
    assert out == '<img src="onenote-image://1" alt="">'


def test_placeholder_blocks_shape():
    blocks = _placeholder_blocks('a hand-drawn ink drawing', 'could not be decoded')
    assert len(blocks) == 1
    assert blocks[0]['kind'] == 'text'
    assert 'a hand-drawn ink drawing' in blocks[0]['html']
    assert 'could not be decoded' in blocks[0]['html']
    assert 'x' not in blocks[0] and 'y' not in blocks[0]


def test_resolve_cell_html_non_hyperlink_unicode_text():
    # Real bug, verified against a real file: a table cell whose text needed
    # a character TextExtendedAscii can't hold (a currency symbol) stored it
    # in RichEditTextUnicode with NO hyperlink marker at all, and
    # TextExtendedAscii was genuinely absent (None) — this used to require
    # the hyperlink marker to even look at RichEditTextUnicode, silently
    # dropping the cell's entire content.
    by_id = {
        'cell,1': {'type': 'jcidRichTextOENode', 'val': {'RichEditTextUnicode': '13911€\x00'}},
    }
    consumed = set()
    assert _resolve_cell_html('cell,1', by_id, consumed) == '<p>13911€</p>'
    assert 'cell,1' in consumed


def test_resolve_cell_html_still_handles_real_hyperlinks():
    rtu = '﷟HYPERLINK "https://example.com"Example'
    by_id = {'cell,1': {'type': 'jcidRichTextOENode', 'val': {'RichEditTextUnicode': rtu}}}
    out = _resolve_cell_html('cell,1', by_id, set())
    assert '<a href="https://example.com">Example</a>' in out


def test_resolve_cell_html_applies_formatting_and_alignment():
    # Real bug fixed: cells used to get bare linkified text only — no bold/
    # color/font-size/alignment/list support at all, unlike body paragraphs.
    guid = '33333333-3333-3333-3333-333333333333'
    by_id = {
        f'{guid},1': {
            'type': 'jcidRichTextOENode',
            'val': {
                'TextExtendedAscii': '4d65726765642063656c6c',  # "Merged cell"
                'ParagraphAlignment': b'\x01',
                'TextRunFormatting': [f'<ExtendedGUID> ({guid}, 2)'],
            },
        },
        f'{guid},2': {'val': {'Bold': 'True'}},
    }
    out = _resolve_cell_html(f'{guid},1', by_id, set())
    assert out == '<p style="text-align:center"><strong>Merged cell</strong></p>'


def test_resolve_cell_html_renders_a_list():
    guid = '44444444-4444-4444-4444-444444444444'
    by_id = {f'{guid},1': {'type': 'jcidRichTextOENode', 'val': {'TextExtendedAscii': '4f6e65'}}}  # "One"
    out = _resolve_cell_html(f'{guid},1', by_id, set(), list_runs={f'{guid},1': {'ordered': False, 'level': 0}})
    assert out == '<ul><li><p>One</p></li></ul>'


def _fake_image(ref: str) -> PageImage:
    return PageImage(ref=ref, data=b'x', ext='.png', width=10, height=10)


def test_resolve_cell_html_renders_an_image():
    guid = '55555555-5555-5555-5555-555555555555'
    by_id = {f'{guid},1': {'type': 'jcidImageNode', 'val': {}}}
    images = []
    counter = [0]

    def builder(_nid, _val):
        counter[0] += 1
        return _fake_image(f'onenote-image://{counter[0]}')

    out = _resolve_cell_html(f'{guid},1', by_id, set(), image_builder=builder, images=images)
    assert out == '<img src="onenote-image://1" alt="">'
    assert len(images) == 1 and images[0].ref == 'onenote-image://1'


def test_resolve_table_grid_image_does_not_leak_into_sibling_cell():
    # Real bug, verified against a real 3-cell row: `_resolve_table_grid`
    # passes ONE shared `images` accumulator across every cell in a table
    # (so the caller gets the table's full total) — but a cell with no
    # image of its own used to render whatever image an EARLIER cell in the
    # same table had already added to that shared list, because the
    # rendering loop iterated the shared list instead of just this cell's
    # own finds.
    table_guid = '66666666-6666-6666-6666-666666666666'
    row_guid = '77777777-7777-7777-7777-777777777777'
    img_guid = '88888888-8888-8888-8888-888888888888'
    empty_guid = '99999999-9999-9999-9999-999999999999'
    by_id = {
        f'{table_guid},1': {
            'type': 'jcidTableNode',
            'val': {'ElementChildNodesOfVersionHistory': [f'<ExtendedGUID> ({row_guid}, 1)']},
        },
        f'{row_guid},1': {
            'type': 'jcidTableRowNode',
            'val': {'ElementChildNodesOfVersionHistory': [
                f'<ExtendedGUID> ({img_guid}, 1)', f'<ExtendedGUID> ({empty_guid}, 1)',
            ]},
        },
        f'{img_guid},1': {'type': 'jcidImageNode', 'val': {}},
        f'{empty_guid},1': {'type': 'jcidTableCellNode', 'val': {}},
    }
    counter = [0]

    def builder(_nid, _val):
        counter[0] += 1
        return _fake_image(f'onenote-image://{counter[0]}')

    images = []
    grid = _resolve_table_grid(by_id[f'{table_guid},1'], by_id, set(), image_builder=builder, images=images)
    assert len(images) == 1
    assert '<img' in grid[0][0]['html']
    assert '<img' not in grid[0][1]['html']
