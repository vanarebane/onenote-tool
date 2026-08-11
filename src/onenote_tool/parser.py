"""Parse a single `.one` section file into `PageResult`s.

The main per-page builder: groups runs/images/tables/ink by their
object-space GUID into pages, in real visual/section order, with password
decryption handled transparently (everything downstream sees ordinary
plaintext once a correct password has been found).
"""
from __future__ import annotations

import html
import logging
import os
import re
from collections import OrderedDict
from datetime import datetime, time as dt_time
from typing import Optional

from . import decrypt as onenote_decrypt
from . import ink as onenote_ink
from ._patches import OneDocment, _active_decrypt, _active_shadow, check_valid, require_pyonenote
from .images import (
    _DHASH_THUMB_MATCH, _FILE_PLACEHOLDER, _IMAGE_EXTS, _IMAGE_PLACEHOLDER, _IMAGE_TYPES,
    _attachment_kind, _build_embedded_file, _build_page_image, _dham, _same_image, _youtube_from_rtu,
    _youtube_thumb_hashes,
)
from .models import ElementReport, PageRegion, PageResult
from .ordering import _object_space_order, _page_content_order, _page_regions
from .recovery import _flat_recover_protected_pages
from .rendering import (
    _RTU_MARKER, _child_ids, _has_visible_content, _image_block_html, _list_is_ordered, _media_block_html,
    _placeholder_blocks, _placeholder_run, _placeholder_text, _resolve_run_format, _resolve_table_grid,
    _runs_to_paragraphs, _rtu_to_inline_html, _table_html,
)
from .stamps import _is_stamp, classify_stamp
from .text import _clean_text, _clean_title, _extract_guid, _le_int, _norm_identity, decode_run_text

log = logging.getLogger(__name__)

# Each run's TextRunFormatting references an object-space ExtendedGUID that
# identifies the page the run belongs to. Grouping runs by this GUID rebuilds
# real pages (pyOneNote's flat list is in scrambled revision-store order, so
# positional page splitting mis-assigns titles and duplicates revised pages).
_GUID_RE = re.compile(r'\(([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}),')

_TITLE_TYPES = {'jcidTitleNode'}
_PAGE_TYPES = {'jcidPageNode'}
_PAGE_META_TYPES = {'jcidPageMetaData'}
_OUTLINE_TYPES = {'jcidOutlineNode', 'jcidOutlineElementNode'}
_TEXT_TYPES = {'jcidRichTextOENode'}
# Embedded objects (e.g. an inserted video, or a YouTube preview that plays
# inline in OneNote). Their preview picture imports as a static image;
# there's no dedicated clickable-preview-to-player embed (yet).
_EMBED_TYPES = {'jcidEmbeddedFileNode'}
_TABLE_TYPES = {'jcidTableNode'}
# Row/cell nodes are structural; consumed via the table node, not reported.
_TABLE_PART_TYPES = {'jcidTableRowNode', 'jcidTableCellNode'}
# Types we knowingly do not convert; counted as "unsupported" in the report.
_UNSUPPORTED_HINTS = {
    'jcidLinkNode',
}

# Human labels for the debug placeholder left in place of an element we can't import.
_HINT_LABELS = {
    'jcidLinkNode': 'a link/attachment',
}

# Ink/drawing object jcids. Microsoft explicitly declined to document these in
# the public OneNote format spec ("we have decided not to document these
# drawing objects" — Microsoft Open Specifications team, 2021, in response to
# a direct ask) — unlike most other types they have no name in pyOneNote's own
# JCID._jcid_name_mapping either, so they decode as the generic string
# "Unknown" and are otherwise indistinguishable from a genuinely-unhandled
# type. Matched by raw jcid instead (see 'jcid_raw' on each property, added by
# `_patches._get_properties`). First four confirmed by a community-reverse-
# engineered report citing that same Microsoft response; the rest were found
# co-occurring with them on a real ink page and are almost certainly
# companion ink sub-objects (stroke/layer data), so are treated the same way.
_INK_JCID_RAW = {
    0x00060014, 0x0002003B, 0x00020047, 0x00120048,
    0x00020054, 0x00020055, 0x00020056, 0x00020057,
}


