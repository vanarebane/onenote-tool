from onenote_tool.rendering import (
    _apply_marks,
    _has_visible_content,
    _image_block_html,
    _linkify,
    _placeholder_blocks,
    _render_list,
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
