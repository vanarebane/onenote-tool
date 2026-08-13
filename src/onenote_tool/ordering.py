"""Recover visual/page order from pyOneNote's storage-order-only object tree."""
from __future__ import annotations

import re

from .text import _norm_identity

# Tiny local copies (not imported from `parser`) to avoid a circular import —
# parser.py imports THIS module's functions; same precedent as `ink.py`'s own
# duplicated `_color_from_bytes`.
_TITLE_TYPES = {'jcidTitleNode'}
_TABLE_TYPES = {'jcidTableNode'}
_TABLE_PART_TYPES = {'jcidTableRowNode', 'jcidTableCellNode'}
_IMAGE_TYPES = {'jcidImageNode'}
_EMBED_TYPES = {'jcidEmbeddedFileNode'}
_INK_CONTAINER_JCID = 0x00060014  # onenote_ink.JCID_INK_CONTAINER, duplicated to dodge the same import cycle


def _int(value, default: int = 0) -> int:
    """`OffsetFromParentHoriz`/`LayoutMaxWidth`/etc. come back from pyOneNote
    as plain ints already (unlike e.g. `FontSize`, packed as little-endian
    bytes — see `rendering._font_size_pt` — `_le_int` is for that shape, not
    this one)."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default

# Ordered child-reference lists, walked to reconstruct the SECTION's page order
# (_object_space_order) — deliberately a wider key set than _CONTENT_CHILD_KEYS
# below, since a section/page-series node's own children use different keys
# than a page manifest's content tree does.
_ORDER_CHILD_KEYS = (
    'ContentChildNodesOfPageManifest', 'ElementChildNodesOfVersionHistory',
    'StructureElementChildNodes', 'OutlineElementChildNodes', 'ContentChildNodes',
)

# Child-reference keys walked for a PAGE's own content order/title detection
# (_page_content_order). Deliberately narrower than _ORDER_CHILD_KEYS above —
# including 'OutlineElementChildNodes'/'ContentChildNodes' here was tried and
# reverted: it let the walk cross into a neighboring page's subtree (a stale
# revision-store reference, see onenote-file-format.md's duplication section)
# and mis-claim a run that legitimately belonged to its own, separate page —
# confirmed against a real file, where it silently dropped a whole page.
_CONTENT_CHILD_KEYS = (
    'ElementChildNodesOfVersionHistory', 'ContentChildNodesOfPageManifest', 'StructureElementChildNodes',
)


def _ordered_child_ids(prop) -> list:
    out = []
    val = prop.get('val', {}) if prop else {}
    for key in _ORDER_CHILD_KEYS:
        refs = val.get(key)
        if isinstance(refs, list):
            out.extend(_norm_identity(r) for r in refs)
        elif refs:
            out.append(_norm_identity(refs))
    return [x for x in out if x]


def _object_space_order(document, props, by_id):
    """Recover the section's page order. OneNote orders pages via the section's
    page-series list, but pyOneNote flattens object spaces so the page runs (keyed
    by their object OID guid) don't directly reference it. Bridge the two:
      1. walk the file-node tree tracking the current object-space gosid, mapping
         each object's OID guid -> its space gosid;
      2. read jcidSectionNode -> ordered jcidPageSeriesNode -> ordered
         ChildGraphSpaceElementNodes (the page gosids in visual order).
    Returns {oid_guid: order_index} for sorting the page groups.
    """
    def gid(x):
        mm = re.search(r'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})', str(x))
        return mm.group(1) if mm else None

    oid_to_gosid = {}

    def walk(fnl, cur_gosid):
        for frag in getattr(fnl, 'fragments', []):
            for fn in getattr(frag, 'fileNodes', []):
                data = getattr(fn, 'data', None)
                gosid = cur_gosid
                space = gid(getattr(data, 'gosid', None)) if data is not None else None
                if space:
                    gosid = space
                inner = getattr(data, 'base', data)
                body = getattr(inner, 'body', None)
                oid = getattr(body, 'oid', None)
                if oid is not None:
                    og = gid(oid)
                    if og and og not in oid_to_gosid:
                        oid_to_gosid[og] = cur_gosid
                for child in getattr(fn, 'children', []):
                    walk(child, gosid)

    try:
        walk(document.root_file_node_list, None)
    except Exception:
        return {}

    gosid_order = {}
    for p in props:
        if p.get('type', '') != 'jcidSectionNode':
            continue
        for ref in p.get('val', {}).get('ElementChildNodesOfVersionHistory', []) or []:
            series = by_id.get(_norm_identity(ref))
            if not series or series.get('type', '') != 'jcidPageSeriesNode':
                continue
            for child in series.get('val', {}).get('ChildGraphSpaceElementNodes', []) or []:
                g = gid(child)
                if g and g not in gosid_order:
                    gosid_order[g] = len(gosid_order)

    if not gosid_order:
        return {}
    return {oid: gosid_order[gs] for oid, gs in oid_to_gosid.items() if gs in gosid_order}


def _content_child_ids(prop) -> list:
    out = []
    val = prop.get('val', {}) if prop else {}
    for key in _CONTENT_CHILD_KEYS:
        refs = val.get(key)
        if isinstance(refs, list):
            out.extend(_norm_identity(r) for r in refs)
        elif refs:
            out.append(_norm_identity(refs))
    return [x for x in out if x]


def _page_content_order(props, by_id):
    """Walk each page's manifest tree in child order to recover the real visual
    order of its text runs AND standalone images/embedded files (own-identity
    keyed, same `_norm_identity` space `parser.py`'s main object loop already
    computes `own_identity` in — that's what lets a caller line an image back
    up with its slot here), and flag runs that sit under the TitleNode (the
    page title, not body). A picture/file's own order lets `_build_blocks`
    interleave it with the text it actually sat next to instead of always
    trailing every image after all the page's text (real bug, verified
    against a real page: an image between two paragraphs imported at the very
    end instead of its own row in place). Returns (order_index{run_id or
    image/file identity: seq}, title_run_ids)."""
    order = {}
    title_ids = set()
    counter = [0]
    seen = set()

    def walk(nid, in_title):
        if not nid or nid in seen:
            return
        seen.add(nid)
        p = by_id.get(nid)
        if not p:
            return
        t = p.get('type', '')
        if t in _TABLE_TYPES:
            # The table itself gets a slot (so _build_blocks can interleave
            # it with the text around it — real bug fixed: a table used to
            # always render before ALL page text regardless of where it
            # actually sat, e.g. above a "Causes:" label that introduced it).
            # Its rows/cells are walked/rendered separately, not through here.
            if not in_title and nid not in order:
                order[nid] = counter[0]
                counter[0] += 1
            return
        if t in _TABLE_PART_TYPES:
            return  # cells/rows: consumed via the table's own tree walk elsewhere
        if t == 'jcidRichTextOENode':
            if in_title:
                title_ids.add(nid)
            elif nid not in order:
                order[nid] = counter[0]
                counter[0] += 1
            return
        if t in _IMAGE_TYPES or t in _EMBED_TYPES:
            if not in_title and nid not in order:
                order[nid] = counter[0]
                counter[0] += 1
            return
        deeper = in_title or (t in _TITLE_TYPES)
        for child in _content_child_ids(p):
            walk(child, deeper)

    for p in props:
        if p.get('type', '') == 'jcidPageManifestNode':
            walk(_norm_identity(p.get('identity')), False)
    return order, title_ids


def _page_regions(props, by_id):
    """Group each page's own top-level `jcidOutlineNode` children — OneNote's
    independently-movable "floating boxes" on the page canvas — with their
    `OffsetFromParentHoriz/Vert` position and `LayoutMaxWidth/Height` size
    (verified against a real file: a page with 3 boxes reported 3 distinct
    (x, y) pairs matching its visual layout), and map every run/image/table/
    ink container structurally underneath one back to it.

    A page's title is never inside one of these — it hangs off the page node
    directly (`StructureElementChildNodes`), not through an outline — so,
    unlike `_page_content_order`, there's no title/body distinction to track
    here.

    Returns `(regions_by_page{page_guid: [{'guid','x','y','width','height'}, ...]}`
    in page order, `content_region{content_identity: region_guid})`. `guid`
    here is each outline node's own FULL `(base, revision)` identity, not
    just its base guid — multiple outline boxes on one page normally share
    the SAME base guid (they're all objects in that one page's object-space,
    distinguished only by revision counter), so the base guid alone can't
    tell two boxes apart. A page whose structure doesn't resolve this way
    (e.g. a still-encrypted protected section, where these child-reference
    properties don't decode at all) simply contributes no entries — same
    "additive, silently absent when it can't be built" shape as the rest of
    this module.
    """
    regions_by_page = {}
    content_region = {}
    seen = set()

    def walk(nid, region_guid):
        if not nid or nid in seen:
            return
        seen.add(nid)
        p = by_id.get(nid)
        if not p:
            return
        t = p.get('type', '')
        if t in _TABLE_TYPES or t in _TABLE_PART_TYPES:
            if t in _TABLE_TYPES and region_guid:
                content_region[nid] = region_guid
            return  # tables + their cell runs are handled/consumed separately
        if t == 'jcidRichTextOENode':
            if region_guid:
                content_region[nid] = region_guid
            return
        if region_guid and (t in _IMAGE_TYPES or t in _EMBED_TYPES or p.get('jcid_raw') == _INK_CONTAINER_JCID):
            content_region[nid] = region_guid
        for child in _content_child_ids(p):
            walk(child, region_guid)

    for p in props:
        if p.get('type', '') != 'jcidPageManifestNode':
            continue
        page_guid = (_norm_identity(p.get('identity')) or '').split(',')[0] or None
        # First-occurrence-wins per page, same as everywhere else in this
        # package — a stale duplicate PageManifestNode (see
        # onenote-file-format.md's revision-duplication section) shouldn't
        # get its own, likely-bogus, second region list for the same page.
        if not page_guid or page_guid in regions_by_page:
            continue
        for pnid in _content_child_ids(p):
            page_node = by_id.get(pnid)
            if not page_node or page_node.get('type') != 'jcidPageNode':
                continue
            region_list = regions_by_page.setdefault(page_guid, [])
            for onid in _content_child_ids(page_node):
                onode = by_id.get(onid)
                if not onode or onode.get('type') != 'jcidOutlineNode':
                    continue
                ov = onode.get('val', {})
                # The FULL identity, not the base guid: every object inside
                # one page's object-space (title, outlines, runs, ...)
                # normally shares that SAME base guid, differing only by
                # revision counter — real bug found using base-guid-only
                # here, verified against a real 6-box page: all 6 outline
                # nodes collapsed onto one identical `region_guid`, so every
                # region ended up rendering the SAME merged content instead
                # of its own box's.
                region_guid = onid
                if not region_guid:
                    continue
                region_list.append({
                    'guid': region_guid,
                    'x': _int(ov.get('OffsetFromParentHoriz')),
                    'y': _int(ov.get('OffsetFromParentVert')),
                    'width': _int(ov.get('LayoutMaxWidth')),
                    'height': _int(ov.get('LayoutMaxHeight')),
                })
                walk(onid, region_guid)
    return regions_by_page, content_region
