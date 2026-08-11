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
