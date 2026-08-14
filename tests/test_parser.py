from onenote_tool.models import PageFile, PageImage
from onenote_tool.parser import _build_blocks, _page_style, _run_is_equation


class _FakePrid:
    def __init__(self, value):
        self.value = value


class _FakeBody:
    """Stands in for pyOneNote's real PropertySet body — see the same fake
    in test_rendering.py for why (`_page_style` reads raw, unnamed
    PropertyIDs off `prop['body']`, same escape hatch as the merge-span/
    table-column-widths properties there)."""

    def __init__(self, pairs):
        self.rgPrids = [_FakePrid(k) for k, _ in pairs]
        self.rgData = [v for _, v in pairs]


def test_page_style_reads_color_and_rule_lines():
    prop = {'body': _FakeBody([
        (0x14001D2A, b'\xf0\xf8\xfa\x00'),
        (0x0C00352D, b'\x01'),
    ])}
    assert _page_style(prop) == ('#f0f8fa', 'narrow_lines')


def test_page_style_all_eight_rule_line_patterns():
    # Verified against 13 real pages, one per built-in pattern — the raw
    # enum matched each page's own title exactly.
    names = ['narrow_lines', 'college_ruled', 'standard_rules', 'wide_ruled',
             'small_grid', 'medium_grid', 'large_grid', 'very_large_grid']
    for i, name in enumerate(names, start=1):
        prop = {'body': _FakeBody([(0x0C00352D, bytes([i]))])}
        assert _page_style(prop) == (None, name)


def test_page_style_absent_properties_is_none_none():
    assert _page_style({'body': _FakeBody([])}) == (None, None)
    assert _page_style(None) == (None, None)
    assert _page_style({}) == (None, None)


def test_run_is_equation_true_when_math_formatting_flag_set():
    guid = '11111111-1111-1111-1111-111111111111'
    val = {'TextRunFormatting': [f'<ExtendedGUID> ({guid}, 2)']}
    by_id = {f'{guid},2': {'val': {'MathFormatting': 'True', 'Font': 'Cambria Math\x00'}}}
    assert _run_is_equation(val, by_id) is True


def test_run_is_equation_false_for_ordinary_formatting():
    guid = '22222222-2222-2222-2222-222222222222'
    val = {'TextRunFormatting': [f'<ExtendedGUID> ({guid}, 2)']}
    by_id = {f'{guid},2': {'val': {'Bold': 'True'}}}
    assert _run_is_equation(val, by_id) is False


def test_run_is_equation_false_with_no_formatting_ref():
    assert _run_is_equation({}, {}) is False


def test_build_blocks_interleaves_positioned_image_between_text_runs():
    # Real bug, verified against a real page: an image sitting between two
    # paragraphs used to always import trailing after ALL the page's text
    # instead of its own row in place. order_index/identity_by_ref mirror
    # what ordering._page_content_order + parser.py's own_identity tracking
    # actually produce — see _build_blocks' docstring.
    runs = [
        {'t': 'first paragraph', 'h': None, 'rid': 'r1'},
        {'t': 'second paragraph', 'h': None, 'rid': 'r2'},
    ]
    image = PageImage(ref='img-ref-1', data=b'', ext='.png', width=10, height=10)
    order_index = {'r1': 0, 'img-identity-1': 1, 'r2': 2}
    identity_by_ref = {'img-ref-1': 'img-identity-1'}

    blocks = _build_blocks(
        runs, [], [image], [], [], {}, set(),
        order_index=order_index, identity_by_ref=identity_by_ref,
    )

    assert [b['kind'] for b in blocks] == ['text', 'image', 'text']
    assert 'first paragraph' in blocks[0]['html']
    assert blocks[1]['image_src'] == 'img-ref-1'
    assert 'second paragraph' in blocks[2]['html']


def test_build_blocks_image_with_unresolved_order_falls_back_to_trailing():
    runs = [{'t': 'only paragraph', 'h': None, 'rid': 'r1'}]
    image = PageImage(ref='img-ref-1', data=b'', ext='.png', width=10, height=10)

    blocks = _build_blocks(runs, [], [image], [], [], {}, set())

    assert [b['kind'] for b in blocks] == ['text', 'image']


