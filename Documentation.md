# Documentation

Full reference for the `onenote-tool` library and CLI. For a quick
overview, see [README.md](https://github.com/vanarebane/onenote-tool/blob/main/README.md).

## Contents

1. [Installation & extras](#installation--extras)
2. [CLI reference](#cli-reference)
3. [Python API reference](#python-api-reference)
4. [The `blocks` format](#the-blocks-format)
5. [Password handling](#password-handling)
6. [Progress-flow contract](#progress-flow-contract)
7. [Error handling](#error-handling)
8. [Constraints inherited from the implementation](#constraints-inherited-from-the-implementation)
9. [Development & testing progress](#development--testing-progress)

## Installation & extras

```bash
pip install onenote-tool          # library only: convert_onepkg, parse_one_file, ...
pip install onenote-tool[cli]     # + the `onenote-tool` console command (needs Typer)
pip install onenote-tool[dev]     # + pytest/ruff, for working on this repo itself
```

Core runtime dependencies: `pyOneNote` (does the low-level MS-ONESTORE
binary parsing this package patches heavily — see
[docs/knowledge/onenote-file-format.md](https://github.com/vanarebane/onenote-tool/blob/main/docs/knowledge/onenote-file-format.md)),
`Pillow` (image decode/resize/hashing), `cryptography` (AES for
password-protected sections).

## CLI reference

All commands are under the `onenote-tool` entry point (only installed with
the `[cli]` extra). Run `onenote-tool --help` or `onenote-tool <command>
--help` for the live, authoritative flag list — this section describes what
each one is *for*.

### `onenote-tool convert <onepkg>`

Unpacks and converts a whole `.onepkg` notebook, prints a summary (sections,
pages, per-element converted/failed counts).

- `--section-password NAME=PASSWORD` — repeatable, one per protected
  section. Every password supplied is tried against every protected
  section (not just the one it's named for) — if two sections share a
  password, you only need to give it once.
- `--work-dir DIR` — where to unpack the CAB archive (default: a fresh temp
  directory).
- `--dump-dir DIR` — writes each converted page as one flat HTML file
  (`DIR/<section>/<page title>.html`) for quick eyeballing. This is a
  **dev convenience only** — it just concatenates each block's `html` with
  no layout — not a template for how a real caller should use `page.blocks`
  (see [below](#the-blocks-format)).

### `onenote-tool unpack <onepkg> [out_dir]`

Just extracts the CAB archive and lists the `.one` section files found
inside, without parsing them. Useful for grabbing one section to feed to
`inspect` in isolation, or for confirming the CAB extraction step itself
works in your environment.

### `onenote-tool inspect <one_file>`

Parses a **single** `.one` file directly (skips CAB unpacking) — the fast
iteration loop for testing one section's recovery quality without
re-unpacking a whole notebook each time. Prints each recovered page's
title, block kinds, and image/file counts, plus the full element report.

- `--password PASSWORD` — if the section is protected.

### `onenote-tool doctor`

Environment diagnostic: confirms `pyOneNote` imported and got patched
successfully, prints dependency versions, warns if you're not on Windows
(see [known limitations](https://github.com/vanarebane/onenote-tool/blob/main/docs/knowledge/known-limitations.md)). Run this
first when something doesn't work.

## Python API reference

```python
from onenote_tool import (
    convert_onepkg, unpack_onepkg, process_sections, parse_one_file,
    ConversionResult, SectionResult, PageResult, PageImage, PageFile, PageRegion, ElementReport,
    PYONENOTE_AVAILABLE, PYONENOTE_IMPORT_ERROR, PyOneNoteUnavailable,
)
```

### `convert_onepkg(onepkg_path, work_dir=None, section_passwords=None, progress_cb=None) -> ConversionResult`

Unpack and convert a whole `.onepkg` in one call. Raises
`PyOneNoteUnavailable` if `pyOneNote` isn't installed. See
[Password handling](#password-handling) and
[Progress-flow contract](#progress-flow-contract) for `section_passwords`/
`progress_cb`.

A caller that needs to **pause between sections** (e.g. a web backend that
has to return an HTTP response and wait for the user to type a password
before continuing) should not use this — call `unpack_onepkg` +
`process_sections` directly instead (see below).

### `unpack_onepkg(onepkg_path, work_dir) -> list[str]`

Extracts the `.onepkg` CAB archive into `work_dir` (created if missing) and
returns the sorted list of absolute `.one` file paths found inside. Pass a
**persistent** `work_dir` you own (not auto-cleaned) if you're going to call
`process_sections` more than once against the same unpack.

### `process_sections(one_files, result, work_dir, section_passwords=None, sizes=None, processed_size=0, progress_cb=None, stop_at_first_unresolved=False) -> (remaining_files, blocked, processed_size)`

Parses `one_files` in order, appending a `SectionResult` to `result.sections`
for each (mutates `result` in place, so a caller can inspect partial
progress even if this stops early).

- `stop_at_first_unresolved=False` (default): every file gets processed,
  even a protected one with no matching password (it gets a placeholder
  page and a `password_status`).
- `stop_at_first_unresolved=True`: processing **stops** the instant a
  section needs a password that isn't in `section_passwords.values()`.
  Nothing is recorded for that section; it becomes the first element of
  `remaining_files` so a later call can retry once you have a candidate
  password. This is the shape a resumable, multi-request caller wants —
  see [Progress-flow contract](#progress-flow-contract).

Returns `(remaining_files, blocked, processed_size)`:
- `remaining_files`: `[]` once everything got through.
- `blocked`: `None` unless stopped early, in which case `{'name', 'status'}`
  for the one section that blocked.
- `processed_size`: running total (starting from the `processed_size`
  argument) for a resumable caller to carry forward across calls.

### `parse_one_file(path, report, passwords=None) -> (pages, password_status)`

Parses a **single** `.one` file into a list of `PageResult`. `passwords` is
a list of candidate passwords tried in order (see
[Password handling](#password-handling)). `password_status` is `None` for
an unprotected section, else one of `'needs_password'`, `'wrong_password'`,
`'decrypt_failed'` — see [Error handling](#error-handling).

### Data types (`onenote_tool.models`)

```python
@dataclass
class PageResult:
    title: str
    block_count: int
    blocks: list           # see "The blocks format" below
    created_at: datetime | None    # from the page's own date/time stamp, if any
    images: list            # list[PageImage] — the raw bytes behind any 'image' block
    files: list              # list[PageFile] — the raw bytes behind any 'file_links'/'audio'/'video' block
    sub_level: int = 0        # OneNote page indent: 0 = top level, 1 = sub-page, ...
    regions: list = []        # list[PageRegion] — see "PageResult.regions" below
    background_color: str | None = None  # '#rrggbb', or None (plain white — the common case)
    rule_lines: str | None = None        # 'narrow_lines'|'college_ruled'|'standard_rules'|
                                          # 'wide_ruled'|'small_grid'|'medium_grid'|'large_grid'|
                                          # 'very_large_grid', or None (no lines — the common case)

@dataclass
class PageImage:
    ref: str          # placeholder token embedded in a block's html — see below
    data: bytes
    ext: str            # e.g. ".jpg"
    width: int
    height: int
    source_width: int = 0
    filename: str = ''
    dhash: int | None = None   # perceptual hash, used internally for dedup

@dataclass
class PageFile:
    ref: str
    data: bytes
    filename: str
    icon_ref: str | None = None  # matches a PageImage.ref — the file's own
                                  # preview picture, if it had one

@dataclass
class PageRegion:
    x: int
    y: int
    width: int
    height: int
    blocks: list       # same shape as PageResult.blocks

@dataclass
class SectionResult:
    name: str
    pages: list             # list[PageResult]
    group_path: list         # folder path from the notebook root, e.g. ['Work', 'Archive']
    password_status: str | None

@dataclass
class ConversionResult:
    notebook_name: str
    sections: list            # list[SectionResult]
    report: ElementReport
    page_count: int            # property: sum of all sections' page counts

@dataclass
class ElementReport:
    converted: dict     # kind -> count, e.g. {'text_run': 40, 'image': 6}
    failed: dict          # kind -> count, e.g. {'image_missing_data': 2}
```

`ElementReport`'s `kind` keys are a free-form diagnostic breakdown, not a
fixed enum — treat them as human-readable labels, not something to branch
logic on (with the exception of the `password_protected_section:*` and
`section_parse_error:*` prefixes, which are stable).

### Availability

```python
onenote_tool.PYONENOTE_AVAILABLE   # bool
onenote_tool.PYONENOTE_IMPORT_ERROR  # str | None — the underlying import error, if any
onenote_tool.PyOneNoteUnavailable    # exception raised by convert_onepkg/parse_one_file when unavailable
```

Importing `onenote_tool` itself **never fails** just because `pyOneNote`
is missing or broken — you can check `PYONENOTE_AVAILABLE` up front (e.g.
for a startup health check), and every conversion function raises
`PyOneNoteUnavailable` (a `RuntimeError` subclass) if called anyway.

## The `blocks` format

`PageResult.blocks` is a list of dicts, **in reading order**, each one a
self-contained, semantic HTML fragment:

```python
{
    "kind": "text" | "image" | "audio" | "video" | "table" | "ink" | "file_links",
    "html": "<p>...</p>",   # always present — a ready-to-embed fragment
    # "image" kind only:
    "image_src": "onenote-image://3",   # matches some PageImage.ref in page.images
    "width": 512,
    "height": 384,
    # "audio" / "video" kind only:
    "audio_src": "onenote-file://5",    # matches some PageFile.ref in page.files
    "video_src": "onenote-file://6",    # (same idea, "video" kind)
}
```

This is deliberately **not** a layout format — there is no `x`/`y`/pixel
position anywhere on an individual block. OneNote's own pages are free-form
canvases (users drag text/image boxes anywhere), but `blocks` doesn't try to
reproduce that canvas: it hands you the content in reading order — a table
or a standalone image whose own document position resolves is interleaved
with the surrounding text at the spot it actually sat (e.g. above the
paragraph that introduces it, or between two others), not forced to a fixed
position — and leaves layout entirely to you. See `page.regions` below if
your application wants the canvas positions themselves. (One whose position
doesn't resolve — rare — still falls back to a fixed placement: tables
before text, images after; never dropped.)

An image/audio/video block's `html` (`<img src="...">` for images,
`<audio controls src="...">`/`<video controls src="...">` for audio/video)
references its placeholder token (`PageImage.ref` / `PageFile.ref`, e.g.
`"onenote-image://3"`) as the `src` — **your code is responsible for
uploading `PageImage.data`/`PageFile.data` somewhere and substituting the
real URL in place of that token** (a simple string replace across the
block's `html` is enough; the token is unique per page). A `file_links`
block's `html` is one `<div class="onenote-file-embed">` per file, each
wrapping a link (`href` = that `PageFile.ref`) around an optional icon
(`<img class="onenote-file-icon" src="...">`, from that file's own
`icon_ref` when it had a preview picture) and the filename — style it
however you like, or ignore the class names and just use the link/icon
`src` tokens directly. Audio/video attachments are ordinary `PageFile`s in
`page.files` — there's no dedicated jcid for either in OneNote's format, so
they're recognized by filename extension the same way any other attachment
type without its own node shape would be.

An `'ink'` block's `html` is a self-contained inline `<svg>...</svg>`
fragment (see
[docs/knowledge/onenote-file-format.md](https://github.com/vanarebane/onenote-tool/blob/main/docs/knowledge/onenote-file-format.md)
for how ink strokes are decoded) — there's nothing else to extract from it.

### `PageResult.regions` — OneNote's floating-box layout

`page.regions` is an opt-in, **additive** second view of the exact same
content, for a caller that wants to reproduce OneNote's own free-form canvas
(where the user can drag independent text/image boxes anywhere on the page)
instead of a single top-to-bottom flow:

```python
@dataclass
class PageRegion:
    x: int
    y: int
    width: int
    height: int
    blocks: list   # same per-block shape as PageResult.blocks — position lives ONLY on PageRegion
```

Each `PageRegion` is one of the page's own top-level, independently-movable
content boxes (a OneNote `OutlineNode`), with its own `x`/`y`/`width`/
`height` (OneNote's own canvas units, not necessarily 1:1 with CSS pixels)
relative to the page's top-left corner. `regions` has **no guaranteed
order** — canvas boxes have no inherent reading order; use `blocks` for
that. A page with just one content box still gets one region.

`regions` is empty when this page's box layout couldn't be resolved — most
notably, **password-protected-section recovery doesn't implement this at
all** (see [known limitations](https://github.com/vanarebane/onenote-tool/blob/main/docs/knowledge/known-limitations.md))
— so always use `blocks` for content and treat `regions` as best-effort
positioning on top of it, never the other way around.

### `PageResult.background_color` / `.rule_lines` — page-level presentation

Two plain, optional page properties — OneNote's own "Page Color" and
"Rule Lines" page-setup options. Neither is baked into `blocks` html:
whether and how to visualize a page's background color or ruled/grid
pattern (a solid CSS `background-color`, an actual rendered grid/line
pattern, or neither at all) is entirely a decision for the code calling
this library, same reasoning as `PageRegion`'s x/y/width/height above.
Both are `None` for the overwhelmingly common case (OneNote only writes
either property when a page overrides the always-plain-white/no-lines
default) — never guess a color or pattern for a page where these are
`None`, that's the correct, common, unstyled state.

## Password handling

- A section's `password_status` is `None` if it isn't protected at all.
- Pass candidate passwords via `section_passwords: dict[section_name, password]`
  to `convert_onepkg`/`process_sections`, or a plain `list[str]` to
  `parse_one_file` directly.
- **Every password given is tried against every protected section**, not
  just the one it was supplied for — if your notebook has three sections
  protected with the same password, the user only has to type it once and
  it's reused automatically for the other two.
- Passwords are used purely in memory, for the duration of one parse call,
  to derive an AES key (see
  [docs/knowledge/ms-offcrypto-encryption.md](https://github.com/vanarebane/onenote-tool/blob/main/docs/knowledge/ms-offcrypto-encryption.md)) —
  this library never logs, stores, or transmits a password anywhere itself.
- `password_status` values:
  - `None` — not protected, or successfully decrypted.
  - `'needs_password'` — protected, no candidate supplied.
  - `'wrong_password'` — protected, none of the candidates verified against
    the section's own stored password hash.
  - `'decrypt_failed'` — a candidate password **verified correct**, but no
    readable content could be recovered. Re-prompting for a (different)
    password will not help here — the password is not the problem.

Content recovery for a protected section is inherently more limited than
for a normal one — see [the progress table](#development--testing-progress)
below and
[docs/knowledge/recovery-techniques-log.md](https://github.com/vanarebane/onenote-tool/blob/main/docs/knowledge/recovery-techniques-log.md)
for exactly what is and isn't recoverable and why.

## Progress-flow contract

`progress_cb`, if given to `convert_onepkg`/`process_sections`, is called
as `progress_cb(event, payload)`:

- Once with `event='start'`, `payload={'sections': [{'name', 'size'}, ...],
  'total_size': int}` — `convert_onepkg` sends this itself, right after
  unpacking; a caller driving `process_sections` directly across multiple
  requests should send it once, up front, and never again.
- Once per section actually processed, `event='section'`,
  `payload={'name', 'size', 'status', 'processed_size'}` where `status` is
  `'ok'`, `'protected'` (was password-protected, and got resolved either
  way), or `'failed'` (`section_parse_error`). `processed_size` is a
  running cumulative total, suitable for a byte-weighted progress bar.

Exceptions raised inside your `progress_cb` are caught and logged, never
allowed to break the conversion itself.

**Building a resumable, multi-request flow** (e.g. a web backend that needs
to return an HTTP response and prompt the user for a password before
continuing, without losing progress or re-unpacking the CAB):

```python
tmp_dir = ...  # your own persistent temp directory
one_files = unpack_onepkg(onepkg_path, tmp_dir)
sizes = {p: os.path.getsize(p) for p in one_files}
result = ConversionResult(notebook_name=...)
section_passwords = {}
processed_size = 0

remaining, blocked, processed_size = process_sections(
    one_files, result, tmp_dir, section_passwords, sizes, processed_size,
    progress_cb=my_progress_cb, stop_at_first_unresolved=True,
)
# Persist `result`, `remaining`, `section_passwords`, `processed_size` and
# `tmp_dir` somewhere (a cache, a session, ...) if `blocked` is not None —
# on the next request, merge the user's new password into
# `section_passwords` and call process_sections again with `remaining` in
# place of `one_files`, the SAME `result` object, and the persisted
# `processed_size`.
```

`unpack_onepkg`'s `work_dir` is never cleaned up automatically — you own
its lifecycle (clean it up once `blocked is None`, or on your own
timeout/cancellation policy).

## Error handling

- `PyOneNoteUnavailable` (a `RuntimeError` subclass) — `pyOneNote` isn't
  installed or failed to import; check `PYONENOTE_IMPORT_ERROR` for why.
- `RuntimeError('not a valid OneNote .one file')` — `parse_one_file` was
  given something that isn't a `.one` file at all.
- `RuntimeError` from `unpack_onepkg`/CAB extraction — needs `expand.exe`
  (Windows) or `cabextract` (Linux/macOS) on `PATH`; see
  [known limitations](https://github.com/vanarebane/onenote-tool/blob/main/docs/knowledge/known-limitations.md).
- A single section failing to parse (a genuine bug, corrupt data, or a
  pyOneNote limitation like an unimplemented property type) does **not**
  abort the whole notebook — `process_sections` catches it, records
  `section_parse_error:<ExceptionType>` in the report, and leaves a visible
  one-block placeholder page in that section's place so the failure is
  never silent.
- `ElementReport.failed` is the general-purpose "what didn't make it"
  breakdown — always check it even on an apparently-successful conversion;
  a notebook can convert with zero exceptions and still have skipped
  content (an unsupported element type, a duplicate image, a missing
  embedded file).

## Constraints inherited from the implementation

- **Not thread-safe, not reentrant.** `parse_one_file` coordinates a
  password-verified re-parse through a small number of module-level
  single-slot variables (see
  [docs/knowledge/ms-offcrypto-encryption.md](https://github.com/vanarebane/onenote-tool/blob/main/docs/knowledge/ms-offcrypto-encryption.md)).
  This is safe for the intended use (parse one file at a time, one call at
  a time) but **not** safe to call concurrently from multiple threads or
  async tasks in the same process. Use multiple processes if you need
  concurrency.
- **Importing `onenote_tool` monkeypatches `pyOneNote` process-wide**, as a
  side effect of import, to fix real bugs and gaps in that library (see
  [docs/knowledge/onenote-file-format.md](https://github.com/vanarebane/onenote-tool/blob/main/docs/knowledge/onenote-file-format.md)).
  If your process imports `pyOneNote` directly for something else too, it
  will observe these patches as well.
- `pyOneNote` is version-**pinned** in `pyproject.toml` (not a `>=` range):
  the patches reach into that library's private internals by exact
  attribute path, and an upstream release could silently change or remove
  what's being patched without raising any error at all — only a pinned
  version has actually been verified against.

## Development & testing progress

This is an early-stage tool built by reverse-engineering an undocumented
binary format against real files (see
[docs/knowledge/recovery-techniques-log.md](https://github.com/vanarebane/onenote-tool/blob/main/docs/knowledge/recovery-techniques-log.md)
for the full trail). The table below tracks what's actually been verified,
not what's aspirational.

Legend: ✅ complete and verified · ⚠️ partial / best-effort · ❌ not implemented

### Table A — content types

| Element | Normal section | Password-protected section |
|---|:---:|:---:|
| Plain text / paragraphs | ✅ | ✅ |
| Headings (h1–h3) | ✅ | ⚠️ (only when the underlying style object decodes) |
| Bold / italic / underline / strikethrough | ✅ (real pyOneNote bug fixed: every `True`-valued boolean property, project-wide, was being silently dropped — see [known limitations](https://github.com/vanarebane/onenote-tool/blob/main/docs/knowledge/known-limitations.md)) | ⚠️ (same — per-run, not guaranteed) |
| Font color / highlight | ✅ | ⚠️ (same) |
| Font size / family | ✅ (only where the run explicitly overrides the paragraph default — see below) | ⚠️ (same per-run decode ceiling as other formatting) |
| Bulleted / numbered lists, incl. nesting | ✅ | ⚠️ (wired in; verified partial on a real file — ~47% of the objects needed for list membership and ~25% for ordered-vs-bullet decoded) |
| Hyperlinks (standalone + inline) | ✅ | ⚠️ (mechanism is wired in, same per-run decode ceiling as formatting) |
| Images (inline/pasted) | ✅ (real pyOneNote bug found and fixed testing a real ~250MB notebook: `get_files()` collapses multiple identities that share one underlying blob — e.g. the same photo pasted on two pages — into a single last-wins slot, silently orphaning the rest; see [known limitations](https://github.com/vanarebane/onenote-tool/blob/main/docs/knowledge/known-limitations.md). Also fixed: a standalone image between two paragraphs used to always trail after ALL of a page's text instead of its own row in place — now interleaved at its real position when it resolves) | ✅ |
| Embedded files / attachments (non-image) | ✅ (same identity-collapse fix as above. Also fixed testing real notebooks: a file had no structural page lookup at all — unlike images, it attached unconditionally to whichever page's text was seen most recently while iterating, not a real page lookup, so a PDF/audio/spreadsheet could land on the wrong, usually adjacent, page; a file's own identity guid is now preferred the same way images already do. A revision-duplicate file (same content, different identity) also no longer imports as two copies — exact `(filename, content)` equality is deduped. A file's own preview picture, if it had one, is now linked to its card via `PageFile.icon_ref` instead of showing up as an unrelated standalone image) | ✅ |
| Audio recordings | ✅ (inline `<audio>` player) | ✅ (verified against real .wav/.ogg attachments — extension classification, not page attribution, is what's proven here; a file can still land on the wrong page under the pre-existing structural-clustering spillover) |
| Video attachments | ✅ (inline `<video>` player) | ✅ (same — verified against real .mp4/.webm/.wmv attachments) |
| Tables | ✅ (correct cell placement; cells render with the same formatting/alignment/lists/background-color as body text — see below; a real bug fixed testing real pages: a table used to always render before ALL of a page's text regardless of where it actually sat — e.g. above the paragraph that introduced it — `blocks` now interleaves it at its real document position when that position resolves) | ❌ (wired in, but every `jcidTableNode` in the test file decoded to a completely empty property set — 0/6 across both protected sections tested; see [known limitations](https://github.com/vanarebane/onenote-tool/blob/main/docs/knowledge/known-limitations.md)) |
| Merged table cells | ✅ (a real, undocumented-by-pyOneNote structural property found via raw property scanning — not a content heuristic; renders as real `<td colspan>`, matching the MHT ground truth — see [known limitations](https://github.com/vanarebane/onenote-tool/blob/main/docs/knowledge/known-limitations.md)) | ❌ |
| Images inside a table cell | ✅ (verified against a real cell; two real bugs — double-extraction and a cross-cell image leak — found and fixed along the way, see [known limitations](https://github.com/vanarebane/onenote-tool/blob/main/docs/knowledge/known-limitations.md)) | ❌ (protected tables never decode at all — see above) |
| Table-level styling (column widths, border visibility) | ✅ (baked into the table's own HTML as a `<colgroup>` + border styling, same inline-CSS convention as cell color/alignment; "autofit vs. fixed width" needs no separate handling — both just resolve to concrete column widths — see [known limitations](https://github.com/vanarebane/onenote-tool/blob/main/docs/knowledge/known-limitations.md)) | ❌ |
| Ink / hand-drawn drawings | ✅ | ⚠️ (collected structurally; stroke decode has the same partial-decode ceiling) |
| YouTube embeds (thumbnail-matched) | ✅ | ⚠️ (embed itself wired in and renders correctly; thumbnail pre-matching not attempted; unverified against real decoded data — see [known limitations](https://github.com/vanarebane/onenote-tool/blob/main/docs/knowledge/known-limitations.md)) |
| Paragraph text alignment (left/center/right) | ✅ | ⚠️ (same per-run decode ceiling as other formatting) |
| Floating-box canvas layout (`page.regions`) | ✅ (position/size per top-level content box, verified against a real 6-box page; a real bug found testing more real notebooks — a run's own guid is its OUTLINE BOX's guid, not always the page's, silently leaving `regions` empty on any page with more than one box — is now cross-referenced and fixed for every page where the file's own structure has a path to correct it; see [known limitations](https://github.com/vanarebane/onenote-tool/blob/main/docs/knowledge/known-limitations.md)) | ❌ (tried directly against a real protected copy of that same 6-box page — the 5-level reference chain needed never decoded intact for even one page; see [known limitations](https://github.com/vanarebane/onenote-tool/blob/main/docs/knowledge/known-limitations.md)) |
| Page titles | ⚠️ (a renamed page can leave both its old and new title text under the same identity with no reliable signal which is current — `page_title_by_guid`'s first-occurrence-wins picks correctly on some pages, stale on others, same file; see [known limitations](https://github.com/vanarebane/onenote-tool/blob/main/docs/knowledge/known-limitations.md)) | ⚠️ (commonly ~50–100% depending on the file; some titles are never recoverable at all) |
| Page order within a section | ✅ | ⚠️ (structural file-position order used as a proxy, not verified against OneNote's own page-series order) |
| Section groups / folders | ✅ | ✅ (comes from the `.onepkg` archive structure, unaffected by a section's own encryption) |
| Date/time auto-stamps → page `created_at` | ✅ | ✅ |
| Page indent level (sub-pages) | ✅ (a real bug — the same outline-box-guid-vs-page-guid mismatch as `page.regions` above — was silently resetting a page's indent to 0; fixed by the same cross-reference) | ⚠️ (reads the same `PageLevel` property as the normal pipeline; verified recovering a real secondary-level page — bounded by the usual per-object decode ceiling, so a page whose own `PageMetaData` didn't decode still defaults to top-level) |
| Revision-duplicate cleanup (no ghost/duplicate pages) | ✅ (a real bug fixed testing a real notebook: a revision-duplicate declaration inside a table could produce an entire spurious extra *page* — not just duplicate content within one — holding a second copy of that table under a stale/"Untitled" title; the same outline-box cross-reference above merges it back into the real page. Not fully closed: an outline box with no structural path back to ANY page — not just the wrong one — still can't be recovered this way, and remains a known, documented residual case) | ⚠️ (actively handled with a dedicated dedup pass; some spillover cases are documented, not all eliminated) |
| Embedded Office files (Excel, PDF, 3D models, generic attachments) | ✅ (no dedicated handling needed — the same generic attachment path already covering .zip/.wav/etc.; a real revision-store identity mismatch found and fixed along the way, see [known limitations](https://github.com/vanarebane/onenote-tool/blob/main/docs/knowledge/known-limitations.md)) | ⚠️ (same per-object decode ceiling as everything else in a protected section) |
| Math equations (OneNote's inline equation editor) | ⚠️ (detected and replaced with an honest placeholder — the underlying Unicode "linear format" control characters aren't laid out into a real formula, and OneNote's own MHT export doesn't do this either, it rasterizes to an image; see [known limitations](https://github.com/vanarebane/onenote-tool/blob/main/docs/knowledge/known-limitations.md)) | ❌ (same detection mechanism, untested against protected content) |
| Page background color / rule-lines pattern | ✅ (`PageResult.background_color` / `.rule_lines` — plain page-level properties, not baked into `blocks` html; rendering/visualizing them is left to the caller. Verified against 13 real pages, one per built-in pattern plus color variants — see [known limitations](https://github.com/vanarebane/onenote-tool/blob/main/docs/knowledge/known-limitations.md)) | ❌ (untested against protected content) |
| Rule-line **color** (separate from the pattern/page-color above — OneNote's own "Rule Line Colour" picker) | ❌ (real, independent per-page setting, confirmed on 2 real test pages — exhaustively searched (full property-key diff + whole-file unnamed-property scan) and not found anywhere in the page's own object space; not guessed at, left as documented future work — see [known limitations](https://github.com/vanarebane/onenote-tool/blob/main/docs/knowledge/known-limitations.md)) | ❌ |
| Visio objects, page tags ("Important"/"To Do") | ❌ (never tested — not in `Test.onepkg`; falls back to the generic lossy-by-design placeholder + `ElementReport.failed`, not silently dropped, but no dedicated handling exists) | ❌ (same, plus the usual protected-section decode ceiling) |
| Per-author attribution (who wrote what, on a notebook shared between multiple users) | ✅ (paragraph granularity, not per-run — that's the finest granularity OneNote itself tracks; a real display name resolved via `AuthorOriginal` -> `jcidReadOnlyPersistablePropertyContainerForAuthor`, emitted as `data-author="..."` on the paragraph's own tag, including a table cell's own paragraphs (same run type, same resolution, verified: 1225/1394 real table paragraph tags on the same file). Verified against a real 66-section/1131-page multi-author shared notebook: resolves for 99.9% of all real body-text runs (72293/72339); the remainder gets no author tag at all, never a guessed one — see [known limitations](https://github.com/vanarebane/onenote-tool/blob/main/docs/knowledge/known-limitations.md)) | ❌ (same per-object decode ceiling as everything else in a protected section, worse than most: tried against a real protected copy, only 38/602 outline elements even carried the `AuthorOriginal` reference at all, and 0 of those resolved to a decoded author name) |

Table A tracks every content type this project has actually built and
verified against real files — it is not a claim that every OneNote feature
exists. The last row above lists what's known to be genuinely out of scope
today; anything not in this table at all (any OneNote feature not
mentioned anywhere above) hasn't been evaluated either way.

Font size/family is deliberately **not** mapped from `ParagraphStyle` (the
paragraph's own default, present on essentially every run) — only from a run's
own `TextRunFormatting` override, matching what OneNote itself actually writes
(it only ever sets a run-level `FontSize`/`Font` when that run's font differs
from the paragraph's default). Mapping the always-present paragraph default
too would wrap every single paragraph in a same-as-default `font-size`/
`font-family` span for no visible difference.

Paragraph alignment maps to a `style="text-align:..."` attribute on the
paragraph's own `<p>`/`<h#>`/list-item tag (not a nested `<span>`, since
alignment is a paragraph-level property, not a per-character one). Only
`center` and `right` are currently recognized — OneNote only writes
`ParagraphAlignment` at all when a paragraph isn't left-aligned (its
unwritten default), and no real `justify` value has been observed yet to
confirm what byte it uses, so an unrecognized value is left unstyled
(effectively left-aligned) rather than guessed at.

A table cell's own content gets exactly the same treatment as page body
text — bold/italic/underline/strike/color/highlight/font-size/font-family,
paragraph alignment, bulleted/numbered lists, and a pasted-in image all
render inside a cell the same way they would outside one (an image's bytes
still go through `page.images` like any other image — the same
placeholder-token/`ref` contract, just referenced from inside the cell's
own `html` instead of a top-level `image` block). A cell's own background
(`CellShadingColor`) becomes `style="background-color:..."` on the `<td>`
itself. Merged cells resolve to a real `colspan` on the surviving `<td>`
(the absorbed cell is dropped) — a genuine structural property found by
scanning every raw property ID on a real merged cell rather than one this
tool could look up by name (see
[known limitations](https://github.com/vanarebane/onenote-tool/blob/main/docs/knowledge/known-limitations.md)
for how it was found). Table-level styling — column widths and
border-visibility — is baked into the table's own `<table>`/`<colgroup>`
markup, the same additive-inline-CSS approach as everything above; no new
`blocks` schema field was needed for any of this. `TableColumnsLocked`
(an editing-lock bitmask, not visible content) is deliberately not exposed
— see known limitations for why.

### Table B — OneNote versions tested against

| OneNote version | Notes |
|---|---|
| Latest OneNote (Microsoft 365, Copilot-integrated build) | The only version test files have been generated with so far. |

*(Add a row here each time a notebook exported from a different OneNote
version/build is verified against this tool — file format details have
historically shifted between OneNote generations, and nothing above should
be assumed to generalize until it's actually been checked.)*
