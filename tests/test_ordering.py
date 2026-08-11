from onenote_tool.ordering import _int, _page_regions


def _ident(guid: str, rev: int) -> list:
    return [f'<ExtendedGUID> ({guid}, {rev})']


def test_int_coerces_plain_values():
    assert _int(48) == 48
    assert _int('48') == 48
    assert _int(None) == 0
    assert _int('not a number', default=7) == 7


PAGE_GUID = '11111111-1111-1111-1111-111111111111'
OUTLINE1_GUID = '22222222-2222-2222-2222-222222222222'
OUTLINE2_GUID = '33333333-3333-3333-3333-333333333333'
RUN1_GUID = '44444444-4444-4444-4444-444444444444'
RUN2_GUID = '55555555-5555-5555-5555-555555555555'


def _synthetic_two_region_page():
    props = [
        {
            'type': 'jcidPageManifestNode',
            'identity': _ident(PAGE_GUID, 10),
            'val': {'ContentChildNodesOfPageManifest': _ident(PAGE_GUID, 20)},
        },
        {
            'type': 'jcidPageNode',
            'identity': _ident(PAGE_GUID, 20),
            'val': {'ElementChildNodesOfVersionHistory': [
                *_ident(OUTLINE1_GUID, 30), *_ident(OUTLINE2_GUID, 40),
            ]},
        },
        {
            'type': 'jcidOutlineNode',
            'identity': _ident(OUTLINE1_GUID, 30),
            'val': {
                'OffsetFromParentHoriz': 10, 'OffsetFromParentVert': 20,
                'LayoutMaxWidth': 100, 'LayoutMaxHeight': 50,
                'ElementChildNodesOfVersionHistory': _ident(RUN1_GUID, 31),
            },
        },
        {'type': 'jcidRichTextOENode', 'identity': _ident(RUN1_GUID, 31), 'val': {}},
        {
            'type': 'jcidOutlineNode',
            'identity': _ident(OUTLINE2_GUID, 40),
            'val': {
                'OffsetFromParentHoriz': 200, 'OffsetFromParentVert': 20,
                'LayoutMaxWidth': 100, 'LayoutMaxHeight': 50,
                'ElementChildNodesOfVersionHistory': _ident(RUN2_GUID, 41),
            },
        },
        {'type': 'jcidRichTextOENode', 'identity': _ident(RUN2_GUID, 41), 'val': {}},
    ]
    by_id = {}
    for p in props:
        by_id[f"{p['identity'][0].split('(')[1].split(')')[0].replace(' ', '')}"] = p
    return props, by_id


def test_page_regions_two_floating_boxes():
    props, by_id = _synthetic_two_region_page()
    regions_by_page, content_region = _page_regions(props, by_id)

    regions = regions_by_page[PAGE_GUID]
    assert len(regions) == 2
    assert regions[0]['guid'] == OUTLINE1_GUID
    assert regions[0]['x'] == 10 and regions[0]['y'] == 20
    assert regions[0]['width'] == 100 and regions[0]['height'] == 50
    assert regions[1]['guid'] == OUTLINE2_GUID
    assert regions[1]['x'] == 200

    assert content_region[f'{RUN1_GUID},31'] == OUTLINE1_GUID
    assert content_region[f'{RUN2_GUID},41'] == OUTLINE2_GUID


def test_page_regions_no_manifest_returns_empty():
    regions_by_page, content_region = _page_regions([], {})
    assert regions_by_page == {}
    assert content_region == {}
