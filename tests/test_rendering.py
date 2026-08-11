from onenote_tool.rendering import (
    _apply_marks,
    _font_size_pt,
    _has_visible_content,
    _image_block_html,
    _linkify,
    _media_block_html,
    _placeholder_blocks,
    _render_list,
    _resolve_run_format,
    _runs_to_paragraphs,
    _table_html,
)


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


def test_resolve_run_format_font_size_only_from_trf_override():
    # OneNote only ever writes FontSize onto TextRunFormatting when a run's
    # size differs from the paragraph's own default (ParagraphStyle) — that
    # default itself should NOT surface as a per-run override (see
    # rendering._resolve_run_format's docstring/comment).
    guid = '11111111-1111-1111-1111-111111111111'
    ps_ref = [f'<ExtendedGUID> ({guid}, 1)']
    trf_ref = [f'<ExtendedGUID> ({guid}, 2)']
    by_id = {
        f'{guid},1': {'val': {'FontSize': b'\x16\x00'}},  # paragraph default, 11pt
        f'{guid},2': {'val': {'FontSize': b'\x18\x00'}},  # explicit override, 12pt
    }
    fmt = _resolve_run_format(ps_ref, trf_ref, by_id)
    assert fmt['font_size'] == 12


def test_resolve_run_format_no_trf_override_leaves_font_size_unset():
    guid = '22222222-2222-2222-2222-222222222222'
    ps_ref = [f'<ExtendedGUID> ({guid}, 1)']
    trf_ref = [f'<ExtendedGUID> ({guid}, 2)']
    by_id = {
        f'{guid},1': {'val': {'FontSize': b'\x16\x00', 'Bold': 'True'}},
        f'{guid},2': {'val': {}},
    }
    fmt = _resolve_run_format(ps_ref, trf_ref, by_id)
    assert fmt['font_size'] is None
    assert fmt['font_family'] is None
    assert fmt['bold'] is True


def test_media_block_html_audio_and_video():
    assert _media_block_html('audio', 'onenote-file://1') == '<audio controls src="onenote-file://1"></audio>'
    assert _media_block_html('video', 'onenote-file://2') == '<video controls src="onenote-file://2"></video>'


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
    grid = [['a', 'b'], ['c', 'd']]
    out = _table_html(grid)
    assert out == '<table><tr><td><p>a</p></td><td><p>b</p></td></tr><tr><td><p>c</p></td><td><p>d</p></td></tr></table>'


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
