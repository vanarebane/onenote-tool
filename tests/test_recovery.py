from onenote_tool.recovery import _pages_from_structural_groups


class _FakeDocument:
    def __init__(self, files=None):
        self._files = files or {}

    def get_files(self):
        return self._files


def _hex(text: str) -> str:
    return text.encode('utf-8').hex()


PAGE_GUID = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
LIST_ITEM_GUID = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'
PLAIN_RUN_GUID = 'cccccccc-cccc-cccc-cccc-cccccccccccc'
NUMBER_LIST_GUID = 'dddddddd-dddd-dddd-dddd-dddddddddddd'
OUTLINE_ELEMENT_GUID = 'eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee'
TABLE_GUID = 'ffffffff-ffff-ffff-ffff-ffffffffffff'
ROW_GUID = '11111111-2222-3333-4444-555555555555'
CELL_GUID = '66666666-7777-8888-9999-aaaaaaaaaaaa'
YT_RUN_GUID = '22222222-3333-4444-5555-666666666666'


def _ident(guid: str, rev: int) -> list:
    return [f'<ExtendedGUID> ({guid}, {rev})']


def test_pages_from_structural_groups_recovers_a_list_item():
    by_id = {
        f'{PAGE_GUID},1': {'type': 'jcidPageMetaData', 'val': {'CachedTitleString': 'MyPage'}},
        f'{LIST_ITEM_GUID},1': {'type': 'jcidRichTextOENode', 'val': {'TextExtendedAscii': _hex('Item A')}},
        f'{PLAIN_RUN_GUID},1': {'type': 'jcidRichTextOENode', 'val': {'TextExtendedAscii': _hex('Plain para')}},
        f'{NUMBER_LIST_GUID},1': {'type': 'jcidNumberListNode', 'val': {'NumberListFormat': '1.'}},
        f'{OUTLINE_ELEMENT_GUID},1': {
            'type': 'jcidOutlineElementNode',
            'val': {
                'ListNodes': _ident(NUMBER_LIST_GUID, 1),
                'OutlineElementChildLevel': 1,
                'ElementChildNodesOfVersionHistory': _ident(LIST_ITEM_GUID, 1),
            },
        },
    }
    groups = [{
        'title_identity': f'{PAGE_GUID},1',
        'text_identities': [f'{LIST_ITEM_GUID},1', f'{PLAIN_RUN_GUID},1'],
        'file_guids': [], 'ink_identities': [], 'table_identities': [],
    }]

    pages = _pages_from_structural_groups(_FakeDocument(), by_id, groups)
    assert len(pages) == 1
    page = pages[0]
    assert page.title == 'MyPage'
    text_block = next(b for b in page.blocks if b['kind'] == 'text')
    assert '<ol><li><p>Item A</p></li></ol>' in text_block['html']
    assert '<p>Plain para</p>' in text_block['html']


def test_pages_from_structural_groups_recovers_a_table():
    by_id = {
        f'{PAGE_GUID},1': {'type': 'jcidPageMetaData', 'val': {'CachedTitleString': 'TablePage'}},
        f'{TABLE_GUID},1': {
            'type': 'jcidTableNode',
            'val': {'ElementChildNodesOfVersionHistory': _ident(ROW_GUID, 1)},
        },
        f'{ROW_GUID},1': {
            'type': 'jcidTableRowNode',
            'val': {'ElementChildNodesOfVersionHistory': _ident(CELL_GUID, 1)},
        },
        f'{CELL_GUID},1': {'type': 'jcidRichTextOENode', 'val': {'TextExtendedAscii': _hex('A1')}},
    }
    groups = [{
        'title_identity': f'{PAGE_GUID},1',
        'text_identities': [f'{CELL_GUID},1'], 'file_guids': [], 'ink_identities': [],
        'table_identities': [f'{TABLE_GUID},1'],
    }]

    pages = _pages_from_structural_groups(_FakeDocument(), by_id, groups)
    assert len(pages) == 1
    page = pages[0]
    table_block = next(b for b in page.blocks if b['kind'] == 'table')
    assert '<table><tr><td><p>A1</p></td></tr></table>' == table_block['html']
    # The cell's own text must not ALSO show up as a loose body paragraph.
    assert not any(b['kind'] == 'text' and 'A1' in b['html'] for b in page.blocks)


def test_pages_from_structural_groups_empty_table_body_is_silently_skipped():
    # A jcidTableNode whose PropertySet body didn't decode (val == {}) — the
    # real-file ceiling this was verified against (see known-limitations.md) —
    # must not crash or produce a broken block, just contribute nothing.
    by_id = {
        f'{PAGE_GUID},1': {'type': 'jcidPageMetaData', 'val': {'CachedTitleString': 'EmptyTablePage'}},
        f'{TABLE_GUID},1': {'type': 'jcidTableNode', 'val': {}},
        f'{PLAIN_RUN_GUID},1': {'type': 'jcidRichTextOENode', 'val': {'TextExtendedAscii': _hex('Still here')}},
    }
    groups = [{
        'title_identity': f'{PAGE_GUID},1',
        'text_identities': [f'{PLAIN_RUN_GUID},1'], 'file_guids': [], 'ink_identities': [],
        'table_identities': [f'{TABLE_GUID},1'],
    }]

    pages = _pages_from_structural_groups(_FakeDocument(), by_id, groups)
    assert len(pages) == 1
    page = pages[0]
    assert not any(b['kind'] == 'table' for b in page.blocks)
    assert any(b['kind'] == 'text' and 'Still here' in b['html'] for b in page.blocks)


