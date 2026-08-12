# Known limitations

A direct, unvarnished list — see [Documentation.md](../Documentation.md)'s
progress tables for the content-type-by-content-type breakdown; this is the
structural/architectural stuff underneath that.

## `.onepkg` unpacking needs an external CAB extractor

`convert.unpack_onepkg` shells out to extract the `.onepkg` CAB archive —
it doesn't parse the CAB format itself. `_expand_onepkg` in `convert.py`
prefers Windows' built-in `expand.exe` when found (checked both at its
usual `System32` path and on `PATH`, so this also covers Cygwin/WSL setups
that expose it), and otherwise falls back to the `cabextract` CLI (wraps
`libmspack`; install via `apt`/`brew`/`dnf` package `cabextract`), which
covers Linux and macOS. If neither is present it raises `RuntimeError`
with install guidance rather than silently failing. Run `onenote-tool
doctor` to see which extractor (if any) it found.

Both tools are shelled out to rather than reimplemented in Python because
CAB extraction (LZX/MSZIP decompression) is nontrivial to get right, and
both are mature, widely-packaged implementations. Nothing currently
prevents also wiring in a pure-Python CAB reader if the external-binary
dependency ever becomes a problem, but none has been vetted against real
`.onepkg` files yet. (Parsing an already-extracted `.one` file, via
`parse_one_file` directly, has no such restriction — it's pure Python +
`pyOneNote`, no extractor needed.)

## Conversion is lossy by design

This is an explicit design choice, not an oversight: rather than trying to
perfectly reproduce every OneNote feature, unsupported content is reported
(via `ElementReport.failed`) and, where a page-position is known, left as a
visible placeholder — so a conversion never silently drops something
without a trace. See [Documentation.md](../Documentation.md)'s progress
table for exactly what's supported today.

## `pyOneNote` is version-pinned, not `>=`

`pyproject.toml` pins `pyOneNote==0.0.2` exactly. This package's
`_patches.py` monkeypatches that library's **private internals** — specific
classes, specific method names, specific attribute shapes — not any public,
stable API `pyOneNote` promises to keep. An upstream release could rename
or restructure any of that without warning, and the patches would either
raise loudly (best case) or silently stop taking effect (worst case,
quietly regressing parse quality with no error at all). Loosening this pin
needs re-verifying every patch in `_patches.py` against the new version
first — don't just bump it.

## Not thread-safe / not reentrant

`_patches.py`'s `_active_shadow`/`_active_decrypt`/`_active_expected_jcid`
are plain single-slot module-level lists, not thread-local or async-safe.
The whole pipeline is designed to run synchronously, one `.one` file at a
time, one call at a time, within a process. Calling into this package
concurrently from multiple threads/async tasks in the same interpreter can
cross-contaminate state between unrelated parses. If you need concurrency,
use multiple OS processes, not multiple threads.

## Importing this package has a process-wide side effect

`onenote_tool._patches` monkeypatches `pyOneNote`'s classes **at import
time**, unconditionally, for the lifetime of the process. If your
application also imports `pyOneNote` directly for something unrelated, it
will observe these patches too. This is deliberate (it's the only way to
fix real bugs/gaps in a third-party library without forking it), but worth
knowing if you're debugging something that touches `pyOneNote` directly.

## Revision-store duplication can misattribute content, even unprotected

OneNote's revision-store duplication (see
[onenote-file-format.md](onenote-file-format.md)) reaches the main,
unprotected pipeline too, via `page_title_by_guid`'s title dedup:

- **Still open**: a renamed page can leave both its old and new title text
  in the file under the exact same identity tuple, with no reliable signal
  to tell which is current — `page_title_by_guid`'s first-occurrence-wins
  picks correctly on some pages and stalely on others in the *same* file.
  Not attempted: a same-guid counter-example (a page whose title legitimately
  needed the *first* occurrence, not the last) was found on the same test
  file this was investigated against, ruling out a blind
  first-wins-to-last-wins flip.

