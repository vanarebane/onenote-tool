from onenote_tool.ordering import _int, _page_content_order, _page_regions


def _ident(guid: str, rev: int) -> list:
    return [f'<ExtendedGUID> ({guid}, {rev})']


def test_int_coerces_plain_values():
    assert _int(48) == 48
    assert _int('48') == 48
    assert _int(None) == 0
    assert _int('not a number', default=7) == 7


def test_page_content_order_includes_image_between_runs():
    # Real bug this enables fixing (parser._build_blocks): an image node used
    # to be walked over (to reach further children) but never itself given a
    # position, so a caller had no way to tell it sat between these two runs
    # rather than after both.
    page_guid = '66666666-6666-6666-6666-666666666666'
    run1_guid = '77777777-7777-7777-7777-777777777777'
    image_guid = '88888888-8888-8888-8888-888888888888'
    run2_guid = '99999999-9999-9999-9999-999999999999'
    props = [
        {
            'type': 'jcidPageManifestNode',
            'identity': _ident(page_guid, 10),
            'val': {'ContentChildNodesOfPageManifest': _ident(page_guid, 20)},
        },
        {
            'type': 'jcidPageNode',
            'identity': _ident(page_guid, 20),
            'val': {'ElementChildNodesOfVersionHistory': [
                *_ident(run1_guid, 30), *_ident(image_guid, 31), *_ident(run2_guid, 32),
            ]},
        },
        {'type': 'jcidRichTextOENode', 'identity': _ident(run1_guid, 30), 'val': {}},
        {'type': 'jcidImageNode', 'identity': _ident(image_guid, 31), 'val': {}},
        {'type': 'jcidRichTextOENode', 'identity': _ident(run2_guid, 32), 'val': {}},
    ]
    by_id = {}
    for p in props:
        by_id[f"{p['identity'][0].split('(')[1].split(')')[0].replace(' ', '')}"] = p

    order, title_ids = _page_content_order(props, by_id)

    assert order[f'{run1_guid},30'] == 0
    assert order[f'{image_guid},31'] == 1
    assert order[f'{run2_guid},32'] == 2
    assert title_ids == set()


def test_page_content_order_includes_table_after_intro_run_without_descending_into_it():
    # Real bug this enables fixing: a table used to be walked over the same
    # way (no position recorded) — always rendering before ALL page text.
    # Its own cell/row children must NOT get their own top-level slot too
    # (they're consumed via the table's own separate tree walk).
    page_guid = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
    run1_guid = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'
    table_guid = 'cccccccc-cccc-cccc-cccc-cccccccccccc'
    row_guid = 'dddddddd-dddd-dddd-dddd-dddddddddddd'
    run2_guid = 'eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee'
    props = [
        {
            'type': 'jcidPageManifestNode',
            'identity': _ident(page_guid, 10),
            'val': {'ContentChildNodesOfPageManifest': _ident(page_guid, 20)},
        },
        {
            'type': 'jcidPageNode',
            'identity': _ident(page_guid, 20),
            'val': {'ElementChildNodesOfVersionHistory': [
                *_ident(run1_guid, 30), *_ident(table_guid, 31), *_ident(run2_guid, 32),
            ]},
        },
        {'type': 'jcidRichTextOENode', 'identity': _ident(run1_guid, 30), 'val': {}},
        {
            'type': 'jcidTableNode',
            'identity': _ident(table_guid, 31),
            'val': {'ElementChildNodesOfVersionHistory': _ident(row_guid, 40)},
        },
        {'type': 'jcidTableRowNode', 'identity': _ident(row_guid, 40), 'val': {}},
        {'type': 'jcidRichTextOENode', 'identity': _ident(run2_guid, 32), 'val': {}},
    ]
    by_id = {}
    for p in props:
        by_id[f"{p['identity'][0].split('(')[1].split(')')[0].replace(' ', '')}"] = p

    order, title_ids = _page_content_order(props, by_id)

    assert order[f'{run1_guid},30'] == 0
    assert order[f'{table_guid},31'] == 1
    assert order[f'{run2_guid},32'] == 2
    assert f'{row_guid},40' not in order
    assert title_ids == set()