def test_pages_from_structural_groups_reads_sub_level():
    # PageLevel is a 4-byte little-endian int, same packed shape pyOneNote
    # hands back for FontSize etc. (see rendering._font_size_pt) — real
    # value observed for a real sub-page: b'\x02\x00\x00\x00'.
    by_id = {
        f'{PAGE_GUID},1': {
            'type': 'jcidPageMetaData',
            'val': {'CachedTitleString': 'SubPage', 'PageLevel': b'\x02\x00\x00\x00'},
        },
        f'{PLAIN_RUN_GUID},1': {'type': 'jcidRichTextOENode', 'val': {'TextExtendedAscii': _hex('Body')}},
    }
    groups = [{
        'title_identity': f'{PAGE_GUID},1',
        'text_identities': [f'{PLAIN_RUN_GUID},1'], 'file_guids': [], 'ink_identities': [],
        'table_identities': [],
    }]

    pages = _pages_from_structural_groups(_FakeDocument(), by_id, groups)
    assert pages[0].sub_level == 1  # PageLevel 2 (sub-page) -> sub_level 1


def test_pages_from_structural_groups_garbage_page_level_falls_back_to_zero():
    # Verified on a real protected file: a misaligned decrypt can land
    # PageLevel on unrelated ciphertext and decode to a huge garbage int
    # (observed: 3334854043) rather than failing cleanly — must clamp, not
    # propagate a nonsensical sub_level.
    by_id = {
        f'{PAGE_GUID},1': {
            'type': 'jcidPageMetaData',
            'val': {'CachedTitleString': 'GarbageLevelPage', 'PageLevel': (3334854043).to_bytes(4, 'little')},
        },
        f'{PLAIN_RUN_GUID},1': {'type': 'jcidRichTextOENode', 'val': {'TextExtendedAscii': _hex('Body')}},
    }
    groups = [{
        'title_identity': f'{PAGE_GUID},1',
        'text_identities': [f'{PLAIN_RUN_GUID},1'], 'file_guids': [], 'ink_identities': [],
        'table_identities': [],
    }]

    pages = _pages_from_structural_groups(_FakeDocument(), by_id, groups)
    assert pages[0].sub_level == 0


def test_pages_from_structural_groups_recovers_a_youtube_embed():
    # RichEditTextUnicode shape for a standalone YouTube link: the RTU
    # marker followed by a single HYPERLINK field, no leading plain text
    # (see rendering._RTU_MARKER / images._youtube_from_rtu).
    rtu = '﷟HYPERLINK "https://www.youtube.com/watch?v=dQw4w9WgXcQ"Never Gonna Give You Up'
    by_id = {
        f'{PAGE_GUID},1': {'type': 'jcidPageMetaData', 'val': {'CachedTitleString': 'YouTubePage'}},
        f'{YT_RUN_GUID},1': {'type': 'jcidRichTextOENode', 'val': {'RichEditTextUnicode': rtu}},
    }
    groups = [{
        'title_identity': f'{PAGE_GUID},1',
        'text_identities': [f'{YT_RUN_GUID},1'], 'file_guids': [], 'ink_identities': [],
        'table_identities': [],
    }]

    pages = _pages_from_structural_groups(_FakeDocument(), by_id, groups)
    assert len(pages) == 1
    text_block = next(b for b in pages[0].blocks if b['kind'] == 'text')
    assert 'onenote-youtube-embed' in text_block['html']
    assert 'data-youtube-id="dQw4w9WgXcQ"' in text_block['html']
    assert 'Never Gonna Give You Up' in text_block['html']


def test_pages_from_structural_groups_recovers_non_hyperlink_unicode_text():
    # Same real bug as rendering._resolve_cell_html's identical fix: a run's
    # text needing a character TextExtendedAscii can't hold (a currency
    # symbol) stored it in RichEditTextUnicode with no hyperlink marker at
    # all — used to require the marker to even look there.
    by_id = {
        f'{PAGE_GUID},1': {'type': 'jcidPageMetaData', 'val': {'CachedTitleString': 'CurrencyPage'}},
        f'{PLAIN_RUN_GUID},1': {'type': 'jcidRichTextOENode', 'val': {'RichEditTextUnicode': '13911€\x00'}},
    }
    groups = [{
        'title_identity': f'{PAGE_GUID},1',
        'text_identities': [f'{PLAIN_RUN_GUID},1'], 'file_guids': [], 'ink_identities': [],
        'table_identities': [],
    }]

    pages = _pages_from_structural_groups(_FakeDocument(), by_id, groups)
    assert len(pages) == 1
    text_block = next(b for b in pages[0].blocks if b['kind'] == 'text')
    assert '13911€' in text_block['html']