**Tried and reverted (0.1.1): a "sibling consensus" run-grouping override.**
An earlier version of this package (0.1.0) had `parser.parse_one_file` build
a per-page "consensus" object-space guid from the manifest tree
(`ordering._page_content_order`'s tree walk, widened to also track which
page-manifest each run was reached under) and used it to override a run's
own `TextRunFormatting`-derived guid whenever the two disagreed — intended
to fix a run whose formatting reference pointed at a stale sibling
generation. It was never actually verified against a real file before
shipping: A/B testing 0.1.0 against the pre-extraction, verified-working
homeos code on the same real notebook found it introduced a **real
regression** — the manifest tree's own widened child-key walk (see
`ordering._CONTENT_CHILD_KEYS` vs `_ORDER_CHILD_KEYS`) could cross into a
neighboring page's subtree via a stale revision-store reference and claim a
run that legitimately belonged to its own, separate page, silently
**dropping that page entirely** (confirmed: a real "― George Carlin" quote
page vanished from a test notebook's output). Reverted in 0.1.1 to the
original, verified mechanism: group a run purely by its own `_run_guid`
(`TextRunFormatting`), no tree-consensus override. If a stale-sibling-
formatting-reference bug like the one 0.1.0 tried to fix is ever confirmed
again, verify any fix with a full page-count/title diff against a known-good
baseline on a real file before landing it — this one shipped on reasoning
alone and broke something worse than what it fixed.

## Table cells: rich formatting landed, merged cells deliberately not attempted

A table cell's content used to get bare linkified text only — no bold/
color/font-size/alignment/list support at all, unlike a page's body text.
**Fixed**: `rendering._resolve_cell_html` now collects a cell's runs into
the exact same run-dict shape the main per-page pipeline builds and renders
them through `_runs_to_paragraphs` — the same formatting/alignment/list
code, not a reimplementation. Cell background color
(`CellShadingColor`, same 4-byte RGB+alpha-flag shape as `FontColor`)
became a `background-color` style on the `<td>`. All verified against a
real table with colored cells, per-cell and per-run alignment, and list
items inside cells.

**Merged cells: implemented, real structural signal found (not a
heuristic).** OneNote's own MHT export resolves a merged cell to a real
`<td colspan=2>` — genuine merge information clearly exists internally —
and an earlier pass at this looked for it and gave up: an exhaustive check
of every property this tool could **name** (pyOneNote's own
`_property_id_name_mapping`, cross-referenced against everything visible on
a real merged cell) found nothing. The mistake was trusting that name
table as complete. It isn't — `PropertySet.get_properties()` silently drops
any property whose raw ID isn't in pyOneNote's small hardcoded dict
(decodes as the string `"Unknown"`, filtered out before a caller ever sees
it; `ink.py` already had to work around this same gap for undocumented ink
properties). Dumping every **raw** property ID on every real
`jcidTableCellNode` in the test file (bypassing `get_properties()` via the
raw `PropertySet` body kept on `prop['body']`, the same escape hatch
`ink.py` uses) turned up an ID pyOneNote has no name for at all
(`0x140035DD`) that appears on exactly the cells involved in a merge and
nowhere else: absent on every ordinary cell; on each of the file's three
real merges, the first (content-bearing) cell carries the value `2` (its
own column span — every merge in the test file happened to span exactly 2
columns, so a 3+-column span is unverified but the mechanism generalizes),
and the following, absorbed cell carries `0`. This is a genuine structural
signal, not the content-equality heuristic considered and rejected earlier
(comparing adjacent cells' text — the same failure shape as the "sibling
consensus" regression documented elsewhere in this file, since two
legitimately-different cells that happen to hold equal text, e.g. two
"N/A"s, would be indistinguishable from a real merge by that signal alone).
`rendering._cell_merge_span` reads it; `_resolve_table_grid` drops any
`0`-valued cell from its row and gives the preceding cell a real `colspan`
attribute. Verified against all three real merges in the test file
(including one where the merged cell holds a pasted image, not text) —
output now matches the MHT ground truth's `<td colspan="2">` exactly.

**Images inside a table cell: implemented and verified (normal sections
only).** A picture pasted directly into a cell is a plain `jcidImageNode`
there too, reached the same way a cell's text runs are (`_child_ids` tree
walk) — `rendering._collect_cell_runs` now recognizes it and extracts it
via a caller-supplied `image_builder` callback (`parser.py` binds this to
the same `_build_page_image` the page-level path already uses; not wired
into `recovery.py` at all, since tables there are the confirmed 0%-decode
dead end above — nothing to attach an image to). Two real bugs found and
fixed while verifying this against the actual test cell (which turned out
to be present in the `.onepkg` after all, not absent as first suspected):

