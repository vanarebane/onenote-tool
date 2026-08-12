"""Best-effort content recovery for password-protected sections.

Two independent mechanisms, tried in order by `_flat_recover_protected_pages`
(the entry point `parser.parse_one_file` calls once a password has been
verified but the normal per-page grouping found nothing usable):

1. **Structural (file-position) recovery** — `_structural_page_groups` +
   `_pages_from_structural_groups`. The mechanism that actually generalizes
   to real multi-page, multi-image protected sections; see its docstring.
2. **Flat/legacy recovery** — the tail of `_flat_recover_protected_pages`
   itself, kept as a fallback for a section shape structural recovery
   doesn't recognize. Its own docstring is the reasoning trail for why the
   structural approach exists (several earlier heuristics tried and
   rejected — byte-offset content proximity, object-space/gosid grouping,
   file/title identity-OID equality).
"""
from __future__ import annotations

import io
from typing import Optional

from . import ink as onenote_ink
from ._patches import OneDocment, _pyonenote_filenode
from .images import (
    _FILE_PLACEHOLDER, _IMAGE_EXTS, _IMAGE_PLACEHOLDER, _MAX_IMAGE_DISPLAY_WIDTH, _attachment_kind,
    _youtube_from_rtu,
)
from .models import PageFile, PageImage, PageResult
from .rendering import (
    _child_ids, _image_block_html, _list_is_ordered, _media_block_html, _paragraph_alignment,
    _resolve_run_format, _resolve_table_grid, _rtu_to_inline_html, _runs_to_paragraphs, _table_html,
)
from .stamps import classify_stamp
from .text import _clean_text, _le_int, _norm_identity, decode_run_text


