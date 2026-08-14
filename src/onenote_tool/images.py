"""Image/attachment extraction, dedup, and YouTube-thumbnail matching."""
from __future__ import annotations

import io
import re
import urllib.request
from typing import Optional

from .models import PageFile, PageImage
from .rendering import _RTU_MARKER
from .text import _clean_text, _extract_guid

_YOUTUBE_ID_RE = re.compile(
    r'(?:youtube\.com/(?:watch\?(?:.*&)?v=|embed/|shorts/|v/)|youtu\.be/)([A-Za-z0-9_-]{11})',
    re.IGNORECASE,
)

_IMAGE_TYPES = {'jcidImageNode'}
_IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp'}
# Audio recordings (e.g. OneNote's own "Record Audio") and video files arrive
# as an ordinary embedded-file attachment (`_build_embedded_file`) — there's
# no dedicated jcid for either, just a filename extension to go on, same as
# any other attachment type this package doesn't have a specific node shape
# for.
_AUDIO_EXTS = {'.mp3', '.wav', '.m4a', '.aac', '.ogg', '.oga', '.opus', '.wma', '.flac'}
_VIDEO_EXTS = {'.mp4', '.webm', '.mov', '.avi', '.wmv', '.mkv', '.m4v'}
_IMAGE_PLACEHOLDER = 'onenote-image://'  # caller replaces with the uploaded URL
_FILE_PLACEHOLDER = 'onenote-file://'    # caller replaces with the uploaded URL (non-image attachments)


def _attachment_kind(ext: str) -> str:
    """Classify a non-image attachment's extension for block rendering:
    'audio'/'video' get an inline HTML5 player block, anything else falls
    back to a plain download link (`'file'`)."""
    ext = (ext or '').lower()
    if ext in _AUDIO_EXTS:
        return 'audio'
    if ext in _VIDEO_EXTS:
        return 'video'
    return 'file'

# A displayed image's reported width is capped at this many pixels; height is
# then recomputed proportionally so capping never distorts the aspect ratio.
# This does NOT affect the stored image bytes, only the width/height numbers
# reported on `PageImage`, which a caller is free to ignore or override.
#
# This used to be 600 — a judgment call inherited unchanged from the tool
# this package was extracted from, never verified against real data, and
# actively wrong: two independent real notebooks confirm `LayoutMaxWidth`/
# `LayoutMaxHeight` match OneNote's own MHT export pixel-for-pixel (a PDF
# preview at 793x1122; a plain pasted photo at 972x1258 — different content
# types, both exact matches, no discrepancy either time). That's real
# evidence this property is trustworthy, not evidence it needs shrinking to
# a "typical" size — the 600 (and briefly, while investigating this, 900)
# ceilings were both still silently shrinking legitimate, correctly-sized
# real content (confirmed: real pasted screenshots in the wild routinely
# store `LayoutMaxWidth` up to ~2400px). Raised to a genuine safety-net
# value instead — one meant to catch an actually-pathological stored value,
# not to second-guess an ordinary large image.
_MAX_IMAGE_DISPLAY_WIDTH = 4000


def _image_dhash(data: bytes) -> Optional[int]:
    """64-bit difference hash of an image, or None if it can't be decoded."""
    try:
        from PIL import Image
        # RGBA first, then L: a paletted image with transparency converted
        # straight to 'L' triggers a Pillow deprecation warning (a future
        # version will stop implicitly compositing it) — compositing
        # through RGBA first is the well-defined path either way. Only
        # affects this perceptual hash, never the stored image bytes.
        im = Image.open(io.BytesIO(data)).convert('RGBA').convert('L').resize((9, 8))
        px = list(im.getdata())
        bits = 0
        k = 0
        for row in range(8):
            for col in range(8):
                if px[row * 9 + col] > px[row * 9 + col + 1]:
                    bits |= (1 << k)
                k += 1
        return bits
    except Exception:
        return None


def _dham(a, b) -> int:
    if a is None or b is None:
        return 999
    return bin(a ^ b).count('1')


# Two images are the "same" (for dedup) when their content hashes are close;
# when hashing is unavailable, same filename is assumed to mean the same image.
_DHASH_SAME = 8         # <= => same picture (revision repeat / web variant)
_DHASH_THUMB_MATCH = 8  # <= => an image IS the YouTube video's thumbnail


def _same_image(a: PageImage, b: PageImage) -> bool:
    if a.dhash is not None and b.dhash is not None:
        return _dham(a.dhash, b.dhash) <= _DHASH_SAME
    return True


_yt_thumb_hash_cache: dict = {}


def _youtube_thumb_hashes(video_id: str):
    """Fetch a YouTube video's thumbnails and hash them (cached). [] on
    failure — this is the one network call in the whole package, and it's
    always optional/best-effort: without it, a YouTube embed just loads its
    thumbnail from YouTube client-side instead of getting matched to an
    already-embedded preview image."""
    if video_id in _yt_thumb_hash_cache:
        return _yt_thumb_hash_cache[video_id]
    hashes = []
    for variant in ('maxresdefault', 'mqdefault', 'hqdefault'):
        try:
            url = f'https://img.youtube.com/vi/{video_id}/{variant}.jpg'
            req = urllib.request.Request(url, headers={'User-Agent': 'onenote-tool'})
            with urllib.request.urlopen(req, timeout=5) as resp:
                h = _image_dhash(resp.read())
            if h is not None:
                hashes.append(h)
        except Exception:
            continue
    _yt_thumb_hash_cache[video_id] = hashes
    return hashes


def _youtube_id(url: str):
    m = _YOUTUBE_ID_RE.search(url or '')
    return m.group(1) if m else None