- **Double extraction.** The exact same image was being extracted TWICE —
  once by the main per-page loop's own generic image handling (landing as
  a stray page-level image via the near_guid fallback), once via the new
  cell walk (landing correctly in its cell) — same bytes, two `PageImage`s,
  two ref tokens. Fixed by tracking which identities were claimed by a
  cell (`cell_image_idents`) and dropping any top-level image record that
  matches one before page assignment.
- **Shared-accumulator leak.** `_resolve_table_grid` shares ONE `images`
  list across every cell in a table (so the caller gets the table's full
  total) — but `_resolve_cell_html` was rendering a cell's own `<img>` tags
  by iterating that SAME shared list, so a cell with no image of its own
  rendered whatever an EARLIER cell in the same table had already added.
  Verified on the real merged-cell row: the cell with no picture rendered
  the neighboring cell's picture a second time. Fixed by giving each cell
  its own local list for rendering, only feeding results back into the
  shared accumulator afterward.

## A real pyOneNote bug dropped every True-valued boolean property

Found while chasing table-level styling (`TableBordersVisible`, below) but
much bigger than tables: **every boolean property, whenever its value was
`True`, was being silently dropped** — Bold, Italic, Underline,
Strikethrough, Superscript, Subscript, `TableBordersVisible`,
`EnableHistory`, `OutlineElementRTL`, all of them, project-wide, in every
section (not protected-content-specific).

Root cause: [MS-ONESTORE] 2.6.14 packs a Bool-type property's actual value
into bit 31 of the PropertyID itself (`PropertyID.boolValue`), not as
separate data. pyOneNote's `_property_id_name_mapping` is keyed on the
**full** 32-bit ID, but only ever registers the bit-31-clear (False) form —
the bit-31-set (True) form of e.g. `Bold` is a completely different integer
that was never added to the dict, so it decodes to the name `"Unknown"` and
`get_properties()` throws it away before any caller ever sees it (same
`get_properties()`-drops-`"Unknown"` gap the merged-cell fix above found).
Since OneNote only ever writes a boolean property when it overrides the
(always-False) default, this meant essentially every real occurrence of
every boolean formatting property in the wild was invisible.

Measured on the real test file, before vs. after masking bit 31 off before
the name lookup (`_patches.py`'s `_bool_aware_get_property_name`, patching
`PropertyID.get_property_name`): `Bold` appeared 25 times in decoded
properties, **0 of them `True`**, before the fix; after, 5 of those 25
correctly read `True`, and `Italic`/`Underline`/`Strikethrough` — previously
invisible in their `True` form entirely — appeared for the first time too.
Cross-checked against the MHT ground truth's `font-weight:bold` spans (a
"Lorem Ipsum" paragraph, several table header cells) — the tool's `<strong>`
output now lands on exactly the same text. Bit 31 is spec-reserved (0) for
every non-Bool property type, so masking it off is a no-op everywhere else
— verified via full test suite + unchanged page counts on all three real
sections before/after.

## Table-level styling: column widths and border visibility, implemented

Both real properties this project's Table A previously listed as "found,
not exposed":

- **`TableBordersVisible`** only needed the boolean-property fix above —
  it's a perfectly ordinary named property once its `True` form stops
  getting dropped. `rendering._table_borders_visible` reads it from `val`
  directly.
