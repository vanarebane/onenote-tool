# Changelog

All notable changes to this project are documented here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/); versions match
`pyproject.toml`.

## [0.1.3] - 2026-08-13

### Added
- Font size/family mapping — emitted only where a run's `TextRunFormatting`
  actually overrides the paragraph's own default, matching how OneNote
  itself only writes the property when it differs.
- Audio and video content types (`kind: 'audio'`/`'video'` blocks), detected
  by attachment extension and rendered as inline HTML5 players.
- Floating-box canvas layout: `PageResult.regions`, one `PageRegion` (with
  real `x`/`y`/`width`/`height`) per top-level, independently-movable
  content box, as an additive second view alongside `blocks`.
- Paragraph text alignment (left/center/right).
- Table-cell rich formatting: bold/italic/underline/strike/color/highlight/
  font-size/font-family/alignment/lists/pasted images now render inside a
  cell exactly like page body text, not bare linkified text.
- Merged table cells: a real, previously-undocumented structural property
  (found by scanning raw PropertyIDs pyOneNote itself has no name for, not
  a content-equality heuristic) resolves to a real `colspan` on the
  surviving `<td>`.
- Table-level styling: column widths (as a `<colgroup>`) and border
  visibility, baked into the table's own HTML the same way cell color is.
- Page-level presentation metadata: `PageResult.background_color` and
  `.rule_lines` (OneNote's "Page Color" and "Rule Lines" page-setup
  options) — plain data fields, not rendered into `blocks`, so the caller
  decides whether/how to visualize them.
- `PageResult.sub_level` (OneNote page indent) applied consistently across
  the normal and password-protected-recovery pipelines.
- A `.mht` (OneNote's own "Export -> Single File Web Page") ground-truth
  comparison method, used throughout this release to verify output against
  OneNote's own rendering rather than by inspection alone.

### Fixed
- **Real pyOneNote bug**: every `True`-valued boolean property (Bold,
  Italic, Underline, Strikethrough, `TableBordersVisible`, ...) was being
  silently dropped, project-wide, not just in tables — pyOneNote's own
  property-name table only ever registered the `False` form of a boolean
  PropertyID. Patched at the root; confirmed previously-invisible bold/
  italic/underline/strikethrough text now renders correctly against real
  content.
- A run's real text stored in `RichEditTextUnicode` (rather than
  `TextExtendedAscii`) was silently dropped unless it happened to contain a
  hyperlink marker — lost plain Unicode-only text (e.g. currency symbols).
- Table-cell images: fixed a double-extraction bug (same image landing as
  both a stray page-level image and a correctly-placed cell image) and a
  shared-accumulator leak (a cell with no image of its own rendering a
  neighboring cell's image).
- Embedded-file attachment could silently fail to attach to its page at all
  when its object reference pointed at a stale revision of the same
  underlying file (confirmed on a real PDF) — added a same-base-GUID
  fallback that only resolves when unambiguous, so it can't misattach among
  genuinely different files sharing a base GUID.
- **Real pyOneNote bug, found testing a real ~250MB/21-section notebook**:
  `OneDocment.get_files()` collapses multiple object identities that
  reference the exact same underlying file blob (e.g. the same photo pasted
  on two different pages) into a single last-wins identity, silently
  orphaning every other identity that legitimately points at that blob —
  135 of ~350 real pasted images unresolvable in one section alone. Fixed
  with a proper identity-preserving index (`OneDocment.files_by_identity()`)
  used everywhere image/attachment bytes are resolved. Verified end-to-end:
  `image_missing_data` 257 -> 0 on the real notebook, no regressions on any
  existing test file (full before/after content diff).
- CLI: `onenote-tool convert` could crash on Windows with `UnicodeEncodeError`
  printing a page title containing a character outside the console's legacy
  codepage — after the conversion itself had already succeeded. Reconfigures
  stdout/stderr to UTF-8 at CLI import time.
- A revision-store region-collapse bug: multiple floating boxes on one page
  were being merged into a single region because their key used only the
  page's base GUID, not each box's own full identity.
- An ISO-date (`YYYY-MM-DD`) false-positive in stamp detection was silently
  dropping real table content that happened to look like a date.
- Math equations previously rendered as literal, garbled Unicode control
  characters (OneNote's internal "linear format" delimiters); now detected
  and replaced with an honest placeholder instead.
- A real regression in page-splitting: reading real revision-store
  properties could produce a MORE complete page-title/page-count picture
  than the earlier structural-recovery pass alone, on the SAME password-
  protected section.

### Performance
- Password-protected section parsing: ~39s -> ~1.7s on a real test file.
  Root cause was an unbounded `file.read()` on a ciphertext-derived garbage
  length prefix during the marker-detection pass, not the property-set
  decode logic itself.
- `text._as_bytes` (a hot path once per-cell formatting started resolving
  every run): switched from `ast.literal_eval` to `codecs.escape_decode`,
  ~375x faster on real samples, byte-identical output. Fixed a
  self-introduced 21.7s -> 0.2s regression from adding table-cell
  formatting.

### Changed
- Recovered content types for password-protected sections — YouTube
  embeds, RTU non-hyperlink text handling, and list-membership decoding —
  brought closer to parity with the normal pipeline, subject to the
  existing per-object decode ceiling documented in
  `docs/knowledge/known-limitations.md`.

## [0.1.2] - prior release

### Fixed
- `.onepkg` unpacking on plain Linux could resolve GNU coreutils' own
  `/usr/bin/expand` (tabs -> spaces, an unrelated program) instead of
  falling through to `cabextract`, breaking CAB extraction entirely. Now
  looks up `expand.exe` specifically (Windows' real binary), so
  Cygwin/WSL setups that inherit Windows' PATH still resolve it correctly.

## [0.1.1] - prior release

### Fixed
- **Reverted 0.1.0's page-grouping "sibling consensus" fix** — it shipped
  without being verified against a real file. A/B diffing it against the
  pre-extraction baseline on the same real notebook found it could silently
  **drop whole pages**: a widened manifest-tree walk could cross into a
  neighboring page's subtree via a stale revision-store reference and claim
  a run that belonged to its own, separate page. Reverted to grouping a run
  purely by its own `TextRunFormatting`-derived guid, with no tree-based
  override — matches the verified baseline exactly (byte-identical page/
  title/report parity) on both a real unprotected and a real
  password-protected notebook.

This release is the reason this project verifies page-count/title parity
against a known-good baseline before landing any page-grouping change —
see `docs/knowledge/known-limitations.md`.

## [0.1.0] - initial extraction

### Added
- Initial extraction of the OneNote conversion engine as a standalone
  library (parsing `.onepkg`/`.one` via a patched `pyOneNote`).
- Cross-platform `.onepkg` unpacking: `cabextract` as a Linux/macOS
  fallback alongside Windows' `expand.exe`, with a `doctor` diagnostic
  command.
- PyPI publish workflow.

### Fixed
- A revision-store bug where a run's own `TextRunFormatting` could point at
  a stale, title-less object-space instead of the guid its page's other
  runs and manifest tree agreed on, silently splitting one page into a
  titled page plus a title-less orphan (reproduced across 3 separately-
  exported copies of the same section).