def _structural_page_groups(document) -> list:
    """Group a protected section's declarations by structural (plaintext)
    file position — the correlation mechanism that actually generalizes,
    found after byte-offset-of-CONTENT proximity, object-space/gosid
    grouping, and file/title identity-OID equality all failed on a real
    multi-page, multi-image protected section (see `_flat_recover_
    protected_pages`'s docstring for that history). Verified against that
    same file: each page's declarations — its title, then its body text
    runs, then its embedded files — are laid out CONTIGUOUSLY in the file,
    in file-position order, ending exactly where the next title's
    declaration begins. This needs NO OIDs-stream resolution at all:
    `ref.stp` (a run/title's out-of-line property-set position) and
    `_structural_start` (a FileNode's own position, set on every node by
    `_patches._filenode_init`) are both structural framing — real plaintext
    even in a protected section, same as everywhere else in this package
    that reads position/type/identity without touching content.

    Returns an ordered list of `{'title_identity', 'text_identities': [...],
    'file_guids': [...], 'ink_identities': [...], 'table_identities': [...]}`
    — one per title declaration that has ANY content after it before the next
    title (a
    title-only stub with nothing following is dropped — almost always a
    stale/superseded revision; OneNote's revision store keeps many of
    these).

    `file_guids` deliberately is NOT the file object's own OID: pyOneNote's
    `OneDocment.get_files()` keys its dict by `FileDataReference.StringData`
    (an embedded `<ifndf>{guid}` reference string, normalized) — the file's
    OID only ends up as that dict entry's `identity` VALUE, and only for
    this node type (`FileDataStoreObjectReferenceFND`-sourced entries leave
    it `""`). So matching by OID silently drops every file whose content
    arrived via that other node type. Reading `FileDataReference` ourselves
    and normalizing it exactly like `get_files()` does lets
    `_pages_from_structural_groups` look files up directly by that same key,
    covering both node shapes as long as the *declaration* — always
    `ObjectDeclarationFileData3RefCountFND`, structurally page-local — is
    what we anchor the position on. The actual bytes (`FileDataStoreObject
    ReferenceFND`) live in a shared data-store area with no per-page
    position at all, which is exactly why this function never looks at it.

    Ink containers are `jcidInkContainer`-typed PropertySet objects — same
    `ref.stp`/`body.oid` shape as titles/text runs, just not in pyOneNote's
    small jcid name table (decodes as "Unknown"), so identified by the raw
    jcid int instead (`onenote_ink.JCID_INK_CONTAINER`, same constant the
    normal per-page walk already uses).
    """
    try:
        nodes = []
        OneDocment.traverse_nodes(document.root_file_node_list, nodes, [])
    except Exception:
        return []

    decls = []  # (stp, kind, identity_str)
    for node in nodes:
        data = getattr(node, 'data', None)
        if data is None:
            continue
        inner = getattr(data, 'base', data)
        ref = getattr(inner, 'ref', None)
        body = getattr(inner, 'body', None)
        jcid = getattr(body, 'jcid', None)
        if ref is not None and jcid is not None:
            jname = str(jcid)
            if jname in ('jcidPageMetaData', 'jcidRichTextOENode', 'jcidTableNode'):
                decls.append((ref.stp, jname, str(body.oid)))
            elif getattr(jcid, 'jcid', None) == onenote_ink.JCID_INK_CONTAINER:
                decls.append((ref.stp, 'Ink', str(body.oid)))
            continue
        if isinstance(data, _pyonenote_filenode.ObjectDeclarationFileData3RefCountFND):
            start = getattr(node, '_structural_start', None)
            ref_data = getattr(data, 'FileDataReference', None)
            guid = getattr(ref_data, 'StringData', None)
            if start is not None and guid:
                guid = guid.replace('<ifndf>{', '').replace('}', '').lower()
                decls.append((start, 'FileData', guid))
    decls.sort(key=lambda d: d[0])

    # A page's own TEXT/INK declarations sit close together (verified: a
    # page's own text runs stayed within ~6KB of its title in a real file) —
    # a gap far past that, before the next title, is the revision-store
    # cluster boundary (stale generations sit in entirely separate
    # clusters, megabytes away from the live one). Verified directly
    # against a real bug report: a title's own text ended cleanly ~2KB in,
    # then the VERY NEXT text declaration was 24.5 MILLION bytes later and
    # belonged to a totally different (stale, title-decode-failed) page —
    # silently swallowed as this title's own content because no other title
    # happened to intervene before it. Starting a fresh (untitled) group at
    # such a gap stops that: the existing dedup logic already down-ranks
    # untitled groups against real titled ones, so spillover like this
    # loses to wherever its content legitimately belongs, instead of being
    # permanently misattributed.
    #
    # Deliberately NOT applied to FileData declarations: unlike text, a
    # page's own embedded-file declarations can legitimately sit millions of
    # bytes from its title in some files (confirmed on a real file: a
    # 6-image page's title/text stayed close together, but its own 6 FileData
    # declarations lived far away in the same distant region spillover text
    # ALSO happened to occupy) — gap-splitting there broke real, correct
    # image attribution (the very "Image" page this mechanism was built to
    # fix), so FileData always attaches to whatever group is `current`,
    # exactly as before this change.
    _CLUSTER_GAP_THRESHOLD = 1_000_000

    groups = []
    current = None
    prev_text_stp = None
    orphan_counter = 0
    for stp, kind, identity in decls:
        if kind == 'jcidPageMetaData':
            current = {
                'title_identity': _norm_identity(identity), 'text_identities': [],
                'file_guids': [], 'ink_identities': [], 'table_identities': [],
            }
            groups.append(current)
            prev_text_stp = stp
            continue
        # Tables get the same gap-based clustering as text/ink (not FileData's
        # always-attach-to-current): a table's own declaration is structurally
        # page-local, same shape as a text run's, not the shared-store split
        # FileData has (see the comment above on why FileData is different).
        if kind in ('jcidRichTextOENode', 'Ink', 'jcidTableNode'):
            if prev_text_stp is not None and stp - prev_text_stp > _CLUSTER_GAP_THRESHOLD:
                # No real title identity to key this synthetic group by — but
                # it still needs a UNIQUE one (not None/falsy shared across
                # every orphan group), since `_pages_from_structural_groups`
                # dedupes its final results by `title_identity`; sharing one
                # key would make unrelated orphan clusters collide and
                # silently drop all but the richest.
                orphan_counter += 1
                current = {
                    'title_identity': f'__orphan_{orphan_counter}__', 'text_identities': [],
                    'file_guids': [], 'ink_identities': [], 'table_identities': [],
                }
                groups.append(current)
            prev_text_stp = stp
        if current is None:
            continue
        if kind == 'jcidRichTextOENode':
            norm = _norm_identity(identity)
            if norm:
                current['text_identities'].append(norm)
        elif kind == 'Ink':
            norm = _norm_identity(identity)
            if norm:
                current['ink_identities'].append(norm)
        elif kind == 'jcidTableNode':
            norm = _norm_identity(identity)
            if norm:
                current['table_identities'].append(norm)
        else:  # 'FileData' — identity is already a normalized get_files() guid key
            current['file_guids'].append(identity)

    return [
        g for g in groups
        if g['text_identities'] or g['file_guids'] or g['ink_identities'] or g['table_identities']
    ]


