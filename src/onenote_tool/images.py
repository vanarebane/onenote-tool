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
_IMAGE_PLACEHOLDER = 'onenote-image://'  # caller replaces with the uploaded URL
_FILE_PLACEHOLDER = 'onenote-file://'    # caller replaces with the uploaded URL (non-image attachments)

# A displayed image's reported width is capped at this many pixels (OneNote's
# own LayoutMax*/Picture* properties can report arbitrarily large values);
# height is then recomputed proportionally. This cap is a judgment call
# inherited unchanged from the tool this package was extracted from — it
# does NOT affect the stored image bytes, only the width/height numbers
# reported on `PageImage`, which a caller is free to ignore or override.
_MAX_IMAGE_DISPLAY_WIDTH = 600


def _image_dhash(data: bytes) -> Optional[int]:
    """64-bit difference hash of an image, or None if it can't be decoded."""
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(data)).convert('L').resize((9, 8))
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
    width = _int(val.get('LayoutMaxWidth'), 0) or _int(val.get('PictureWidth'), 320)
    height = _int(val.get('LayoutMaxHeight'), 0) or _int(val.get('PictureHeight'), 240)
    width = max(40, min(width, _MAX_IMAGE_DISPLAY_WIDTH))
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
