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
    order of its text runs, and flag runs that sit under the TitleNode (the page
    title, not body). Returns (order_index{run_id: seq}, title_run_ids)."""
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
        if t in _TABLE_TYPES or t in _TABLE_PART_TYPES:
            return  # tables + their cell runs are handled/consumed separately
        if t == 'jcidRichTextOENode':
            if in_title:
                title_ids.add(nid)
            elif nid not in order:
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
