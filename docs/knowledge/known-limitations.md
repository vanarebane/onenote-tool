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
- YouTube-embed matching, page indent levels, and the floating-box canvas
  layout (`page.regions`) are still not reconstructed at all for protected
  content (see [Documentation.md](../Documentation.md)'s progress table) —
  not yet attempted.
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
`ordering._page_regions`), verified against a real file with 3 independent
boxes on one page (3 distinct `(x, y)` pairs matching its visual layout).

Not implemented for password-protected-section recovery — `regions` is
always `[]` there; that recovery path has no notion of "which outline box"
at all, only a flatter title/text/file clustering (see
`recovery._structural_page_groups`), and building one would need real
verification against a protected file with multiple floating boxes, which
hasn't been done. `blocks` remains authoritative for content either way;
`regions` is a purely additive, best-effort second view.

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

## Recycle-bin / deleted-page content: unconfirmed lead, not acted on

OneNote has a per-notebook recycle bin (deleted pages/sections are held, not
immediately purged). `pyOneNote`'s own property-name table has an
`IsDeletedGraphSpaceContent` entry (jcid `0x1C001DE9`) — architecturally
almost certainly the flag for this — but it was checked directly against
every section of the test notebook this project uses (including one with a
page deliberately deleted first) and never appeared on a single property,
in any section. Either a plain `.onepkg` export doesn't carry recycle-bin
content at all (most likely — would mean there's nothing here to handle),
or it's exposed in a shape `get_json()`'s property decode doesn't surface
(e.g. structural FileNode metadata rather than a `PropertySet` property).
**Not implemented, not guessed at** — no verified file exists yet where this
flag is actually set, and the earlier "sibling consensus" incident above is
a direct example of what shipping a plausible-but-unverified fix here would
risk. If a real file surfaces where deleted content is visibly leaking into
a normal parse (spurious pages/titles that don't correspond to anything the
user still has), that's the first thing to check for.

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