def _pages_from_structural_groups(document, by_id: dict, groups: list) -> list:
    """Build real PageResults from `_structural_page_groups`' output,
    decoding each group's title/text identities through `by_id` (the same
    shadow-decrypted property map `parser.parse_one_file` already built)
    and its file guids directly against `document.get_files()` (see
    `_structural_page_groups`'s docstring for why that's a guid, not an
    OID) and its ink identities through `onenote_ink.render_ink_containers`
    (same helper the normal per-page path uses)."""
    try:
        files = document.get_files() or {}
    except Exception:
        files = {}

    # List membership, mirroring `parser.parse_one_file`'s own construction
    # of this same mapping exactly, just walking `by_id` (deduped, shadow-
    # decrypted properties) instead of the raw `props` list — a run's own
    # PropertySet body has the SAME 3-attempt decode ceiling as everywhere
    # else in this file, so this recovers list membership for however many
    # OutlineElementNode/NumberListNode objects actually decoded, no more
    # (unlike jcidTableNode — see `_structural_page_groups`'s table handling
    # — this isn't a 0% ceiling on a real test file, just the usual partial
    # one: verified ~47% of OutlineElementNode and ~25% of NumberListNode
    # objects decoded non-empty on a real protected section).
    list_runs = {}
    for prop in by_id.values():
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

    def _title_text(identity):
        t = (by_id.get(identity) or {}).get('val', {}).get('CachedTitleString')
        if isinstance(t, str) and t.strip():
            return _clean_text(decode_run_text(t)) or t.strip()
        return None

    _MAX_PLAUSIBLE_PAGE_LEVEL = 20  # OneNote UI caps nesting well under this

    def _page_sub_level(identity):
        """OneNote PageLevel (1/2/3 = top/sub/sub-sub) -> sub_level (0/1/2),
        same field and same off-by-one as `parser.parse_one_file`'s main
        pipeline reads from the identical `jcidPageMetaData` property — just
        via `by_id` (shadow-decrypted) instead of the raw `props` list.
        Defaults to 0 (top-level) when the property didn't decode, same
        default the main pipeline falls back to.

        Clamped: verified on a real protected file that `PageLevel` can
        decode to outright garbage (a misaligned property landing on
        unrelated ciphertext) rather than just failing cleanly — one page
        came back as `PageLevel=3334854043`, which without a plausibility
        cap would have produced an equally nonsensical `sub_level`. The
        normal (unprotected) pipeline doesn't need this same guard: its
        PropertySet decode doesn't hit misaligned reads the way protected
        content's 3-attempt chain does, so garbage values like this haven't
        been observed there."""
        val = (by_id.get(identity) or {}).get('val', {})
        level = _le_int(val.get('PageLevel'), 1)
        if not 1 <= level <= _MAX_PLAUSIBLE_PAGE_LEVEL:
            level = 1
        return max(0, level - 1)

    def _run_entry(identity):
        """Mirrors the normal per-page pipeline's TEXT_TYPES branch closely
        enough to reuse its renderer, `_runs_to_paragraphs` — same run-dict
        shape ({'t','h','fmt','list','yt'}), so formatting (bold/italic/
        color/highlight/headings, resolved via `_resolve_run_format` against
        the same shadow-decrypted `by_id`), list membership, and a standalone
        YouTube link render instead of being silently dropped as plain text
        or a bare `<a>`.

        The YouTube case is wired the same way as everything else here —
        reusing `images._youtube_from_rtu`, the SAME detector the normal
        pipeline uses, against the same shadow-decrypted `RichEditTextUnicode`
        — but unlike lists/alignment/tables above, it's NOT verified against
        a real decoded example: the one real YouTube link in the test file
        used for this never decoded at all (`RichEditTextUnicode` came back
        empty), same ordinary partial-decode ceiling as everything else, not
        a new failure mode. Deliberately does NOT attempt the normal
        pipeline's thumbnail-perceptual-hash matching
        (`_youtube_thumb_hashes`/`images._build_page_image`'s `dhash`) —
        `_pages_from_structural_groups`'s own `PageImage`s don't compute a
        `dhash` at all, and matching one to the right embed here would be
        new, unverified logic on top of already-unverified input; the embed
        still renders correctly without a matched thumbnail, just falling
        back to YouTube's own thumbnail client-side (see
        `rendering._youtube_embed_html`)."""
        val = (by_id.get(identity) or {}).get('val', {})
        raw = val.get('TextExtendedAscii')
        rtu = val.get('RichEditTextUnicode')
        # See parser.py's identical fix — RichEditTextUnicode is used
        # whenever present, not just when it carries a hyperlink; a run's
        # plain (non-hyperlink) text can live there too when it needs real
        # Unicode TextExtendedAscii can't hold (e.g. a currency symbol).
        inline_html = None
        if isinstance(rtu, str) and rtu.replace('\x00', '').strip():
            yt = _youtube_from_rtu(rtu)
            if yt is not None:
                return {'t': yt['title'] or yt['url'], 'h': None, 'yt': yt}
            text, inline_html = _rtu_to_inline_html(rtu)
        elif raw is not None:
            text = _clean_text(decode_run_text(raw))
        else:
            return None
        if not text or classify_stamp(text)[0] is not None:
            return None
        entry = {'t': text, 'h': inline_html}
        if identity in list_runs:
            entry['list'] = list_runs[identity]
        align = _paragraph_alignment(val.get('ParagraphAlignment'))
        if align:
            entry['align'] = align
        fmt = _resolve_run_format(val.get('ParagraphStyle'), val.get('TextRunFormatting'), by_id)
        if fmt is not None:
            entry['fmt'] = fmt
        return entry

    untitled_counter = [0]

    # Resolve every group's title FIRST (before filtering any run text) —
    # a stray run whose text happens to equal ANY page's real title (not
    # just THIS group's own) is almost always a duplicate title-echo that
    # landed in a different structural window than its real title, not
    # genuine body content. Verified against a real bug: a group with no
    # other content of its own except the literal word "Värvid" surfaced as
    # its own near-empty page right next to the real, correctly-titled
    # "Värvid" page — cross-checking against every KNOWN title (not just
    # this group's) is what a single-title check can't catch.
    real_titles = {}
    for grp in groups:
        t = _title_text(grp['title_identity'])
        if t:
            real_titles[grp['title_identity']] = t
    all_title_texts = set(real_titles.values())

    # PHASE 1: decode each group's raw content (title + run dicts + file
    # guids + tables), no rendering/claiming yet.
    decoded = []
    for grp in groups:
        real_title = real_titles.get(grp['title_identity'])
        if real_title:
            title = real_title
        else:
            untitled_counter[0] += 1
            title = f'Untitled page {untitled_counter[0]}' if untitled_counter[0] > 1 else 'Untitled page'
        sub_level = _page_sub_level(grp['title_identity'])

        # Tables first: `_resolve_table_grid` walks each table's own row/cell
        # tree through the SAME shadow-decrypted `by_id` the normal per-page
        # pipeline uses (its property values are ordinary plaintext once
        # decrypted, same as any other object's — this needed no protected-
        # specific handling at all, just wiring it in), and records which
        # cell text-run identities it consumed so they're excluded below
        # (same rule as the normal pipeline: cell text is shown in the
        # table, not also as a loose body paragraph).
        table_consumed = set()
        table_ids = list(dict.fromkeys(grp['table_identities']))
        grids = []
        for tid in table_ids:
            table_prop = by_id.get(tid)
            if not table_prop:
                continue
            grid = _resolve_table_grid(table_prop, by_id, table_consumed, list_runs)
            if grid:
                grids.append((tid, grid))

        seen_texts = set()
        runs = []
        for ident in grp['text_identities']:
            if ident in table_consumed:
                continue
            entry = _run_entry(ident)
            if entry and entry['t'] not in all_title_texts and entry['t'] not in seen_texts:
                seen_texts.add(entry['t'])
                runs.append(entry)

        file_guids = list(dict.fromkeys(grp['file_guids']))
        ink_identities = list(dict.fromkeys(grp['ink_identities']))
        decoded.append({
            'title_identity': grp['title_identity'], 'title': title, 'has_real_title': bool(real_title),
            'runs': runs, 'file_guids': file_guids, 'ink_identities': ink_identities, 'tables': grids,
            'sub_level': sub_level,
        })

    # PHASE 2: revision duplication produces more than one group for the SAME
    # logical page — sometimes literally the SAME run/file content declared
    # again near a DIFFERENT (often stale, untitled) neighboring title, not
    # just a duplicate of its OWN title. Verified against a real section:
    # "File" (with its image) and a separate "Untitled page" both carried
    # the EXACT same 4 body lines.
    #
    # Claim run text / file guids / ink identities GLOBALLY so a duplicate
    # loses whatever an earlier-processed group already claimed — BUT only
    # groups with NO real decoded title (`has_real_title=False`, a strong
    # signal they're a stale/ghost revision with no identity of their own)
    # are ever allowed to LOSE content this way. A group whose own title DID
    # decode keeps everything it structurally collected, even if that
    # duplicates another group's content — verified against a real
    # counter-example this protects against: "Background"'s window
    # structurally also contains a stale copy of "List"'s own runs (both
    # groups have real titles); richness-based claiming without this guard
    # gave the shared lines to whichever group had MORE content (Background,
    # since it also had its own unique line), which actively REMOVED "List"'s
    # only content from "List"'s own correctly-titled page — worse than the
    # pre-existing spill. Titled groups process first (claiming unconditionally,
    # richest first as a stable tiebreak) so untitled duplicates always lose to
    # a real page; untitled-vs-untitled duplicates still resolve by richness.
    def _richness(d):
        return len(d['runs']) + len(d['file_guids']) + len(d['ink_identities']) + len(d['tables'])

    claimed_texts = set()
    claimed_file_guids = set()
    claimed_ink = set()
    claimed_tables = set()
    ordered = sorted(decoded, key=lambda d: (not d['has_real_title'], -_richness(d)))
    for d in ordered:
        if d['has_real_title']:
            claimed_texts.update(entry['t'] for entry in d['runs'])
            claimed_file_guids.update(d['file_guids'])
            claimed_ink.update(d['ink_identities'])
            claimed_tables.update(tid for tid, _grid in d['tables'])
            continue
        kept_runs = []
        for entry in d['runs']:
            if entry['t'] in claimed_texts:
                continue
            claimed_texts.add(entry['t'])
            kept_runs.append(entry)
        d['runs'] = kept_runs

        kept_files = [g for g in d['file_guids'] if g not in claimed_file_guids]
        claimed_file_guids.update(kept_files)
        d['file_guids'] = kept_files

        kept_ink = [i for i in d['ink_identities'] if i not in claimed_ink]
        claimed_ink.update(kept_ink)
        d['ink_identities'] = kept_ink

        kept_tables = [(tid, grid) for tid, grid in d['tables'] if tid not in claimed_tables]
        claimed_tables.update(tid for tid, _grid in kept_tables)
        d['tables'] = kept_tables

    # PHASE 3: render surviving (non-empty, post-claim) groups, in original
    # structural order.
    image_counter = [0]
    file_counter = [0]
    results = []
    for d in decoded:
        images = []
        media = []   # (kind, PageFile) for audio/video -> inline player block
        attachments = []
        for guid in d['file_guids']:
            fmeta = files.get(guid)
            content = fmeta.get('content') if fmeta else None
            if not content:
                continue
            ext = (fmeta.get('extension') or '').lower()
            if ext in _IMAGE_EXTS:
                width, height = 320, 240
                try:
                    from PIL import Image as _PILImage
                    with _PILImage.open(io.BytesIO(content)) as im:
                        iw, ih = im.size
                    if iw:
                        width = max(40, min(iw, _MAX_IMAGE_DISPLAY_WIDTH))
                        height = max(20, round(ih * width / iw))
                except Exception:
                    pass
                image_counter[0] += 1
                images.append(PageImage(
                    ref=f'{_IMAGE_PLACEHOLDER}{image_counter[0]}', data=content, ext=ext,
                    width=width, height=height, source_width=len(content),
                ))
                continue
            file_counter[0] += 1
            pf = PageFile(
                ref=f'{_FILE_PLACEHOLDER}{file_counter[0]}', data=content,
                filename=f'attachment{file_counter[0]}{ext or ""}',
            )
            kind = _attachment_kind(ext)
            if kind == 'file':
                attachments.append(pf)
            else:
                media.append((kind, pf))

        ink_svg = (
            onenote_ink.render_ink_containers(by_id, d['ink_identities'], _norm_identity)
            if d['ink_identities'] else None
        )

        if not d['runs'] and not images and not media and not attachments and not ink_svg and not d['tables']:
            continue

        blocks = []
        for _tid, grid in d['tables']:
            blocks.append({'kind': 'table', 'html': _table_html(grid)})
        if d['runs']:
            blocks.append({'kind': 'text', 'html': _runs_to_paragraphs(d['runs'])})
        for image in images:
            blocks.append({
                'kind': 'image', 'html': _image_block_html(image.ref),
                'image_src': image.ref, 'width': image.width, 'height': image.height,
            })
        for kind, pf in media:
            blocks.append({'kind': kind, 'html': _media_block_html(kind, pf.ref), f'{kind}_src': pf.ref})
        if attachments:
            import html as _html
            links = ''.join(
                f'<p>\U0001F4CE <a href="{_html.escape(f.ref, quote=True)}">{_html.escape(f.filename)}</a></p>'
                for f in attachments
            )
            blocks.append({'kind': 'file_links', 'html': links})
        if ink_svg:
            blocks.append({'kind': 'ink', 'html': ink_svg})

        results.append((d['title_identity'], PageResult(
            title=d['title'][:100], blocks=blocks, block_count=len(blocks),
            images=images, files=attachments + [pf for _k, pf in media],
            sub_level=d['sub_level'],
        )))

    # A DIFFERENT revision-duplication shape: the exact SAME title identity
    # can still produce 2+ separately-surviving groups after phase 2 (e.g.
    # one stale generation keeps a stray run phase 2 didn't claim away) —
    # keep only the richest result per title identity.
    best_by_identity = {}
    for identity, r in results:
        existing = best_by_identity.get(identity)
        if existing is None or r.block_count > existing.block_count:
            best_by_identity[identity] = r
    return list(best_by_identity.values())