def _run_guid(val) -> Optional[str]:
    """The object-space ExtendedGUID (page id) referenced by a run's formatting."""
    m = _GUID_RE.search(str(val.get('TextRunFormatting', '')))
    return m.group(1) if m else None


def _build_blocks(runs, tables, images, ink_container_ids, files, by_id, used_thumb_refs, report=None) -> list:
    """Render one content scope (a whole page, or a single `PageRegion`) into
    `blocks` — the same builder both call, so a region's content is rendered
    identically to how it would look in the flat, whole-page `blocks` list.

    `report` is optional and, when given, only ever used for the ink
    drawing's own converted/failed tally — passed for the whole-page call and
    omitted for each of that same page's region calls, so re-rendering the
    SAME ink drawing into a region's own blocks doesn't double-count it.
    """
    blocks = []

    for _tid, grid in tables:
        blocks.append({'kind': 'table', 'html': _table_html(grid)})

    if runs:
        blocks.append({'kind': 'text', 'html': _runs_to_paragraphs(runs)})

    for image in images:
        if image.ref in used_thumb_refs:
            continue  # shown inside the YouTube embed, not as a standalone image
        blocks.append({
            'kind': 'image', 'html': _image_block_html(image.ref),
            'image_src': image.ref, 'width': image.width, 'height': image.height,
        })

    # Audio/video attachments get an inline HTML5 player instead of a bare
    # download link — same underlying PageFile/bytes (still uploaded via
    # `page.files` like any attachment), just a different `kind` for the ones
    # a caller can actually play back. No dedicated jcid exists for either;
    # matched by filename extension, same as any other attachment type this
    # package doesn't have a specific node shape for.
    other_files = []
    for f in files:
        kind = _attachment_kind(os.path.splitext(f.filename)[1])
        if kind == 'file':
            other_files.append(f)
        else:
            blocks.append({'kind': kind, 'html': _media_block_html(kind, f.ref), f'{kind}_src': f.ref})
    if other_files:
        links = ''.join(
            f'<p>\U0001F4CE <a href="{html.escape(f.ref, quote=True)}">{html.escape(f.filename)}</a></p>'
            for f in other_files
        )
        blocks.append({'kind': 'file_links', 'html': links})

    if ink_container_ids:
        svg = onenote_ink.render_ink_containers(by_id, ink_container_ids, _norm_identity)
        if svg:
            if report is not None:
                report.ok('ink_drawing', len(ink_container_ids))
            blocks.append({'kind': 'ink', 'html': svg})
        else:
            if report is not None:
                report.fail('ink_drawing')
            blocks.append({
                'kind': 'text',
                'html': f'<p>{html.escape(_placeholder_text("a hand-drawn ink drawing", "could not be decoded"))}</p>',
            })

    # Drop text blocks that render as nothing visible — e.g. body_runs that
    # turned out to be ONLY blank-line markers (kept upstream to preserve
    # spacing between real content, but with no real content around them
    # they're just an empty block). Image/audio/video/table/ink blocks are
    # never dropped here even though they have no plain text.
    return [
        b for b in blocks
        if b.get('kind') in ('image', 'audio', 'video') or _has_visible_content(b.get('html', ''))
    ]