def test_build_blocks_skips_thumb_and_file_icon_refs_from_interleaving():
    # A YouTube thumbnail / a file's own icon must never show up as its own
    # standalone (interleaved or trailing) image block either.
    runs = [{'t': 'paragraph', 'h': None, 'rid': 'r1'}]
    thumb = PageImage(ref='thumb-ref', data=b'', ext='.jpg', width=10, height=10)
    icon = PageImage(ref='icon-ref', data=b'', ext='.png', width=10, height=10)
    file = PageFile(ref='file-ref', data=b'x', filename='report.pdf', icon_ref='icon-ref')
    order_index = {'r1': 0, 'thumb-identity': 1, 'icon-identity': 2}
    identity_by_ref = {'thumb-ref': 'thumb-identity', 'icon-ref': 'icon-identity'}

    blocks = _build_blocks(
        runs, [], [thumb, icon], [], [file], {}, {'thumb-ref'},
        order_index=order_index, identity_by_ref=identity_by_ref,
    )

    kinds = [b['kind'] for b in blocks]
    assert 'image' not in kinds
    assert kinds == ['text', 'file_links']


def test_build_blocks_interleaves_positioned_table_after_intro_text():
    # Real bug: a table used to always render before ALL page text
    # regardless of position — a "Causes:" label introducing a table used to
    # import with the table ABOVE it instead of below.
    runs = [
        {'t': 'Causes:', 'h': None, 'rid': 'r1'},
        {'t': 'See below for details', 'h': None, 'rid': 'r2'},
    ]
    grid = [[{'html': '<p>cell</p>'}]]
    order_index = {'r1': 0, 'table-1': 1, 'r2': 2}

    blocks = _build_blocks(runs, [('table-1', grid)], [], [], [], {}, set(), order_index=order_index)

    assert [b['kind'] for b in blocks] == ['text', 'table', 'text']
    assert 'Causes' in blocks[0]['html']
    assert 'See below' in blocks[2]['html']


def test_build_blocks_table_with_unresolved_order_falls_back_to_original_placement():
    runs = [{'t': 'only paragraph', 'h': None, 'rid': 'r1'}]
    grid = [[{'html': '<p>cell</p>'}]]

    blocks = _build_blocks(runs, [('table-1', grid)], [], [], [], {}, set())

    assert [b['kind'] for b in blocks] == ['table', 'text']


def test_build_blocks_skips_cell_image_refs_from_standalone_rendering():
    # Real bug, verified against a real table ("Ape image" cell): a picture
    # pasted directly into a table cell rendered correctly inline in the
    # cell's own HTML AND, separately, as its own standalone 'image' block —
    # grp['images'] legitimately keeps including cell images (page.images'
    # own total relies on it), so _build_blocks needs its own skip, same
    # shape as used_thumb_refs/used_file_icon_refs.
    runs = [{'t': 'paragraph', 'h': None, 'rid': 'r1'}]
    cell_image = PageImage(ref='cell-img-ref', data=b'', ext='.png', width=10, height=10)
    grid = [[{'html': '<img src="onenote-image://cell-img-ref">'}]]

    blocks = _build_blocks(
        runs, [('table-1', grid)], [cell_image], [], [], {}, set(),
        used_cell_image_refs={'cell-img-ref'},
    )

    kinds = [b['kind'] for b in blocks]
    assert 'image' not in kinds
    assert kinds == ['table', 'text']


def test_build_blocks_audio_file_icon_still_renders_as_standalone_image():
    # Real regression fixed: an audio/video attachment's own preview picture
    # was being suppressed the same way a 'file' kind's icon is (shown
    # inside its embed card) — but _media_block_html (the audio/video
    # player) never renders that icon anywhere, so the picture just vanished
    # off the page entirely.
    icon = PageImage(ref='icon-ref', data=b'', ext='.png', width=10, height=10)
    audio_file = PageFile(ref='audio-ref', data=b'x', filename='clip.mp3', icon_ref='icon-ref')

    blocks = _build_blocks([], [], [icon], [], [audio_file], {}, set())

    kinds = [b['kind'] for b in blocks]
    assert kinds == ['image', 'audio']
