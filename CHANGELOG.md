# Changelog

All notable changes to this project are documented here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/); versions match
`pyproject.toml`.

## [0.1.5] - 2026-08-14

### Added
- `PageFile.icon_ref` — a `jcidEmbeddedFileNode`'s own preview picture
  (`PictureContainer`) is now linked to its real attached file
  (`EmbeddedFileContainer`) instead of surfacing as an unrelated standalone
  image block. `_build_blocks`'s `'file_links'` entries render as a neutral
  `onenote-file-embed` card (icon + filename) instead of a bare paperclip
  emoji + link.

### Fixed
- **Real bug**: a header could render AFTER a list that structurally
  followed it on the same page. `ordering._page_content_order`'s
  `seen`/`order` tracking was shared across every page-manifest root in
  the file rather than reset per page — a node whose first visit (from
  anywhere in the file, e.g. a stale/duplicate reference from an unrelated
  page's title) happened in a non-order-assigning context permanently lost
  its chance to ever get one, even on its own real page later. Verified
  with a synthetic repro before fixing (not assumed) — see
  `docs/knowledge/known-limitations.md`.
- **Real bug**: a picture pasted directly into a table cell rendered
  correctly inline AND, separately, as its own stray standalone image
  block. `_build_blocks` now skips a ref already rendered inline via a
  cell, same shape as the existing YouTube-thumbnail/file-icon skip.
- **Real bug**: an image's reported width was capped at 600px with no
  proportional height rescale, both shrinking AND distorting real content —
  confirmed against a real embedded PDF preview (OneNote's own MHT export:
  793x1122; this tool: 600x1122). Fixed the distortion, and separately
  raised the cap itself (600 -> 4000) after confirming with TWO independent
  real ground-truth checks that OneNote's stored display-size properties
  are trustworthy, not something that needed shrinking to "a typical size."
- **Real bug**: a paragraph-level font size (e.g. a citation-style
  paragraph's real 9pt vs. an ordinary paragraph's 11pt) could be silently
  dropped when no per-run override existed — confirmed against a real page,
  cross-checked pixel-for-pixel against OneNote's own MHT export.
  `_resolve_run_format` now reads font size/family from `ParagraphStyle` as
  the base (same pattern as bold/italic/color/highlight), with
  `TextRunFormatting` still overriding when present. Every paragraph now
  carries an explicit font-size/font-family span, matching what OneNote's
  own MHT export actually does — verified crash-free, unchanged page/block
  counts across every bundled test file and two large real notebooks.
- Investigated, confirmed NOT bugs in this repo (see
  `docs/knowledge/known-limitations.md`): two-column layouts collapsing to
  one, and multiple images stacking at identical coordinates — both
  `ordering._page_regions`/`_page_content_order` already resolve correctly
  on real multi-box/multi-image pages; a real table's row count also
  matches OneNote's own MHT export exactly on the case checked.
- **Real bug**: `.onepkg` sections/section-groups unpacked in plain
  alphabetical order — never matched OneNote's own sidebar order.
  `convert._toc_order` recovers the real order from each folder's own
  `Open Notebook.onetoc2` (a real, previously-undocumented binary shape,
  reverse-engineered and verified against a real notebook's screenshot —
  pyOneNote has no parser for this file's node type at all). Also fixes a
  related bug found verifying it: a non-ASCII section name decodes
  correctly from `.onetoc2` but the on-disk filename CAB extraction
  produces for it is mojibake'd, so the order lookup was silently missing
  and sorting those sections last — now indexed under both forms. See
  `docs/knowledge/known-limitations.md` for the one residual case
  (group-vs-group mutual order) this doesn't close.
- **Real bug**: a standalone image sitting between two paragraphs always
  imported trailing after ALL of the page's text instead of its own row in
  place — `PageResult.blocks`/each `PageRegion`'s own blocks never tracked
  an image's true document position relative to the surrounding text runs.
  `ordering._page_content_order` now also assigns a position to
  `jcidImageNode`/`jcidEmbeddedFileNode` nodes (previously walked over but
  never given a slot), and `parser._build_blocks` interleaves a
  position-resolved image with the text around it. An image whose position
  doesn't resolve still falls back to the original trailing behavior.
- **Real bug**: a table always rendered before ALL of a page's text,
  regardless of where it actually sat (e.g. above the "Causes:" paragraph
  that introduced it). `_page_content_order` now assigns the table node
  itself a position too (its rows/cells are still consumed separately, not
  walked into), and `_build_blocks` interleaves it the same way as images.
- **Real bug**: an attachment (file/audio/video) had no structural page
  lookup at all — unlike images (which prefer a `PictureContainer`-derived
  page GUID), a file attached unconditionally to `current_guid` ("whichever
  page's text was seen most recently while iterating"), which is iteration
  order, not a real page lookup. Verified against real notebooks: a
  PDF/audio/spreadsheet attachment landing on the wrong (usually adjacent)
  page. Files now defer assignment the same way images do, preferring the
  file node's own identity GUID (its real page object-space).
- **Real bug**: a revision-duplicate `EmbeddedFileNode` (a different,
  non-stale identity pointing at byte-identical content) imported as two
  copies of the same attachment — files had no dedup at all, unlike images
  (deduped by content hash). Exact `(filename, content)` equality now drops
  the duplicate.
- **Real, wide-reaching bug**: a text run's own resolved guid (the
  mechanism every page-grouping decision is built on) is the guid of the
  OUTLINE BOX it structurally sits in, not always the page's own
  "official" identity — the same for a page with one outline box, but
  different for any page with more than one. Verified on real files: this
  was causing empty `page.regions` on pages that structurally do have
  real floating-box data, `sub_level` silently resetting to 0, and — most
  seriously — a page silently splitting into two `PageResult`s, with a
  secondary outline box's content showing up as its own spurious,
  oddly-titled or "Untitled page" entry. Fixed by indexing
  `ordering._page_regions`'s own already-computed outline-to-page
  association in reverse and remapping every run's guid through it before
  grouping — a real structural cross-reference recovered from data this
  tool already builds, verified collision-free, not a heuristic. Full
  before/after content diff on every bundled test file: identical output
  except the one intentional, verified-correct fix (a spurious duplicate
  page merging back into its real one). See
  `docs/knowledge/known-limitations.md` for what this does and doesn't
  close.

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
- A plain pasted picture (not an embedded-file's preview) in a format this
  tool can't decode (.emf/.xps) used to vanish with no trace on the page,
  despite `report.failed` correctly tallying it. Now leaves the same kind
  of visible placeholder as every other genuinely-unsupported element.
- **Three real pyOneNote bugs, found testing a real ~220MB/61-section
  notebook shared between two users**: (1) `FileNode.__init__` crashed
  constructing its own fragment's end-of-fragment terminator marker
  (`file_node_id` 0 or 255) before pyOneNote's own loop ever got a chance
  to recognize it and stop cleanly — confirmed losing 93% of a real 19MB
  section's content to this; (2) the same crash shape for a second,
  genuinely-unidentified node type; (3) `OneDocment.get_json()` crashed
  calling `.hex()` on an incomplete file declaration (a real, legitimate
  leftover from a pruned revision history, not itself a bug). Together
  these were aborting 8 of 61 real sections entirely (each caught
  gracefully, but reduced to a single error placeholder). Fixed all
  three; the same real notebook now converts 0 crashed sections, 1948
  successfully-converted page titles (up from 829 pre-fix, same run).
- Section/section-group names with non-ASCII characters (Estonian ö/ü)
  could come back double-encoded mojibake from CAB extraction (an
  external-tool issue, not this package's own code) — e.g.
  "Hommikusöögiks" as "HommikusÃ¶Ã¶giks". Fixed with a verified,
  self-checking round-trip correction (`convert._fix_mojibake`).
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