- **`TableColumnWidths`** needed its own fix. It's a variable-length blob
  (byte 0 = column count, then that many little-endian float32 values, one
  per column, in the same "half-inch units" `PropertySet
  .half_inch_size_to_pixels` already converts every other width/height
  property with elsewhere) — but `get_properties()`'s generic fallback for
  a blob property that isn't a GUID is `.decode('utf-16')`, and only
  `.hex()` if THAT raises. A short/unlucky byte pattern can decode as
  UTF-16 successfully and produce mojibake instead of raising — observed
  directly on the sibling property `TableColumnsLocked` (same blob shape),
  which came back as an unprintable character instead of hex. Not a
  reliable string to parse either way. `rendering._table_column_widths_px`
  reads the raw bytes straight off `prop['body']` instead (the same escape
  hatch the merged-cell fix and `ink.py` use) and decodes the count-prefixed
  float array directly. Verified against a real 8-column employee-data
  table: decoded widths (px) were `[49.5, 117.4, 80.7, 85.4, 91.9, 49.5,
  50.4, 49.5]` for columns ID / Name / Department / City / "Join Date" /
  Salary / Status / (blank) — narrow for short fields, wide for names and
  the date column, exactly matching what the real table looks like.

Both are baked directly into `_table_html`'s output as inline styling — a
`<colgroup>` of `<col style="width:...px">` (an HTML `<table>`'s default
column sizing is otherwise content-driven and ignores OneNote's widths
entirely) and, only when borders are explicitly ON, `border-collapse` +
a real per-cell border (an HTML table has no border by default, which
already matches a OneNote table with borders explicitly hidden — no
styling needed for that half). Same "additive inline CSS, no new schema
field" convention already used for cell background-color and paragraph
alignment. `TableColumnsLocked` (an editing-lock bitmask, not visible
content) was deliberately left unexposed — its semantics are unverified and
it doesn't affect what a converted page looks like.

## Table-cell formatting had a real, now-fixed performance cost

Resolving formatting for every table cell (the fix above) made
`text._as_bytes` a genuine hot path — it used to go through
`ast.literal_eval` (full `compile()` + AST parse) for every 2-16-byte
property value, cheap enough to ignore when only a handful of runs per
page needed it, but a real cost once every cell's every run started
resolving font-size/color/alignment. Measured: **21.7s to parse a 396KB
section**, almost entirely `compile()` time. Fixed by switching to
`codecs.escape_decode` (the same escape-sequence parser CPython itself
uses for a bytes literal, ~375x faster on real samples, verified
byte-identical output) — same section now parses in 0.2s.

