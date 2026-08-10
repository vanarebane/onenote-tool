# Documentation

Full reference for the `onenote-tool` library and CLI. For a quick
overview, see [README.md](README.md).

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
[docs/knowledge/onenote-file-format.md](docs/knowledge/onenote-file-format.md)),
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
(see [known limitations](docs/knowledge/known-limitations.md)). Run this
first when something doesn't work.

## Python API reference

```python
from onenote_tool import (
    convert_onepkg, unpack_onepkg, process_sections, parse_one_file,
    ConversionResult, SectionResult, PageResult, PageImage, PageFile, ElementReport,
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
    files: list              # list[PageFile] — the raw bytes behind any 'file_links' block
    sub_level: int = 0        # OneNote page indent: 0 = top level, 1 = sub-page, ...

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
    "kind": "text" | "image" | "table" | "ink" | "file_links",
    "html": "<p>...</p>",   # always present — a ready-to-embed fragment
    # "image" kind only:
    "image_src": "onenote-image://3",   # matches some PageImage.ref in page.images
    "width": 512,
    "height": 384,
}
```

This is deliberately **not** a layout format — there is no `x`/`y`/pixel
position anywhere. OneNote's own pages are free-form canvases (users drag
text/image boxes anywhere), but this library doesn't try to reproduce that
canvas: it hands you the content, in the order OneNote itself stores it,
and leaves layout entirely to you. If your application wants OneNote's
original floating-box positions, canvas coordinates aren't currently
exposed by this library at all — see
[known limitations](docs/knowledge/known-limitations.md).

An image or file block's `html` (`<img src="...">` for images; a list of
`<p>📎 <a href="...">name</a></p>` links for `file_links`) references its
placeholder token (`PageImage.ref` / `PageFile.ref`, e.g.
`"onenote-image://3"`) as the `src`/`href` — **your code is responsible for
uploading `PageImage.data`/`PageFile.data` somewhere and substituting the
real URL in place of that token** (a simple string replace across the
block's `html` is enough; the token is unique per page).

An `'ink'` block's `html` is a self-contained inline `<svg>...</svg>`
fragment (see
[docs/knowledge/onenote-file-format.md](docs/knowledge/onenote-file-format.md)
for how ink strokes are decoded) — there's nothing else to extract from it.

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
  [docs/knowledge/ms-offcrypto-encryption.md](docs/knowledge/ms-offcrypto-encryption.md)) —
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
[docs/knowledge/recovery-techniques-log.md](docs/knowledge/recovery-techniques-log.md)
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
  [known limitations](docs/knowledge/known-limitations.md).
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
  [docs/knowledge/ms-offcrypto-encryption.md](docs/knowledge/ms-offcrypto-encryption.md)).
  This is safe for the intended use (parse one file at a time, one call at
  a time) but **not** safe to call concurrently from multiple threads or
  async tasks in the same process. Use multiple processes if you need
  concurrency.
- **Importing `onenote_tool` monkeypatches `pyOneNote` process-wide**, as a
  side effect of import, to fix real bugs and gaps in that library (see
  [docs/knowledge/onenote-file-format.md](docs/knowledge/onenote-file-format.md)).
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
[docs/knowledge/recovery-techniques-log.md](docs/knowledge/recovery-techniques-log.md)
for the full trail). The table below tracks what's actually been verified,
not what's aspirational.

Legend: ✅ complete and verified · ⚠️ partial / best-effort · ❌ not implemented

### Table A — content types

| Element | Normal section | Password-protected section |
|---|:---:|:---:|
| Plain text / paragraphs | ✅ | ✅ |
| Headings (h1–h3) | ✅ | ⚠️ (only when the underlying style object decodes) |
| Bold / italic / underline / strikethrough | ✅ | ⚠️ (same — per-run, not guaranteed) |
| Font color / highlight | ✅ | ⚠️ (same) |
| Font size / family | ❌ (not mapped to output) | ❌ |
| Bulleted / numbered lists, incl. nesting | ✅ | ❌ (not reconstructed by protected-content recovery) |
| Hyperlinks (standalone + inline) | ✅ | ⚠️ (mechanism is wired in, same per-run decode ceiling as formatting) |
| Images (inline/pasted) | ✅ | ✅ |
| Embedded files / attachments (non-image) | ✅ | ✅ |
| Tables | ✅ (correct cell placement) | ❌ (not reconstructed by protected-content recovery) |
| Ink / hand-drawn drawings | ✅ | ⚠️ (collected structurally; stroke decode has the same partial-decode ceiling) |
| YouTube embeds (thumbnail-matched) | ✅ | ❌ |
| Page titles | ✅ | ⚠️ (commonly ~50–100% depending on the file; some titles are never recoverable at all) |
| Page order within a section | ✅ | ⚠️ (structural file-position order used as a proxy, not verified against OneNote's own page-series order) |
| Section groups / folders | ✅ | ✅ (comes from the `.onepkg` archive structure, unaffected by a section's own encryption) |
| Date/time auto-stamps → page `created_at` | ✅ | ✅ |
| Page indent level (sub-pages) | ✅ | ❌ (protected-content recovery doesn't set this; always 0) |
| Revision-duplicate cleanup (no ghost/duplicate pages) | ✅ | ⚠️ (actively handled with a dedicated dedup pass; some spillover cases are documented, not all eliminated) |

### Table B — OneNote versions tested against

| OneNote version | Notes |
|---|---|
| Latest OneNote (Microsoft 365, Copilot-integrated build) | The only version test files have been generated with so far. |

*(Add a row here each time a notebook exported from a different OneNote
version/build is verified against this tool — file format details have
historically shifted between OneNote generations, and nothing above should
be assumed to generalize until it's actually been checked.)*
