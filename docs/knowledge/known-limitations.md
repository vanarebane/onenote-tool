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

**Merged cells: investigated, not implemented, real reason.** OneNote's own
MHT export resolves a merged cell to a real `<td colspan=2>` — genuine
merge information clearly exists internally — but an exhaustive check of
every `jcidTableNode`/`jcidTableRowNode`/`jcidTableCellNode` property this
tool can read (pyOneNote's own property-name table, cross-referenced
against every property that actually appears on a real merged cell) found
no span/merge count anywhere. What IS observed: a merged cell's content is
literally duplicated — TWO separate `jcidRichTextOENode` objects, different
identities, identical text — one physically sitting in each of the cells
the merge visually spans. Detecting a merge by comparing adjacent cells'
content for equality was considered and rejected: it's exactly the shape of
unverified heuristic that caused the "sibling consensus" regression
documented elsewhere in this file — legitimately-duplicate, non-merged cell
content (e.g. two cells that both happen to say "N/A") would be
indistinguishable from a real merge by that signal alone. Current output
shows the duplicated content in both cells rather than collapsing it —
correct data, wrong shape, not silently lost.

**Images inside a table cell**: not implemented — a real test cell with an
embedded picture currently contributes nothing to that cell's HTML (image
extraction only ever runs at the page level, with no path back to "which
cell is this in"). Verified this specific gap couldn't be double-checked
end-to-end this session: the one real image inside a table cell added to
the test file appeared in a same-day `.mht` re-export but not in the
`.onepkg` export taken ~2 minutes earlier (timestamps confirm the `.onepkg`
predates the image being added) — so its absence from this tool's own
output is expected for THAT specific file, not evidence one way or the
other about whether the extraction gap itself has been fixed. Still a real,
known gap either way.

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