**A second, separate slowdown was found while chasing this — now also
fixed**: parsing the password-protected section alone took ~39s. Root
cause, found via `cProfile` on `parse_one_file`'s first pass in isolation
(the marker-detection-only pass — reads every `FileNode` on the raw,
still-encrypted bytes just to learn whether the section carries an
`ObjectDataEncryptionKeyV2FNDX` marker, before any password/shadow-decrypt
work even starts): a type-0x7 property (`PrtFourBytesOfLengthFollowedByData`
— a variable-length blob) decodes its 4-byte length prefix from ciphertext
too, so on real encrypted content that length comes out as near-random
32-bit garbage. pyOneNote's own (unpatched) code responds to that by doing
`file.read(cb)` unconditionally — materializing and copying up to that many
bytes (bounded only by EOF) — and this single call site accounted for
**~33 of the pass's ~34 seconds** (94 calls, profiled on the real test
file), dwarfing everything else, including the `_as_bytes` cost above.
Nothing in this marker-only pass ever reads the resulting `.Data` (the
whole pass exists only to set `_saw_encryption_marker`, which is decided
by the FileNode's own type, not by any property value) — so during this
pass specifically, `_patches.py` now `seek`s past the claimed length
instead of reading it (same cursor effect for sibling properties, none of
the copy cost). Scoped strictly to the marker-probe pass via the existing
`_active_marker_probe` flag; the real shadow-decrypt second pass, and every
unprotected-file parse, are untouched. Verified byte-identical
`PageResult` output (all three real test sections) and unchanged
`password_status` (`needs_password`/`wrong_password` cases included) before
vs. after, by toggling just this one patch in isolation. Real-file result:
**~39s to ~1.7s** for the password-protected section.

## Password-protected recovery has a real, not-yet-closed ceiling

Even with a **verified correct** password:

- Roughly half of page titles can fail to decode on a harder real file
  (surfaced as `Untitled page N` rather than a wrong name — never silently
  dropped, but not the real title either).
- Text formatting (bold/color/headings/etc.) only renders where the
  underlying style object happens to decode — not universal, no known way
  to force it currently.
- **Lists**: implemented and verified — `recovery._pages_from_structural_groups`
  now builds the same `jcidOutlineElementNode` → `ListNodes` → `NumberListFormat`
  mapping the normal pipeline uses, reading it from `by_id` instead of the
  raw `props` list. Real, partial recovery on the same test file: a page
  that previously rendered as entirely flat paragraphs now has one real
  `<ol>`/`<ul>` item. Bounded by the usual per-object decode ceiling, not by
  missing wiring — on that file, ~47% of `jcidOutlineElementNode` and ~25%
  of `jcidNumberListNode` objects decoded a non-empty property set; an item
  whose own membership object didn't decode still renders as a plain
  paragraph rather than being dropped.
- **Tables**: wired in the same way (`_structural_page_groups` now also
  clusters `jcidTableNode` declarations, reusing the normal pipeline's own
  `rendering._resolve_table_grid`) but **confirmed non-functional on the
  test file available**: every `jcidTableNode` property set — all 6, across
  both protected sections tested — decoded completely empty (`val == {}`),
  not partially like text/lists. This isn't a wiring gap; the object's own
  body isn't recoverable via the existing 3-attempt decode chain on this
  file, more severely than any other object type checked so far (plausibly
  because a table's own properties lean more heavily on the same
  unreliable OID-stream reference resolution than a plain text run's do —
  unconfirmed, no smaller test case has isolated it). The code is left in
  (degrades to silently contributing nothing, exactly like an unset list
  item does) rather than reverted, since it's inert-safe and would start
  working automatically if this decode ceiling is ever improved — but
  don't expect real tables from protected content today.
- **Page indent level**: implemented and verified — `_pages_from_structural_groups`
  now reads the same `PageMetaData.PageLevel` property the normal pipeline
  does. Recovered a real secondary-level (indented) page correctly on the
  test file. Found and fixed a real garbage-decode case while verifying
  this: a misaligned protected-content decrypt landed `PageLevel` on
  unrelated ciphertext and decoded to `3334854043` rather than failing
  cleanly — `_page_sub_level` now clamps anything outside 1–20 back to
  top-level instead of propagating a nonsensical `sub_level`. (The normal
  pipeline reads the identical property with no such clamp — never
  observed to need one there; protected content's 3-attempt decode chain
  is where misaligned reads like this actually happen.)
- **Audio/video attachments**: verified against real files — `.wav`/`.ogg`
  (audio) and `.mp4`/`.webm`/`.wmv` (video) all classified correctly by
  `images._attachment_kind` in a protected section, byte-for-byte matching
  their filenames' declared sizes. What's *not* verified by this — and
  known to still be affected by the pre-existing structural-clustering
  spillover documented above — is which PAGE a given attachment lands on;
  a real audio file was observed bleeding onto a neighboring page's group
  in exactly the shape already described for text/images.
- **YouTube embeds**: wired in (reuses `images._youtube_from_rtu`, the same
  detector the normal pipeline uses) but **not verified against real
  decoded data** — the one real YouTube link in the test file used for this
  never decoded at all (`RichEditTextUnicode` came back empty), the usual
  partial ceiling, not a new failure. Deliberately does NOT attempt
  thumbnail-perceptual-hash matching to a nearby embedded preview image
  (unlike the normal pipeline) — `_pages_from_structural_groups`'s own
  images don't compute a `dhash`, and matching logic on top of already-
  unverified input would be a second layer of guessing; the embed still
  renders correctly without a matched thumbnail, just falling back to
  YouTube's own thumbnail client-side.
- The floating-box canvas layout (`page.regions`) is still not
  reconstructed at all for protected content (see
  [Documentation.md](../Documentation.md)'s progress table) — not yet
  attempted.
- Exact page count on a **wrong** password is unrecoverable — page titles
  are encrypted too, and the revision-store duplication described in
  [onenote-file-format.md](onenote-file-format.md) makes it impossible to
  reliably dedupe stale title copies without readable content to compare.

See [recovery-techniques-log.md](recovery-techniques-log.md) for the full
trail of what's been tried against each of these — several "obvious" fixes
turned out to be real regressions on closer testing, so read that before
attempting one of the above.

## Canvas/layout position: exposed via `page.regions`, normal sections only

`PageResult.blocks` itself is still content-only, in reading order, with no
`x`/`y` on an individual block — that's deliberate (see
[Documentation.md](../Documentation.md)'s "blocks format" section). A
free-form floating-box view IS available additively via `page.regions`
(`models.PageRegion`, one per top-level `OutlineNode`, position from
`OffsetFromParentHoriz/Vert` + size from `LayoutMaxWidth/Height` —
`ordering._page_regions`), verified against a real 6-box page (6 distinct
`(x, y)` pairs, each region's own content matching its box's own
description text — "top left"/"bottom right corner"/"center-middle"/etc.).

**Real bug caught before ever landing**: the first version of
`_page_regions` keyed each region by the outline node's *base* guid
(`identity.split(',')[0]`, the same truncation used everywhere else in this
codebase for *page*-level keys). That's wrong for outline nodes
specifically — every object inside one page's own object-space (its title,
every outline box, every run) normally shares that SAME base guid,
differing only by revision counter, so truncating it collapsed all N boxes
on a page onto one identical key. Every region rendered the exact same
merged, full-page content instead of its own box's — caught immediately by
checking actual region *content* against a real multi-box page, not just
counting that N regions with the right coordinates came back. Fixed by
keying on the outline node's FULL `(base, revision)` identity instead — a
guid that happens to be unique enough for *page*-level lookups is not
automatically unique enough for anything with more than one instance per
page.

Not implemented for password-protected-section recovery — `regions` is
always `[]` there. **Actually tried, not just skipped**: the safest
possible approach was tested directly — calling `ordering._page_regions`
(the SAME tree-walk the normal pipeline uses, zero new heuristics) against
a real protected file's already-decrypted `props`/`by_id`, once a copy of
the real 6-box multi-region test page existed in a protected section
specifically to check this. Result: not a single page's `jcidPageNode`
decoded with more than one outline-box child reference intact anywhere in
the file. The chain needed is 5 objects deep — `jcidPageManifestNode` →
`jcidPageNode` → `jcidOutlineNode` → `jcidOutlineElementNode` →
`jcidRichTextOENode` — each with its own independent ~20-50% decode
ceiling (see the lists/tables findings above); getting all five to decode
together for even one multi-box page never happened. `jcidOutlineNode`
itself decoded standalone often enough (19/106, ~18%, on the same file) to
theoretically report SOME box positions, but with no reliable way to
attribute content to a specific one of them (recovery's structural
clustering only correlates content to a PAGE, not a box within it — that's
the actual missing piece, and it's the same shape of heuristic-layering
that caused the "sibling consensus" regression), building anything from
that would mean shipping positions with empty or guessed content — worse
than not having `regions` at all. Left as: confirmed hard, not attempted
further. `blocks` remains authoritative for content either way; `regions`
is a purely additive, best-effort second view when it's available at all.

## YouTube thumbnail matching needs network access

`images._youtube_thumb_hashes` makes a real outbound HTTP request (to
`img.youtube.com`) to perceptually match an embedded preview image to the
actual YouTube thumbnail. This is the **only** network call anywhere in
this package, and it's fully optional/best-effort — wrapped in its own
try/except, degrading gracefully (the embed just doesn't get pre-matched
to a specific image, and would load its thumbnail from YouTube client-side
instead) if there's no network access or the request fails. There is
currently no way to disable this call explicitly if you want to guarantee
zero network access — it fails closed on its own, but doesn't fail fast.

## Recycle-bin / deleted-page content: `.onepkg` export doesn't carry it

**Confirmed** (updates the earlier "unconfirmed lead" — this is now a real,
structural finding, not a guess): a whole section deleted before export
(so it moved to the notebook's recycle bin) left **no trace of its content**
anywhere in the resulting `.onepkg`. Checked directly: the CAB archive
contained only the 2 live sections' `.one` files — no third `.one`, no
hidden subfolder (`unpack_onepkg`'s recursive listing found nothing else).
The notebook's table-of-contents file (`Open Notebook.onetoc2`, the third
file every `.onepkg` carries alongside its `.one` sections — parseable by
`OneDocment`/`check_valid` as a valid OneNote-family file, but yields 0
properties via `get_json()`, since it uses TOC-specific object types outside
what this package's property decode targets) DOES reference the recycle bin
by name — a raw UTF-16LE string scan of its bytes turned up the literal
string `'OneNote_RecycleBin'` alongside the two real section filenames —
but that's a name in the table of contents, not the section's actual page
data. The `IsDeletedGraphSpaceContent` property (jcid `0x1C001DE9`,
mentioned in an earlier version of this note as an unconfirmed lead) was
irrelevant all along for THIS scenario: there's no deleted graph-space
content in the archive at all for it to flag, because a plain `.onepkg`
export simply never bundles recycle-bin content in the first place —
confirmed structural, not a decode failure.

**Practical implication**: recovering deleted-page/-section content (see
"Future features" below) is **not reachable from a `.onepkg` export at
all**, for either a deleted page within a live section or a whole deleted
section — regardless of password protection. It would need a different
input entirely: direct access to OneNote's own on-disk cache files (not
something `unpack_onepkg`/`parse_one_file` currently reads, and a much
larger scope — a different file format, likely no encryption-bypass tooling
reusable from this project at all). Page-level recycle-bin content within a
still-live section was checked once already (one page deliberately deleted
before export) with the same negative result — consistent with this now
being the expected, structural answer rather than something still open.

## Future features (requested, not started)

- **Deleted pages/sections, imported and marked as deleted** rather than
  silently omitted — surfacing OneNote's recycle bin as real (flagged)
  content instead of dropping it. **Blocked at the input level, confirmed**
  (see the recycle-bin section above): a `.onepkg` export simply doesn't
  carry recycle-bin content at all, for a deleted page OR a deleted whole
  section, password-protected or not. Not something a smarter parser could
  recover — the bytes aren't in the file being handed to it. Would need a
  different input source entirely (OneNote's own on-disk cache, not
  `.onepkg`) to even start on.
- **Revision/edit history for pages and sections** — exposing prior
  generations of a page as an explicit history, instead of the current
  purely-a-nuisance treatment where every stale prior revision is something
  to detect and discard (see the whole "revision-store duplication" thread
  in [onenote-file-format.md](onenote-file-format.md) and
  [recovery-techniques-log.md](recovery-techniques-log.md)). Would very
  likely reuse the same generation-clustering machinery already built to
  *suppress* stale revisions — the difference is keeping them, labeled,
  instead of throwing them away — but that's a real design question, not
  just flipping a flag: it needs a returned shape for "N generations of this
  page" and per-generation ordering/timestamps, neither of which exist yet.

Both are explicitly requested future work, not yet designed or attempted —
noted here so they don't get lost, not because anything below has scoped
them out.

## Testing has real gaps

The test suite (`tests/`) is synthetic-only by design (see
`tests/fixtures/README.md` for why — no real notebooks are committed to a
public repo) except for `test_smoke_local_file.py`, which is skipped
unless a developer points it at their own local file via environment
variables. This means:

- The password-decryption path, the structural recovery path, and every
  real-file edge case documented in
  [recovery-techniques-log.md](recovery-techniques-log.md) have **no
  coverage in CI at all** — they're only exercised by whoever runs the
  local smoke tests against their own files.
- There is currently no shared, version-controlled corpus of test files
  covering the known-tricky cases (protected + multi-image, protected +
  revision-duplicate spillover, etc.) — each contributor has been
  re-creating their own ad hoc test notebooks. A synthetic/scrubbed
  `.onepkg` corpus that's actually safe to commit would close this gap;
  none exists yet.
- **A real ground-truth source exists and is worth using deliberately**:
  OneNote's own "Export → Single File Web Page (.mht)" produces literal
  HTML+CSS Microsoft itself asserts is correct (see
  [onenote-file-format.md](onenote-file-format.md) for the format details).
  A full-text word-level diff between a `.mht` export and this tool's own
  conversion of the same notebook found two real, previously-unnoticed bugs
  in one pass (the ISO-date and `RichEditTextUnicode` fixes above) — a much
  stronger signal than manually eyeballing block kinds/counts, since
  disagreements are actual, unambiguous content losses rather than
  plausible-looking output that might just be wrong in a way nobody
  happened to check for.