def _youtube_from_rtu(rtu: str):
    """If the run is a standalone YouTube hyperlink, return {videoId,url,title}."""
    s = str(rtu).replace('\x00', '')
    segments = s.split(_RTU_MARKER)
    if (segments[0] or '').strip():   # leading plain text -> not standalone
        return None
    links = segments[1:]
    if len(links) != 1:
        return None
    m = re.match(r'\s*HYPERLINK\s+"([^"]*)"(.*)', links[0], re.DOTALL)
    if not m:
        return None
    url, disp = m.group(1), _clean_text(m.group(2))
    vid = _youtube_id(url)
    if not vid:
        return None
    return {'videoId': vid, 'url': url, 'title': disp}


def _build_page_image(val, files_by_identity, image_counter, report):
    """Build a PageImage from a jcidImageNode's properties, or None."""
    identity = _extract_guid(val.get('PictureContainer'))
    fmeta = files_by_identity.get(identity)
    if not fmeta or not fmeta.get('content'):
        report.fail('image_missing_data')
        return None
    ext = (fmeta.get('extension') or '.png').lower()
    if ext not in _IMAGE_EXTS:
        report.fail(f'image_ext{ext}')
        return None

    def _int(v, default):
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    # LayoutMax* is the on-page display size; fall back to the intrinsic size.
    width_raw = _int(val.get('LayoutMaxWidth'), 0) or _int(val.get('PictureWidth'), 320)
    height = _int(val.get('LayoutMaxHeight'), 0) or _int(val.get('PictureHeight'), 240)
    width = max(40, min(width_raw, _MAX_IMAGE_DISPLAY_WIDTH))
    if width_raw > _MAX_IMAGE_DISPLAY_WIDTH:
        # Real bug found and fixed: capping `width` alone without rescaling
        # `height` to match distorts the image (and, worse, can make it
        # come back visibly SMALLER than OneNote's own real display size —
        # confirmed against a real embedded PDF preview: OneNote's own MHT
        # export renders it at 793x1122, this used to report 600x1122,
        # neither the real size nor the real aspect ratio). `recovery.py`'s
        # own two image-sizing call sites already rescale height the same
        # way when capping an intrinsic pixel width; this brings the normal
        # pipeline's LayoutMax*-based sizing in line with that.
        height = round(height * width / width_raw)
    source_width = _int(val.get('PictureWidth'), 0) or len(fmeta['content'])
    filename = str(val.get('ImageFilename') or '').replace('\x00', '').strip()
    image_counter[0] += 1
    ref = f'{_IMAGE_PLACEHOLDER}{image_counter[0]}'
    return PageImage(
        ref=ref, data=fmeta['content'], ext=ext, width=width,
        height=max(20, height), source_width=source_width, filename=filename,
        dhash=_image_dhash(fmeta['content']),
    )


def _build_embedded_file(val, files_by_identity, file_counter, consumed_file_idents, report):
    """Build a PageFile from a jcidEmbeddedFileNode's REAL attached file, or None.

    An embedded file node carries two distinct references: `PictureContainer`
    (a preview thumbnail — often a raster image, sometimes an unsupported
    vector format like .emf) and `EmbeddedFileContainer` (the actual attached
    file — e.g. the real .xlsx/.zip bytes). `_build_page_image` only ever reads
    `PictureContainer`, so the real file needs its own read here — attach it
    as a downloadable link regardless of whether a preview exists.
    """
    identity = _extract_guid(val.get('EmbeddedFileContainer'))
    fmeta = files_by_identity.get(identity)
    if fmeta is None and identity:
        # Real revision-store mismatch, verified on a real file: this
        # reference can point at a STALE revision of the same base object —
        # `get_files()` (pyOneNote) only keeps one, its own last-wins,
        # `ObjectDeclarationFileData3RefCountFND` per real file, which can
        # be a LATER revision than whatever this particular
        # `EmbeddedFileContainer` reference was captured against (a real
        # PDF: the reference pointed at revision ",33", the actual content
        # ended up recorded under ",40" of the same base GUID). Fall back to
        # matching by base GUID (ignoring the revision counter) — but ONLY
        # when that resolves to exactly one non-image candidate; a base GUID
        # groups every object declared in the same revision-manifest batch,
        # not just one file's own revisions (verified: a page with two real
        # audio attachments has both sharing one base GUID), so with 0 or
        # 2+ candidates this doesn't guess, it just leaves the normal
        # unresolved-file failure path below to report it, same as before.
        base = identity.split(',')[0]
        candidates = [
            (k, v) for k, v in files_by_identity.items()
            if k.split(',')[0] == base and (v.get('extension') or '').lower() not in _IMAGE_EXTS
        ]
        if len(candidates) == 1:
            resolved_identity, fmeta = candidates[0]
            # The end-of-parse orphan-file check (parser.py) walks the raw
            # `get_files()` dict and only skips an entry whose OWN identity
            # is in `consumed_file_idents` — mark that one too, not just the
            # stale reference we were actually given, or this still gets
            # double-reported as an unresolved failure despite having just
            # been attached to a page correctly.
            consumed_file_idents.add(resolved_identity)
    if not fmeta or not fmeta.get('content'):
        return None
    filename = str(val.get('EmbeddedFileName') or '').replace('\x00', '').strip()
    if not filename:
        ext = (fmeta.get('extension') or '').lower()
        filename = f'attachment{ext or ""}'
    consumed_file_idents.add(identity)
    file_counter[0] += 1
    ref = f'{_FILE_PLACEHOLDER}{file_counter[0]}'
    report.ok('embedded_file')
    return PageFile(ref=ref, data=fmeta['content'], filename=filename)
