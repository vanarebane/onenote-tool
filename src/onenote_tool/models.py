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


@dataclass
class PageResult:
    title: str
    block_count: int
    # Neutral content blocks, in reading order — see Documentation.md's
    # "blocks format" section for the full contract. Each dict has at least
    # {'kind': 'text'|'image'|'table'|'ink'|'file_links', 'html': str}; an
    # 'image' block additionally carries 'image_src'/'width'/'height'.
    # Deliberately has NO position/layout information (no x/y/width-as-
    # pixels) — that's presentation, left to the caller.
    blocks: list = field(default_factory=list)
    created_at: Optional[datetime] = None  # parsed from the page's date stamp
    images: list = field(default_factory=list)  # list[PageImage] to upload
    files: list = field(default_factory=list)  # list[PageFile] to upload (non-image attachments)
    sub_level: int = 0  # OneNote PageLevel-1 (0 = top level, 1 = sub-page, ...)


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