def parse_one_file(path: str, report: ElementReport, passwords: Optional[list] = None) -> tuple:
    """Parse a single .one section file into (list[PageResult], password_status).

    Runs are grouped by their object-space ExtendedGUID (one group = one page),
    which is robust against pyOneNote's scrambled revision-store ordering. The
    page title is the run that matches a cached page title; the page date/time
    are the stamp runs in the group (date and time may be separate runs).

    `passwords`, if the section turns out to be password-protected, is a list
    of candidate passwords tried in order (a resumable caller that processes a
    whole notebook across several calls accumulates every password the user
    has typed so far and tries them all here, so a shared password across
    sections only needs to be typed once); the first that verifies is used to
    derive the real content-encryption key (see decrypt.py) and re-parse the
    file a second time through a decrypted shadow buffer (see
    `_patches._propset_init`) — everything downstream of this function then
    sees ordinary plaintext runs/titles/images, no special-casing needed.

    `password_status` is None for an unprotected section, else one of
    'needs_password' (no candidates supplied), 'wrong_password' (none of the
    candidates verified), or 'decrypt_failed' (a password verified correct,
    but no content could be recovered — re-prompting for a password won't
    help). The caller surfaces this so an import UI can prompt for a
    (correct) password and retry when it would actually help.
    """
    require_pyonenote()

    candidates = [p for p in (passwords or []) if p]
    wrong_password = False
    with open(path, 'rb') as fh:
        if not check_valid(fh):
            raise RuntimeError('not a valid OneNote .one file')
        fh.seek(0)
        document = OneDocment(fh)

        if getattr(document, '_saw_encryption_marker', False) and candidates:
            blob = getattr(document, '_encryption_blob', None)
            file_key = info = None
            if blob:
                for candidate in candidates:
                    try:
                        info = onenote_decrypt.extract_encryption_info(blob)
                        file_key = onenote_decrypt.derive_and_verify_key(info, candidate)
                    except Exception:
                        log.exception('OneNote section decryption setup failed for %s', path)
                        file_key = None
                    if file_key is not None:
                        break
            if file_key is None:
                wrong_password = True
            else:
                fh.seek(0)
                raw = fh.read()
                shadow = onenote_decrypt.decrypt_package(raw, info, file_key)
                _active_shadow[0] = shadow
                _active_decrypt[0] = (info, file_key)
                try:
                    fh.seek(0)
                    document = OneDocment(fh)
                    # OneDocment.get_files() is lazy (unlike properties,
                    # which get read eagerly by __init__'s FileNode walk —
                    # already inside this window) — reading embedded file
                    # bytes (FileDataStoreObject, patched in _patches.py)
                    # needs the shadow/decrypt context too, or it silently
                    # reads raw ciphertext the moment anything (including
                    # get_json() below) first touches it after this window
                    # closes.
                    document.get_files()
                finally:
                    _active_shadow[0] = None
                    _active_decrypt[0] = None

        data = document.get_json()
        files_by_identity = {}
        for _g, fmeta in (document.get_files() or {}).items():
            ident = _extract_guid(fmeta.get('identity'))
            if ident:
                files_by_identity[ident] = fmeta

    props = data.get('properties', [])

    # identity -> property (first revision), used to reconstruct table structure.
    # Exception: if the first-seen occurrence decoded to an EMPTY val (the
    # PropertySet body decode failed — common for protected content, see
    # `_patches._ospropset_init`'s 3-attempt chain) and a LATER occurrence of
    # the exact same identity decoded real properties, upgrade to that one
    # instead of keeping a permanently-empty placeholder. Verified against a
    # real bug: a title object had two FileNode declarations sharing one
    # identity — the first's body genuinely never decoded, the second's did
    # (real CachedTitleString, confirmed structurally correct) — but "first
    # wins" threw the working one away, leaving the page's title unresolved
    # even though the title text WAS correctly recovered a moment later.
    # Doesn't change behavior for unprotected content, where the first
    # occurrence essentially always decodes non-empty already.
    by_id = {}
    for prop in props:
        nid = _norm_identity(prop.get('identity'))
        if not nid:
            continue
        existing = by_id.get(nid)
        if existing is None or (not existing.get('val') and prop.get('val')):
            by_id[nid] = prop

    # Authoritative page titles + their indent level live in page metadata.
    # PageLevel 1/2/3 (top/sub/sub-sub) -> sub_level 0/1/2.
    #
    # PageMetaData's own identity GUID (stripped of its ",N" revision counter)
    # equals the page's object-space guid — the SAME guid `_run_guid` extracts
    # to group runs into pages. That makes `page_title_by_guid` an authoritative
    # per-page title lookup, unlike the old "find a run whose text equals a
    # known title" heuristic: some pages (e.g. a title typed with nothing else
    # distinguishing it) have NO body run at all matching the title text, which
    # silently fell through to using the first body paragraph as the title.
    known_titles = set()
    page_levels = {}
    page_title_by_guid = {}
    page_level_by_guid = {}
    for prop in props:
        if prop.get('type', '') in _PAGE_META_TYPES:
            val = prop.get('val', {})
            t = _clean_title(val.get('CachedTitleString'))
            level = _le_int(val.get('PageLevel'), 1)
            guid = (_norm_identity(prop.get('identity')) or '').split(',')[0] or None
            if t and not _is_stamp(t):
                known_titles.add(t)
                if t not in page_levels or level < page_levels[t]:
                    page_levels[t] = level
                if guid and guid not in page_title_by_guid:
                    page_title_by_guid[guid] = t
                    page_level_by_guid[guid] = level

    # List membership: an outline element carrying a ListNodes ref is a list item;
    # its content rich-text run(s) become <li>. Bullet glyph -> <ul>, else <ol>.
    # OutlineElementChildLevel (1-based) drives nesting.
    list_runs = {}  # run identity -> {'ordered': bool, 'level': int}
    for prop in props:
        if prop.get('type', '') != 'jcidOutlineElementNode':
            continue
        val = prop.get('val', {})
        refs = val.get('ListNodes')
        if not refs:
            continue
        ordered = False
        for lref in (refs if isinstance(refs, list) else [refs]):
            ln = by_id.get(_norm_identity(lref))
            if ln:
                ordered = _list_is_ordered(ln.get('val', {}).get('NumberListFormat', ''))
                break
        level = max(0, _le_int(val.get('OutlineElementChildLevel'), 1) - 1)
        for rid in _child_ids(prop):
            list_runs[rid] = {'ordered': ordered, 'level': level}

    # Reconstruct visual order + which runs are the page title, from the tree.
    order_index, title_run_ids = _page_content_order(props, by_id)
    # Section page order (which page comes first), keyed by page object-space guid.
    page_order = _object_space_order(document, props, by_id)
    # Each page's own floating boxes (canvas position) and which content sits
    # in which one — see `models.PageRegion` / `ordering._page_regions`.
    regions_by_page, content_region = _page_regions(props, by_id)

    groups = OrderedDict()  # guid -> {runs, images, files, date, time}
    image_counter = [0]
    file_counter = [0]
    consumed_file_idents = set()  # EmbeddedFileContainer identities already attached to a page
    current_guid = None
    image_by_key = {}        # dedup_key -> index into image_records (keep larger)
    image_records = []       # (page_guid, near_guid, PageImage) assigned after grouping
    # ref (image/file placeholder token) -> the originating prop's own identity,
    # used only to look a ref up in `content_region` (see `_page_regions`) once
    # regions are computed below — kept separate from PageImage/PageFile
    # themselves so their public shape doesn't grow an internal-only field.
    identity_by_ref = {}

    def group_for(guid):
        return groups.setdefault(guid, {'runs': [], 'images': [], 'files': [], 'date': None, 'time': None, 'run_ids': set()})

    for prop in props:
        ptype = prop.get('type', '')
        val = prop.get('val', {})

        if ptype in _TEXT_TYPES:
            run_id = _norm_identity(prop.get('identity'))
            guid = _run_guid(val)
            if guid:
                current_guid = guid
            if not current_guid:
                continue
            grp = group_for(current_guid)
            raw = val.get('TextExtendedAscii')
            rtu = val.get('RichEditTextUnicode')
            # A run's links live in RichEditTextUnicode (HYPERLINK fields); the
            # plain text lives in TextExtendedAscii. Use the linkified version
            # when the run carries a hyperlink, otherwise the plain text.
            inline_html = None
            if isinstance(rtu, str) and _RTU_MARKER in rtu:
                yt = _youtube_from_rtu(rtu)
                if yt is not None:
                    if not any(r.get('yt', {}).get('videoId') == yt['videoId'] for r in grp['runs']):
                        grp['runs'].append({'t': yt['title'], 'h': None, 'yt': yt})
                    report.ok('youtube')
                    continue
                text, inline_html = _rtu_to_inline_html(rtu)
                report.ok('link_run')
            else:
                text = _clean_text(decode_run_text(raw)) if raw is not None else ''
            if not text:
                # An empty run that is a real body outline element is a blank line
                # the user inserted (OneNote stores each as its own paragraph);
                # keep it as an empty paragraph to preserve the spacing — that IS
                # a successful (if invisible) conversion, not a failure. Junk
                # empty runs (not in the page's content tree) are dropped
                # silently: nothing of substance to report either way.
                if run_id in order_index and run_id not in grp['run_ids']:
                    grp['run_ids'].add(run_id)
                    grp['runs'].append({'t': '', 'h': '', 'rid': run_id, 'blank': True})
                    report.ok('blank_paragraph')
                continue
            if inline_html is None:
                kind, value = classify_stamp(text)
                if kind == 'datetime':
                    grp['date'] = value.date()
                    if value.hour or value.minute:
                        grp['time'] = value.time()
                    # Not a failure — the stamp's info is kept as the page's
                    # created_at instead of being shown as body text.
                    report.ok('timestamp')
                    continue
                if kind == 'time':
                    grp['time'] = value
                    report.ok('timestamp')
                    continue
            # Dedup by object IDENTITY, not by text: revision copies share an
            # identity (collapse them), but two different cells/paragraphs with
            # the same text have different identities and must both be kept
            # (otherwise duplicate table cells vanish).
            if run_id is not None:
                if run_id in grp['run_ids']:
                    continue
                grp['run_ids'].add(run_id)
            elif any(r['t'] == text for r in grp['runs']):
                continue
            run_entry = {'t': text, 'h': inline_html, 'rid': run_id}
            if run_id in list_runs:
                run_entry['list'] = list_runs[run_id]
            fmt = _resolve_run_format(val.get('ParagraphStyle'), val.get('TextRunFormatting'), by_id)
            if fmt is not None:
                run_entry['fmt'] = fmt
            grp['runs'].append(run_entry)
            report.ok('text_run')
            continue

        if ptype in _IMAGE_TYPES or ptype in _EMBED_TYPES:
            own_identity = _norm_identity(prop.get('identity'))

            # An embedded-file node carries the REAL attached file separately
            # from its preview picture (EmbeddedFileContainer vs PictureContainer
            # — see _build_embedded_file). Extract it first so a spreadsheet/zip/
            # etc. attachment gets a working download link on the page even when
            # its preview is missing or an unsupported format (e.g. .emf).
            embedded_file = None
            if ptype in _EMBED_TYPES and current_guid:
                embedded_file = _build_embedded_file(val, files_by_identity, file_counter, consumed_file_idents, report)
                if embedded_file is not None:
                    group_for(current_guid)['files'].append(embedded_file)
                    identity_by_ref[embedded_file.ref] = own_identity

            # The image's PictureContainer GUID equals its page's object space,
            # so it can be placed on the right page. Dedup across revisions and
            # web/original variants by (page GUID, filename) — OneNote stores a
            # downscaled "web" copy of each pasted image as a second node — and
            # keep the largest resolution among duplicates. Embedded objects
            # (video/YouTube) contribute their preview picture the same way.
            image = _build_page_image(val, files_by_identity, image_counter, report)
            if image is None:
                # Embedded object with no extractable preview — if the real file
                # wasn't attachable either, leave a visible marker instead of
                # silently dropping it.
                if ptype in _EMBED_TYPES and current_guid and embedded_file is None:
                    fname = str(val.get('EmbeddedFileName') or '').replace('\x00', '').strip()
                    desc = f'an embedded file{f" ({fname})" if fname else ""}'
                    group_for(current_guid)['runs'].append(_placeholder_run(desc, 'no preview to import'))
                continue
            gm = _GUID_RE.search(str(val.get('PictureContainer', '')))
            page_guid = gm.group(1) if gm else None
            fname = str(val.get('ImageFilename') or val.get('EmbeddedFileName') or '').replace('\x00', '').strip()
            dedup_key = (page_guid, fname) if (page_guid and fname) else _extract_guid(val.get('PictureContainer'))
            # Same (page, filename) can hold DIFFERENT pictures (e.g. two
            # "Untitled picture.png"); only merge when the content hash matches.
            matched = None
            for idx in image_by_key.get(dedup_key, []):
                if _same_image(image, image_records[idx][2]):
                    matched = idx
                    break
            if matched is not None:
                if image.source_width > image_records[matched][2].source_width:
                    image_records[matched] = (page_guid, current_guid, image)  # prefer larger
                    identity_by_ref[image.ref] = own_identity
                report.fail('image_duplicate')
                continue
            if dedup_key is not None:
                image_by_key.setdefault(dedup_key, []).append(len(image_records))
            image_records.append((page_guid, current_guid, image))
            identity_by_ref[image.ref] = own_identity
            report.ok('embedded_preview' if ptype in _EMBED_TYPES else 'image')
            continue

        if ptype in _TABLE_TYPES:
            # Reconstruct the real table by walking the object tree (rows -> cells
            # -> content). `consumed` records the cell text runs so they aren't
            # also emitted as page paragraphs. Dedup revision copies by identity.
            if current_guid:
                grp = group_for(current_guid)
                tid = _norm_identity(prop.get('identity'))
                seen_tables = grp.setdefault('table_ids', set())
                if tid is None or tid not in seen_tables:
                    if tid is not None:
                        seen_tables.add(tid)
                    grid = _resolve_table_grid(prop, by_id, grp.setdefault('table_consumed', set()))
                    if grid:
                        grp.setdefault('tables', []).append((tid, grid))
            report.ok('table')
            continue

        if ptype in _TABLE_PART_TYPES:
            continue  # structural; consumed via the table node

        if ptype in _TITLE_TYPES:
            report.ok('title')
        elif ptype in _OUTLINE_TYPES:
            report.ok('outline')
        elif ptype in _UNSUPPORTED_HINTS:
            report.fail(ptype)
            if current_guid:
                group_for(current_guid)['runs'].append(
                    _placeholder_run(_HINT_LABELS.get(ptype, ptype), 'not supported yet')
                )
        elif prop.get('jcid_raw') in _INK_JCID_RAW:
            # A drawing is made of many of these sub-objects (container,
            # data node, one node per stroke, shared stroke-properties
            # nodes, ...) — only the CONTAINER is a drawing's own top-level
            # object; the rest are reached by walking references out from it
            # (see onenote_ink.render_ink_containers). Collect just the
            # containers here; actual decoding happens once per page below,
            # after the full `by_id` map exists.
            if current_guid and prop.get('jcid_raw') == onenote_ink.JCID_INK_CONTAINER:
                ident = _norm_identity(prop.get('identity'))
                if ident:
                    # An ink container's OWN identity's base GUID (its OID,
                    # not a reference) IS its page's object-space GUID —
                    # same rule as everywhere else in MS-ONESTORE ("objects
                    # in one object space share a base GUID"), and unlike
                    # `current_guid` it needs no traversal-order assumption
                    # at all. Root cause of a real bug: a container can
                    # appear in traversal order BEFORE its own page's
                    # title/first text run has updated `current_guid` (a
                    # revision-duplicate generation sorting earlier than its
                    # page's other content) — its drawing would otherwise
                    # render on the PRECEDING page instead. Using the
                    # container's own guid sidesteps the ordering question.
                    #
                    # Gated on `current_guid` already being set (NOT just
                    # "ident parsed OK"): for a still-encrypted protected
                    # section current_guid never gets set at all (real
                    # content never resolves in this un-shadowed pass), so
                    # this whole branch is skipped. Without that gate, a
                    # protected section's ink containers (identity IS
                    # plaintext, so `ink_guid` resolves even though nothing
                    # else does) spuriously create a real-looking page here,
                    # which makes `pages` non-empty below and skips the
                    # protected-recovery fallback entirely — a real
                    # regression this gate prevents.
                    ink_guid = ident.split(',')[0] or current_guid
                    group_for(ink_guid).setdefault('ink_container_ids', []).append(ident)

    # Assign each image to its page: prefer the page whose GUID matches the
    # image's PictureContainer GUID, else the nearest preceding text page, else
    # the first page (never create a new page just for an image).
    for page_guid, near_guid, image in image_records:
        if page_guid and page_guid in groups:
            target = page_guid
        elif near_guid and near_guid in groups:
            target = near_guid
        elif groups:
            target = next(iter(groups))
        else:
            target = '__images_only__'
        group_for(target)['images'].append(image)

    pages = []  # list of (section_order, PageResult)
    for _guid, grp in groups.items():
        runs = grp['runs']  # {'t': text, 'h': inline html or None, 'yt': {...}}

        # Pair each YouTube embed with the page image that actually IS the
        # video's thumbnail — verified by comparing perceptual hashes against
        # the real YouTube thumbnail (fetched from the internet). If none match
        # (or there's no network), leave the embed to load the thumbnail from
        # YouTube instead of grabbing an unrelated picture.
        used_thumb_refs = set()
        for run in runs:
            yt = run.get('yt')
            if not yt:
                continue
            yt_hashes = _youtube_thumb_hashes(yt['videoId'])
            if not yt_hashes:
                continue  # no reference -> embed derives the thumbnail from YouTube
            best, best_d = None, 999
            for img in grp['images']:
                if img.ref in used_thumb_refs or img.dhash is None:
                    continue
                d = min((_dham(img.dhash, yh) for yh in yt_hashes), default=999)
                if d < best_d:
                    best, best_d = img, d
            if best is not None and best_d <= _DHASH_THUMB_MATCH:
                yt['thumb_ref'] = best.ref
                used_thumb_refs.add(best.ref)

        # Cell text runs belonging to a table are shown in the table, not as page
        # paragraphs, and are not eligible to be the page title.
        consumed = grp.get('table_consumed', set())
        # PageMetaData's CachedTitleString for this exact page guid is
        # authoritative when available (see page_title_by_guid above) — a page
        # can have no body run matching its title text at all (a title with
        # nothing else distinguishing it), in which case the run-based
        # fallbacks below would misattribute the title to the first paragraph.
        meta_title = page_title_by_guid.get(_guid)
        title_run = None
        if meta_title:
            title = meta_title[:100]
        else:
            # The page title is the run under the TitleNode (from the tree walk);
            # fall back to a run whose text matches a cached page title, then the
            # first run.
            title_run = next(
                (r for r in runs if r.get('rid') in title_run_ids and r.get('rid') not in consumed),
                None,
            )
            if title_run is None:
                title_run = next(
                    (r for r in runs if not r.get('yt') and r.get('rid') not in consumed and r['t'] in known_titles),
                    None,
                )
            if title_run is None:
                text_runs = [r for r in runs if not r.get('yt') and r.get('rid') not in consumed and r['t']]
                title_run = text_runs[0] if text_runs else None
            title = title_run['t'][:100] if title_run else None
        # Body = everything except the title area and table cells, in the page's
        # real top-to-bottom order (placeholders/unordered runs sort to the end).
        body_runs = [
            r for r in runs
            if r is not title_run and r.get('rid') not in consumed and r.get('rid') not in title_run_ids
        ]
        body_runs.sort(key=lambda r: order_index.get(r.get('rid'), 10 ** 9))

        ink_container_ids = list(dict.fromkeys(grp.get('ink_container_ids') or []))
        tables = grp.get('tables', [])
        files = grp.get('files', [])
        blocks = _build_blocks(
            body_runs, tables, grp['images'], ink_container_ids, files, by_id, used_thumb_refs, report=report,
        )

        if not blocks and not title:
            continue  # empty group, skip

        created_at = None
        if grp['date'] is not None:
            created_at = datetime.combine(grp['date'], grp['time'] or dt_time(0, 0))

        # Floating-region view of the SAME content (see `models.PageRegion`):
        # partition this page's already-collected runs/tables/images/ink/
        # files by which top-level outline box (`content_region`, from
        # `ordering._page_regions`) each one structurally belongs to, and
        # render each subset through the exact same `_build_blocks` used
        # above. `report=None` here — the whole-page call above already
        # tallied ink drawings; rendering the same drawing again into its
        # region's own blocks must not double-count it. A region that
        # resolves to no content (nothing in `content_region` pointed at it)
        # is dropped rather than included empty.
        regions = []
        for rmeta in regions_by_page.get(_guid, []):
            rguid = rmeta['guid']
            r_runs = [r for r in body_runs if content_region.get(r.get('rid')) == rguid]
            r_tables = [t for t in tables if content_region.get(t[0]) == rguid]
            r_images = [img for img in grp['images'] if content_region.get(identity_by_ref.get(img.ref)) == rguid]
            r_ink_ids = [i for i in ink_container_ids if content_region.get(i) == rguid]
            r_files = [f for f in files if content_region.get(identity_by_ref.get(f.ref)) == rguid]
            r_blocks = _build_blocks(r_runs, r_tables, r_images, r_ink_ids, r_files, by_id, used_thumb_refs)
            if r_blocks:
                regions.append(PageRegion(
                    x=rmeta['x'], y=rmeta['y'], width=rmeta['width'], height=rmeta['height'], blocks=r_blocks,
                ))

        pages.append((
            page_order.get(_guid, 10 ** 9),
            PageResult(
                title=title or 'Untitled page',
                blocks=blocks,
                block_count=len(blocks),
                created_at=created_at,
                images=grp['images'],
                files=files,
                sub_level=max(0, page_level_by_guid.get(_guid, page_levels.get(title, 1)) - 1),
                regions=regions,
            ),
        ))

    # Report files that ended up neither an inline image nor an attached
    # download link (e.g. a preview-only file with no matching embed node).
    for _g, fmeta in (data.get('files') or {}).items():
        ext = (fmeta.get('extension') or '').lower()
        if ext in _IMAGE_EXTS:
            continue
        ident = _extract_guid(fmeta.get('identity'))
        if ident in consumed_file_idents:
            continue
        report.fail(f'file{ext or "?"}')

    # Order pages as OneNote does (the section's page-series list); groups with no
    # known order keep their storage position (stable sort).
    pages.sort(key=lambda item: item[0])

    if getattr(document, '_saw_encryption_marker', False) and candidates and not wrong_password:
        # ALWAYS try structural recovery for a verified-password-protected
        # section, even when the main per-page pipeline above already found
        # SOME pages — real regression found and fixed here: reading real
        # OID/ObjectSpaceID streams (see `_patches._ospropset_init`'s
        # Attempt 2) lets `_run_guid`/`PictureContainer` SOMETIMES resolve
        # now, which makes `pages` non-empty and used to skip structural
        # recovery entirely — but that resolution is still unreliable
        # per-object (some images' PictureContainer resolves, others don't;
        # same for page grouping), so the main pipeline's "partial success"
        # was demonstrably WORSE than structural recovery's comprehensive
        # result on a real file (images vanished, several pages lost all
        # content). Structural recovery doesn't depend on any reference
        # resolving — prefer it whenever it produces anything, regardless
        # of what the main pipeline did.
        recovered = _flat_recover_protected_pages(document, by_id)
        if recovered:
            report.ok('password_protected_section:flat_recovery', len(recovered))
            return recovered, None

    if not pages and getattr(document, '_saw_encryption_marker', False):
        # The structural skeleton parsed fine (see `_patches._encryption_key_fnd_fix`),
        # but either no password was supplied or it was wrong, so every page's
        # property-set body is still ciphertext and every group ended up with
        # no usable title/content and was dropped above — this section would
        # otherwise vanish from the import with no trace. Page titles are
        # encrypted too (jcidPageMetaData nodes carry duplicate/revision
        # entries with no reliable way to dedupe them without readable
        # content), so an exact page count isn't recoverable — the message
        # stays generic rather than quoting a number that's likely wrong.
        if wrong_password:
            report.fail('password_protected_section:wrong_password')
            reason = 'the supplied password did not match this section — re-import with the correct password'
            status = 'wrong_password'
        elif candidates:
            # Already tried structural recovery above (this section is
            # verified-password, `candidates` is truthy) and it returned
            # nothing — don't call it again.
            report.fail('password_protected_section:decrypt_failed')
            reason = 'decryption was attempted but produced no readable pages — remove the password in OneNote and re-export to import this section'
            status = 'decrypt_failed'
        else:
            report.fail('password_protected_section:needs_password')
            reason = 'this section is password-protected — supply its password to decrypt it'
            status = 'needs_password'
        return [PageResult(
            title='Password protected',
            blocks=_placeholder_blocks('this password-protected OneNote section', reason),
            block_count=1,
        )], status
    return [pr for _order, pr in pages], None
