"""Plain data types returned by the conversion pipeline.

No behavior lives here — just the shapes `parser.parse_one_file` and
`convert.convert_onepkg`/`process_sections` build and return.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class ElementReport:
    """Per-kind conversion tallies. `kind` strings are freeform (e.g.
    `'text_run'`, `'image_duplicate'`, `'password_protected_section:wrong_password'`)
    — treat them as a diagnostic breakdown, not a fixed enum."""
    converted: dict = field(default_factory=dict)
    failed: dict = field(default_factory=dict)

    def ok(self, kind: str, n: int = 1) -> None:
        self.converted[kind] = self.converted.get(kind, 0) + n

    def fail(self, kind: str, n: int = 1) -> None:
        self.failed[kind] = self.failed.get(kind, 0) + n


@dataclass
class PageImage:
    ref: str          # placeholder token embedded in block html until the caller uploads it
    data: bytes       # raw image bytes
    ext: str          # file extension incl. dot, e.g. ".jpg"
    width: int
    height: int
    source_width: int = 0  # intrinsic pixel width; used to prefer the larger duplicate
    filename: str = ''     # original OneNote filename (used to spot link thumbnails)
    dhash: Optional[int] = None  # perceptual hash for dedup + youtube-thumbnail matching


@dataclass
class PageFile:
    ref: str          # placeholder token embedded in block html until the caller uploads it
    data: bytes       # raw file bytes
    filename: str     # original OneNote filename (shown as the download link text)
    # A jcidEmbeddedFileNode carries its own preview picture (PictureContainer)
    # alongside the real attached file (EmbeddedFileContainer) — when present,
    # this is that picture's own PageImage.ref (same placeholder-token contract,
    # already included in PageResult.images so the caller uploads it like any
    # other image). None when the node had no extractable preview.
    icon_ref: Optional[str] = None


@dataclass
class PageRegion:
    """One of a page's independently-movable "floating boxes" on OneNote's
    free-form canvas (a top-level `jcidOutlineNode`), with its own position/
    size and the subset of the page's content structurally inside it. `x`/
    `y`/`width`/`height` are OneNote's own canvas units (not necessarily
    pixels 1:1) relative to the page's top-left. `blocks` uses the exact
    same per-block shape as `PageResult.blocks` — position lives only on the
    `PageRegion` container, never on an individual block."""
    x: int
    y: int
    width: int
    height: int
    blocks: list = field(default_factory=list)


@dataclass
class PageResult:
    title: str
    block_count: int
    # Neutral content blocks, in reading order — see Documentation.md's
    # "blocks format" section for the full contract. Each dict has at least
    # {'kind': 'text'|'image'|'audio'|'video'|'table'|'ink'|'file_links', 'html': str};
    # an 'image'/'audio'/'video' block additionally carries 'image_src'/
    # 'audio_src'/'video_src' (+ 'width'/'height' for 'image').
    # Deliberately has NO position/layout information (no x/y/width-as-
    # pixels) — that's presentation, left to the caller. See `regions` below
    # for the opt-in, position-aware alternative view of the same content.
    blocks: list = field(default_factory=list)
    created_at: Optional[datetime] = None  # parsed from the page's date stamp
    images: list = field(default_factory=list)  # list[PageImage] to upload
    files: list = field(default_factory=list)  # list[PageFile] to upload (non-image attachments)
    sub_level: int = 0  # OneNote PageLevel-1 (0 = top level, 1 = sub-page, ...)
    # OneNote's own floating-box layout, one PageRegion per top-level content
    # box, in no particular guaranteed order (canvas boxes have no inherent
    # reading order — use `blocks` for that). Empty when this page's layout
    # couldn't be resolved (e.g. protected-content recovery, which doesn't
    # implement this) — always check `blocks` for content; `regions` is a
    # purely additive, best-effort second view of the SAME content, not a
    # replacement.
    regions: list = field(default_factory=list)  # list[PageRegion]
    # Page-level presentation metadata, not content — deliberately not baked
    # into any block's html (same reasoning as `regions`' x/y/width/height):
    # whether/how to visualize a page's background color or ruled/grid
    # pattern is a choice for the caller, not this library. None/None for
    # the overwhelmingly common case (OneNote only writes either property
    # when it overrides the always-plain-white/no-lines default).
    background_color: Optional[str] = None  # '#rrggbb' or None
    # One of: 'narrow_lines', 'college_ruled', 'standard_rules', 'wide_ruled',
    # 'small_grid', 'medium_grid', 'large_grid', 'very_large_grid', or None.
    rule_lines: Optional[str] = None
    # Color of the rule lines ('#rrggbb'), set whenever `rule_lines` is —
    # OneNote's default is '#caebfd' (light blue).
    rule_line_color: Optional[str] = None


@dataclass
class SectionResult:
    name: str
    pages: list = field(default_factory=list)  # list[PageResult]
    # Section-group folder path from the notebook root ([] = top level).
    group_path: list = field(default_factory=list)
    # Password-protected section state, for the caller to prompt/retry with:
    # None = not protected, 'needs_password' = protected, none supplied,
    # 'wrong_password' = supplied password didn't verify, 'decrypt_failed' =
    # password verified but no content could be recovered (retrying won't help).
    password_status: Optional[str] = None


@dataclass
class ConversionResult:
    notebook_name: str
    sections: list = field(default_factory=list)  # list[SectionResult]
    report: ElementReport = field(default_factory=ElementReport)

    @property
    def page_count(self) -> int:
        return sum(len(s.pages) for s in self.sections)