def _flat_recover_protected_pages(document, by_id: Optional[dict] = None) -> list:
    """Best-effort recovery when a protected section decrypts (password
    verified) but the normal per-page grouping can't run: that grouping
    keys runs by an ExtendedGUID resolved through TextRunFormatting's
    OIDs-stream reference, and the real OIDs stream for a protected
    object isn't reachable the same way its property VALUES are (see
    `_patches._ospropset_init`) — so `current_guid` never resolves and
    every run is dropped before it reaches a page group, even though its
    text (and, via `_patches._filedatastore_init`, embedded file bytes)
    decrypted correctly.

    Tries `_structural_page_groups` FIRST (plaintext file-position
    correlation — see its docstring; this is what actually generalizes to
    many images/pages) and only falls back to the flatter title-bucketing
    heuristic below if that finds nothing (e.g. a section shape it doesn't
    recognize). The legacy heuristic's own history, kept here because it's
    the reasoning trail for why the structural approach exists:

    Titles and body text turn out NOT to be interleaved in document order
    the way normal-page grouping assumes — empirically, on a real protected
    section, EVERY title is visited before ANY body text, so a simple
    "attach to the most-recently-seen title" heuristic put all recovered
    text under whichever title happened to come last. Byte-offset proximity
    of CONTENT (as opposed to structural declarations — a distinction that
    matters, see `_structural_page_groups`) and the object-space/gosid walk
    used for normal page ORDER were both checked against a real file too,
    and neither correlates with the right page either (gosid turned out to
    be section-granularity, not page-granularity). An embedded file's
    identity OID sometimes equals the identity OID of its real page's
    title/stamp properties, but that only held up on a single-image test
    section — on a real multi-image section only 1 of 15 file OIDs actually
    matched a title OID, so this is a coincidence, not a mechanism.
    """
    if by_id is not None:
        groups = _structural_page_groups(document)
        if groups:
            pages = _pages_from_structural_groups(document, by_id, groups)
            if pages:
                return pages

    try:
        props = document.get_json().get('properties', [])
    except Exception:
        return []

    titles = []
    seen_titles = set()
    for prop in props:
        title = prop.get('val', {}).get('CachedTitleString')
        if isinstance(title, str) and title.strip():
            t = _clean_text(decode_run_text(title)) or title.strip()
            if t not in seen_titles:
                seen_titles.add(t)
                titles.append(t)

    texts = []
    seen_texts = set()
    for prop in props:
        val = prop.get('val', {})
        if val.get('CachedTitleString'):
            continue
        raw = val.get('TextExtendedAscii')
        if raw is None:
            continue
        text = _clean_text(decode_run_text(raw))
        if not text or text in seen_texts or text in seen_titles:
            continue
        if classify_stamp(text)[0] is not None:
            continue
        seen_texts.add(text)
        texts.append(text)

    # An embedded file's identity OID -> the title of the page it actually
    # belongs to, when a property with that SAME OID carries a title (see
    # the docstring above). identity/OID strings here are already-plaintext
    # structural data, unrelated to the OIDs-stream problem everywhere else.
    title_by_oid = {}
    for prop in props:
        t = prop.get('val', {}).get('CachedTitleString')
        if not (isinstance(t, str) and t.strip()):
            continue
        guid = (_norm_identity(prop.get('identity')) or '').split(',')[0]
        if guid and guid not in title_by_oid:
            title_by_oid[guid] = _clean_text(decode_run_text(t)) or t.strip()

    images = []          # unmatched images, to distribute by the order-based fallback
    images_by_title = {}  # title -> [PageImage, ...], via the direct OID match
    image_counter = [0]
    try:
        files = document.get_files() or {}
    except Exception:
        files = {}
    for fmeta in files.values():
        content = fmeta.get('content')
        ext = (fmeta.get('extension') or '.jpg').lower()
        if not content or ext not in _IMAGE_EXTS:
            continue
        width, height = 320, 240
        try:
            from PIL import Image as _PILImage
            with _PILImage.open(io.BytesIO(content)) as im:
                iw, ih = im.size
            if iw:
                width = max(40, min(iw, _MAX_IMAGE_DISPLAY_WIDTH))
                height = max(20, round(ih * width / iw))
        except Exception:
            pass
        image_counter[0] += 1
        image = PageImage(
            ref=f'{_IMAGE_PLACEHOLDER}{image_counter[0]}', data=content, ext=ext,
            width=width, height=height, source_width=len(content),
        )
        file_oid = (_norm_identity(fmeta.get('identity')) or '').split(',')[0]
        owner_title = title_by_oid.get(file_oid)
        if owner_title:
            images_by_title.setdefault(owner_title, []).append(image)
        else:
            images.append(image)

    if not titles and not texts and not images and not images_by_title:
        return []

    if len(titles) <= 1:
        title = titles[0] if titles else 'Recovered content'
        page_defs = [{'title': title, 'images': images_by_title.get(title, []) + images, 'lines': texts}]
    else:
        remaining_images = list(images)
        page_defs = [{'title': t, 'images': list(images_by_title.get(t, [])), 'lines': []} for t in titles]
        for pg in page_defs:
            if not pg['images'] and remaining_images:
                pg['images'].append(remaining_images.pop(0))
        if remaining_images:
            page_defs[-1]['images'].extend(remaining_images)
        imageless = [pg for pg in page_defs if not pg['images']]
        if len(imageless) == 1:
            imageless[0]['lines'] = texts
        elif texts:
            page_defs.append({'title': 'Recovered content', 'images': [], 'lines': texts})

    import html as _html
    results = []
    for pg in page_defs:
        blocks = []
        if pg['lines']:
            body_html = ''.join(f'<p>{_html.escape(line)}</p>' for line in pg['lines'])
            blocks.append({'kind': 'text', 'html': body_html})
        for image in pg['images']:
            blocks.append({
                'kind': 'image', 'html': _image_block_html(image.ref),
                'image_src': image.ref, 'width': image.width, 'height': image.height,
            })
        if not blocks:
            blocks.append({'kind': 'text', 'html': '<p><em>(no content recovered)</em></p>'})
        results.append(PageResult(
            title=(pg['title'] or 'Untitled page')[:100],
            blocks=blocks,
            block_count=len(blocks),
            images=pg['images'],
        ))
    return results