def test_page_content_order_resets_seen_per_page_manifest_root():
    # Real bug, verified via this exact repro before fixing: `seen`/`order`
    # were shared across EVERY jcidPageManifestNode root. A node reachable
    # (via a stale/duplicate reference) from an unrelated STALE page's
    # TitleNode got marked seen there — a context that never assigns
    # `order` at all. Its real, later visit as body content on its own
    # actual page then hit `nid in seen` and returned immediately,
    # permanently missing from `order` — landing on `_build_blocks`'s
    # `10**9` trailing fallback. Symptom reported against a real notebook:
    # a header rendering after a list that structurally follows it.
    stale_page, real_page = '11111111-1111-1111-1111-111111111111', '22222222-2222-2222-2222-222222222222'
    stale_title = '33333333-3333-3333-3333-333333333333'
    shared_header = '44444444-4444-4444-4444-444444444444'
    list_item = '55555555-5555-5555-5555-555555555555'
    props = [
        {
            'type': 'jcidPageManifestNode',
            'identity': _ident(stale_page, 10),
            'val': {'ContentChildNodesOfPageManifest': _ident(stale_page, 20)},
        },
        {
            'type': 'jcidPageNode',
            'identity': _ident(stale_page, 20),
            'val': {'StructureElementChildNodes': _ident(stale_title, 21)},
        },
        {
            'type': 'jcidTitleNode',
            'identity': _ident(stale_title, 21),
            'val': {'ElementChildNodesOfVersionHistory': _ident(shared_header, 30)},
        },
        {
            'type': 'jcidPageManifestNode',
            'identity': _ident(real_page, 10),
            'val': {'ContentChildNodesOfPageManifest': _ident(real_page, 20)},
        },
        {
            'type': 'jcidPageNode',
            'identity': _ident(real_page, 20),
            'val': {'ElementChildNodesOfVersionHistory': [
                *_ident(shared_header, 30), *_ident(list_item, 31),
            ]},
        },
        {'type': 'jcidRichTextOENode', 'identity': _ident(shared_header, 30), 'val': {}},
        {'type': 'jcidRichTextOENode', 'identity': _ident(list_item, 31), 'val': {}},
    ]
    by_id = {}
    for p in props:
        by_id[f"{p['identity'][0].split('(')[1].split(')')[0].replace(' ', '')}"] = p

    order, title_ids = _page_content_order(props, by_id)

    assert f'{shared_header},30' in order
    assert order[f'{shared_header},30'] < order[f'{list_item},31']


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
    assert regions[0]['guid'] == f'{OUTLINE1_GUID},30'
    assert regions[0]['x'] == 10 and regions[0]['y'] == 20
    assert regions[0]['width'] == 100 and regions[0]['height'] == 50
    assert regions[1]['guid'] == f'{OUTLINE2_GUID},40'
    assert regions[1]['x'] == 200

    assert content_region[f'{RUN1_GUID},31'] == f'{OUTLINE1_GUID},30'
    assert content_region[f'{RUN2_GUID},41'] == f'{OUTLINE2_GUID},40'


def test_page_regions_no_manifest_returns_empty():
    regions_by_page, content_region = _page_regions([], {})
    assert regions_by_page == {}
    assert content_region == {}


def test_page_regions_outlines_sharing_the_page_base_guid_stay_distinct():
    # Real bug, verified against a real 6-box page: every object inside one
    # page's object-space (title, outlines, runs, ...) normally shares that
    # SAME base guid, differing only by revision counter — using base-guid-
    # only as the region key collapsed all 6 outline boxes onto one
    # identical key, so every region rendered the SAME merged content
    # instead of its own. This synthetic case reuses PAGE_GUID as BOTH
    # outlines' own base guid (the realistic shape) to guard against that
    # regressing.
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
                *_ident(PAGE_GUID, 30), *_ident(PAGE_GUID, 40),
            ]},
        },
        {
            'type': 'jcidOutlineNode',
            'identity': _ident(PAGE_GUID, 30),
            'val': {
                'OffsetFromParentHoriz': 10, 'OffsetFromParentVert': 20,
                'ElementChildNodesOfVersionHistory': _ident(PAGE_GUID, 31),
            },
        },
        {'type': 'jcidRichTextOENode', 'identity': _ident(PAGE_GUID, 31), 'val': {}},
        {
            'type': 'jcidOutlineNode',
            'identity': _ident(PAGE_GUID, 40),
            'val': {
                'OffsetFromParentHoriz': 200, 'OffsetFromParentVert': 20,
                'ElementChildNodesOfVersionHistory': _ident(PAGE_GUID, 41),
            },
        },
        {'type': 'jcidRichTextOENode', 'identity': _ident(PAGE_GUID, 41), 'val': {}},
    ]
    by_id = {}
    for p in props:
        by_id[f"{p['identity'][0].split('(')[1].split(')')[0].replace(' ', '')}"] = p

    regions_by_page, content_region = _page_regions(props, by_id)
    regions = regions_by_page[PAGE_GUID]
    assert len(regions) == 2
    assert regions[0]['guid'] != regions[1]['guid']
    assert content_region[f'{PAGE_GUID},31'] == regions[0]['guid']
    assert content_region[f'{PAGE_GUID},41'] == regions[1]['guid']
