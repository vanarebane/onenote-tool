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

**A real gap in that guarantee, found and fixed testing a real notebook**: a
plain pasted picture (a bare `jcidImageNode`, not an embedded-file's
preview thumbnail) in a format this tool can't decode — no rasterizer here
for e.g. `.emf`/`.xps` — used to just vanish with no trace at all on the
page itself. `report.failed` DID correctly tally it (`image_ext.emf`,
etc.), but nothing was ever left where the picture used to be — the
placeholder path only ever covered an embedded-file's missing preview, not
this case. Fixed: `parser.py`'s image-handling branch now leaves the same
kind of visible placeholder run either way. Verified on the real notebook:
10 real pasted EMF/XPS images that used to leave nothing now show a
placeholder; the other 23 `image_ext.*` failures found there are
genuinely-cosmetic embedded-file preview misses (the real attached file
itself already succeeded and shows up as a download link — no placeholder
needed, nothing lost).

## Real crashes, found and fixed testing a large, real, multi-author shared notebook

A second real-world stress test (a ~220MB, 61-section notebook synced
between two users — a genuinely different shape of complexity than a
single-author notebook: more revision generations, more concurrent-edit
history) surfaced **8 of 61 sections failing to parse at all**
(`section_parse_error:AttributeError`) — each one caught gracefully by
`convert.py`'s per-section try/except (so the OTHER 53 sections still
converted fine, and the process itself never crashed), but an entire
section's worth of real pages reduced to a single error placeholder each
time. Two distinct root causes, both real bugs in pyOneNote itself, not in
this package's own logic:

**1. `FileNode.__init__` crashes on its own fragment-terminator marker.**
pyOneNote's `FileNodeListFragment.__init__` loop already checks for
`file_node_id == 0` or `255` as an end-of-fragment terminator (`FileNode
.py`, `if ... file_node_id == 255 or ... == 0: break`) — but that check
only runs AFTER unconditionally constructing a full `FileNode` for
whatever comes next, including the terminator itself. `file_node_id 0`
isn't in pyOneNote's own `_FileNodeIDs` table at all, so constructing it
falls into the generic "unhandled type" branch (`self.data` never gets
assigned) — and the SAME `__init__` unconditionally does `self.data.ref`
for any node whose header bits decode `baseType == 2` (confirmed true for
a real id-0 terminator's actual header value), crashing with
`AttributeError: 'FileNode' object has no attribute 'data'` before the
loop's own terminator check is ever reached. Confirmed on the real file:
this happened **93% through a 19MB section** — nearly the entire section's
real content was being thrown away for what both pyOneNote's own loop and
[MS-ONESTORE] agree is just a normal, expected end-of-fragment marker, not
a genuinely unsupported type. Fixed in `_patches.py`'s `_filenode_init`:
peek the 4-byte header before running the vendor dispatch at all; a
terminator ID gets treated as one immediately (no data, no children,
matching the "no data part" shape pyOneNote's own dispatch already uses
for other structural end-markers) instead of running the crash-prone
vendor code for it.

**2. The same crash shape, for a genuinely unidentified type.** A second,
different section hit the identical `self.data.ref` crash for
`file_node_id 2` — NOT a terminator (only 0/255 are), and also absent from
`_FileNodeIDs`. Tried reading a generic `FileNodeChunkReference` here first
(the same structural field working for a different unhandled type in
`_encryption_key_fnd_fix`, already in this file) and recursing into its
children the way pyOneNote's own dispatch would have — but on the real
file this decoded a garbage `stp` offset (`ValueError: cannot fit 'int'
into an offset-sized integer`), meaning this ID's real byte layout isn't a
plain chunk reference (or this is genuinely corrupted/misaligned data, not
a rare-but-valid type). Recursing into that would risk building on garbage
rather than just losing one node's subtree, so this case gets the same
safe "no data, no children" fallback as the terminator case — capping the
damage to whatever's structurally beneath this ONE anomalous node instead
of either crashing the whole section or misreading further content from a
bad offset.

**3. `OneDocment.get_json()` crashes on an incomplete file declaration.**
A separate bug, hit by 6 of the 8 failing sections:
`get_json()` calls `.hex()` unconditionally on every `get_files()` entry's
`content` — but a file declaration
(`ObjectDeclarationFileData3RefCountFND`) whose referenced blob genuinely
never showed up anywhere in the file (a real, legitimate state for a
heavily-pruned revision history — not itself a bug, just an incomplete
cross-reference left over from old edits) leaves that entry's `content` at
`get_files()`'s own string default (`""`), and `"".hex()` raises
`AttributeError: 'str' object has no attribute 'hex'` — aborting the
entire section for one incomplete, usually-irrelevant declaration. Fixed
with `OneDocment.get_json` patched to guard the same way the original
should have: `.hex()` only when content is real bytes, `''` otherwise.

**Verified**: all three fixes together took the real notebook from 8
crashed sections (0 pages each) to 0 — `title` (successfully converted
page count) went from 829 to 1948 in the same run, only these fixes
changed. No regressions found on any existing test file (page/failure
counts identical before and after, both smaller test notebooks and the
first real notebook from the previous round of testing).

## Section/group names can come back mojibake'd from CAB extraction

Also found on the same real shared notebook: a section name with Estonian
non-ASCII characters (ö, ü) extracted as double-encoded mojibake —
`"Hommikusöögiks"` became `"HommikusÃ¶Ã¶giks"` — the classic "real UTF-8
bytes decoded as Latin-1, then re-encoded as UTF-8" shape, coming from
`expand.exe`/`cabextract` (external tools this package doesn't control),
not from this package's own code. Fixed with `convert._fix_mojibake`:
round-trip the name through Latin-1-encode -> UTF-8-decode. This doubles
as its own safety check — it only succeeds for a string that's ACTUALLY
this exact mojibake shape. An all-ASCII name round-trips back to itself
unchanged (harmless no-op); a name with genuine, correctly-encoded Unicode
outside Latin-1's range (confirmed on the same notebook: a section legitimately
named "Meie Maja™") fails at the very first step — Latin-1 can't represent
U+2122 at all — so it's left alone rather than risk mangling it further.

**A separate, DIFFERENT-looking case was investigated and left alone**: a
section named "Skills, Tools" (confirmed by the user against the real
notebook) extracted as `"Skills^J Tools"` — a comma replaced by the
literal two-character sequence `^J`. This is NOT the same bug as the
mojibake above (comma is an ordinary ASCII character, not something UTF-8/
Latin-1 double-encoding would ever produce from it) and, with only one
observed example, there isn't enough evidence to build a safe, verified
un-escaping rule the way the mojibake fix above has (that one had multiple
independent real examples, `Ã¶` for `ö` confirmed in three different
filenames, and a well-understood, well-known root cause). Left as an
observed, undiagnosed cosmetic quirk of the extraction step rather than
guessed at — `SectionResult.name` may not always exactly match a section's
true display name when it contains punctuation `expand.exe`/CAB
extraction treats as filesystem-special.

**Root cause pinned down (still not fixed — same reasoning as above still
applies).** A hex dump of the SOURCE `.onepkg`'s own `Open Notebook
.onetoc2` bytes (not anything either extraction tool produces) shows the
name field already reads `Skills` + `5e 00 4a 00` (`^` U+005E, `J`
U+004A, literal two-character `^J`, NOT a control character — a real line
feed would be `0a 00`) + ` Tools.one` — confirmed at the exact byte
offset, both before and after CAB extraction, identical either way. This
is baked into the notebook file Microsoft's own OneNote client produced,
not something `expand.exe`/`cabextract`/this package introduces or could
safely guess a fix for — same "one observed example, no safe un-escaping
rule" reasoning as above, now with a confirmed origin instead of an
unknown one. `convert.unpack_onepkg`'s section-order fix (below) matches
this section by its corrupted on-disk name, same as any other section —
ordering is unaffected by the corruption, only the display name is.

## Several real, lower-priority bugs found and fixed testing real notebooks

A batch of smaller real bugs, each found by cross-checking this tool's
output against OneNote's own MHT export or a real notebook's structure,
verified before landing (not assumed):

**A cell image duplicated as a stray standalone image.** A picture pasted
directly into a table cell rendered correctly inline in the cell's own HTML
AND, separately, as its own trailing standalone `image` block — confirmed
on a real table (an "Ape image" cell). Root cause: `grp['images']`
(parser.py) legitimately needs to keep including cell images (`page.images`'
own total relies on it, and that part was already correct), but
`_build_blocks`'s trailing-image loop had no way to know a given ref was
already rendered inline elsewhere — unlike the existing `used_thumb_refs`/
`used_file_icon_refs` skip mechanism, nothing equivalent existed for cell
images. Fixed with a matching `used_cell_image_refs` skip set, built
alongside the existing `cell_image_idents` tracking in `_cell_image_builder`.

**Images could import both smaller than OneNote's own display size AND
visibly distorted.** `images._build_page_image` capped a displayed image's
reported `width` at `_MAX_IMAGE_DISPLAY_WIDTH` (600px, "a judgment call
inherited unchanged from the tool this package was extracted from," never
verified against real data) but left `height` at the real, uncapped stored
value — distorting the aspect ratio. Confirmed on a real embedded PDF
preview: OneNote's own MHT export renders it at 793x1122 (its real, stored
`LayoutMaxWidth`/`LayoutMaxHeight`, confirmed matching pixel-for-pixel);
this tool reported 600x1122, both too narrow AND squished. Fixed the
distortion by rescaling `height` proportionally whenever `width` gets
capped (matching what `recovery.py`'s two sibling image-sizing call sites
already did correctly). Separately, verified the cap itself was too low
for legitimate content, not just for this one case: a second, different
real notebook's plain pasted photo has a genuine, correctly-stored
`LayoutMaxWidth`/`Height` of 972x1258 (again confirmed matching OneNote's
own MHT export exactly) — two independent, different-content-type real
checks with **zero discrepancy** between the stored property and OneNote's
real display size. That's evidence the property is trustworthy, not
evidence it needs shrinking to "a typical size" — real notebooks routinely
store `LayoutMaxWidth` up to ~2400px for ordinary pasted screenshots.
Raised `_MAX_IMAGE_DISPLAY_WIDTH` to 4000, now a genuine safety-net ceiling
against a pathological stored value rather than a guess at "normal" size.

**A paragraph-level font size could be silently dropped.** A citation-style
paragraph's own, genuinely different `FontSize` (a real 9pt vs. an ordinary
body paragraph's 11pt) lives only on `ParagraphStyle`, with no
`TextRunFormatting` override — since the run's real size already equals
its own paragraph's default, OneNote correctly writes no per-run override.
`rendering._resolve_run_format` used to read font size/family ONLY from
`TextRunFormatting`, on the theory (see the superseded comment this
replaces) that `ParagraphStyle`'s own copy is always just "the same boring
page-wide default" not worth surfacing — disproven by real data: OneNote's
own MHT export puts an explicit `font-size`/`font-family` on literally
EVERY paragraph's own `<p style=...>`, not just distinctively-styled ones.
Confirmed on a real page (Test/Rich content/"Section page 1"): a `cite`-
styled citation line ("From <https://www.lipsum.com/>") imported with no
font-size at all, same visual size as surrounding 11pt body text, when
OneNote itself renders it noticeably smaller (9pt) and in gray. Fixed by
reading `ParagraphStyle`'s own `FontSize`/`Font` as the base value (same
pattern already used for bold/italic/color/highlight in this function),
with `TextRunFormatting`'s own override still taking precedence when
present. Verified: the citation line now matches OneNote's MHT export
exactly (`font-size:9pt;font-family:Calibri`), and a 4-page real "Font
sizes" test (11pt/12pt/36pt/9pt) still resolves every size correctly
post-fix, cross-checked against the same MHT ground truth. This is a
broad-blast-radius change — every paragraph now carries an explicit
font-size/font-family span, not just ones that differ from a page's most
common size — deliberately, since that's what the real ground truth does;
verified crash-free and with unchanged page/block counts across every
bundled test file and two large real notebooks (Test.onepkg, DIY.onepkg).

**"Two-column layout collapsing to one column" and "images stacking at
identical (x,y)" — investigated, NOT a bug in this repo.** Both were
explicitly flagged for investigation but don't reproduce here:
`ordering._page_regions` correctly detects multiple independent floating
boxes on real multi-column/multi-box pages (verified: a real 2-column
bulleted/numbered list page resolves to 2 distinct regions at different
x-offsets; a real "GBA cartridge" page resolves to 5). Separately,
`ordering._page_content_order` gives 3 real stacked images on one page 3
genuinely distinct, correctly-ordered `order_index` values — no collision.
If a consuming application still renders these as one column or
overlapping images, that's downstream — either it's using the flat
`page.blocks` view (deliberately reading-order-only, see
[Documentation.md](../Documentation.md)) instead of `page.regions` for a
page that has real floating-box structure, or its own layout code isn't
deriving distinct positions from `order_index` for content this tool
already gives it a real, resolvable order for.

**A real table's row count and a table-cell's own row survival — checked,
no bug found.** A specific real table ("1. Real table with actual data")
suspected of losing its last row matches OneNote's own MHT export exactly
(4 rows: header + 3 data rows, byte-for-byte identical content) — does not
reproduce on the available test data.

## Section order: real bug found and fixed, one narrow residual case left

`convert.unpack_onepkg` used to list `.one` files in plain alphabetical
order — never matched OneNote's own sidebar order at all, confirmed
against a real user's notebook and screenshot (e.g. a real section named
"MINI" sorted way out of place alphabetically).

Fixed via `convert._toc_order`: each extracted folder's own `Open Notebook
.onetoc2` (OneNote's table-of-contents file, present alongside the `.one`
files at every folder level) encodes, per entry, a real, previously
undocumented shape — reverse-engineered from real notebooks, not from spec
memory, since pyOneNote has no parsing class at all for this file's node
type (`ObjectDeclarationWithRefCountFNDX`, confirmed absent from
`FileNode.py` entirely, not just unnamed): 16-byte GUID, 4-byte
little-endian order index, 4-byte little-endian byte-length (incl. the
UTF-16 null terminator), then the name itself as null-terminated UTF-16LE.
The byte-length field doubles as a free correctness check for the
brute-force, every-offset scan `_toc_order` uses to find these (verified
zero false positives on two real, unrelated notebooks). `unpack_onepkg`
sorts each folder's own children by this index (files before subfolders,
matching a real, verified behavior of OneNote's own sidebar: section
groups always sort after every individual section at the same level, not
interleaved by raw index) — falling back to alphabetical for anything the
index doesn't cover.

Verified against a real notebook's screenshot: all 9 top-level individual
sections, and all 5 of one section-group's own children, matched exactly.
A second, larger real notebook (61 sections across 5 groups) converts
with no crash and internally-consistent ordering.

**A real, related bug found and fixed while verifying this**: a
non-ASCII entry name (e.g. "Meie näitus") decodes CORRECTLY straight out
of `.onetoc2` — but the on-disk `.one` filename `expand.exe`/`cabextract`
actually produced for that same section is mojibake'd (see above) at
extraction time. `unpack_onepkg`'s lookup was keying purely off the
correctly-decoded name, silently missing the mojibake'd on-disk filename
entirely and falling back to sort-last for every affected section —
confirmed on the real notebook: "Meie näitus" (real index 16 of 18) was
sorting dead last instead. Fixed by also indexing each name under its own
forward-mojibake'd form (the same Latin-1/UTF-8 round-trip `_fix_mojibake`
undoes, run in the opposite direction) so the on-disk lookup hits either
way.

**Known residual limitation, not yet resolved**: the relative order
BETWEEN multiple sibling section-GROUP folders themselves (as opposed to
group-vs-section, which is correct) doesn't fully match the real index
field in the one confirmed counter-example checked — a real notebook's
"PC Builds" group has a lower raw TOC index than its "Arduino" group, but
the real sidebar screenshot shows Arduino listed first. Each group's OWN
children are internally correct; only the groups' mutual order can be
wrong. Left unresolved rather than guessed at — a repeated ~20-byte
prefix pattern preceding each group's GUID looked like it might hold a
separate ordering field, but this wasn't confirmed before landing the fix
above, and the fix above is a strict improvement over the prior plain
alphabetical sort either way (was wrong for both section-vs-group AND
group-vs-group before; now only group-vs-group can still be wrong).

## A page's content order lost a node when reachable from more than one page — real bug found and fixed

Reported symptom on a real notebook: a header rendered AFTER a list that
structurally followed it on the same page. Root cause: `ordering
._page_content_order`'s `seen`/`order` tracking was shared across EVERY
`jcidPageManifestNode` root in the file, not reset per page.

A node's FIRST visit — across the WHOLE file, not just its own page —
decides whether it ever gets an order slot. If that first visit happened
via a stale/duplicate revision-store reference from an unrelated page's
`TitleNode` (the `in_title` branch never touches `order`, only
`title_ids`), the node got marked `seen` there and nothing else. Its real,
later visit as body content on its OWN actual page then hit the `nid in
seen` guard and returned immediately — permanently missing from `order`,
landing on `_build_blocks`'s `10**9` trailing-fallback, sorting after
every sibling that DID get a resolved position.

Verified before fixing, not assumed: a synthetic repro (mirroring
`test_ordering.py`'s existing outline/page-guid-sharing pattern) —
an unrelated "stale" page's title reaching the same node identity as a
real page's own header — reproduced exactly this: the header absent from
`order` entirely while a list item declared after it on the real page got
a real value. Fixed by giving each page-manifest root its own fresh `seen`
set (`order`'s own `nid not in order` check already prevents a genuinely-
resolved value from being overwritten, so this only stops the permanent,
wrongful block). Re-ran the repro against the fix: header now gets a real
order value and correctly precedes the list item. Added as a permanent
regression test (`test_page_content_order_resets_seen_per_page_manifest_root`).
Full test suite unaffected (no other test's expectations changed).

## A run's own guid is an outline-box guid, not always the page's — real, wide-reaching bug found and fixed

Found triaging a large batch of real-notebook bugs a user reported after
using this tool for real (misplaced pages, empty `page.regions`, pages
that split in two, an image trailing far from where it actually sat).
Root cause, traced back to one place: `_run_guid` (the mechanism EVERY
page-grouping decision in `parser.py` is built on) resolves a text run's
`TextRunFormatting` reference to a base guid — and that guid is the base
guid of the **outline box** (floating text area) the run structurally
sits in, not necessarily the page's own "official" identity from
`jcidPageMetaData`/`jcidPageManifestNode`. These are the SAME base guid
for a page with exactly one outline box created directly (the common
case, which is why this went unnoticed through extensive earlier
testing) — but DIFFERENT whenever a page has more than one floating box,
which turns out to include cases created by perfectly ordinary editing
(revision-duplicate outline declarations, multi-box layouts, pages with
a table plus body text in separate boxes).

Verified on real files: **every** page checked in a real notebook had at
least one run whose own resolved guid did NOT match its page's
`jcidPageMetaData` identity. Left unmapped, this had several real,
independently-reported symptoms, all traced to this one cause:

- `page.regions` came back empty for pages that structurally DO have real
  floating-box data — `regions_by_page`/`page_title_by_guid` are both
  keyed by the true page guid, and `current_guid` (from `_run_guid`)
  simply never equalled it for these pages.
- A page's `sub_level` (indent) silently reset to 0 for the same reason —
  `page_level_by_guid` is keyed the same way.
- Worse: a page could **silently split into two** — a run in a
  secondary outline box became its own separate `PageResult`, showing up
  as a spurious, oddly-titled or "Untitled page" entry holding content
  that visually belongs to the FIRST page. Confirmed on a real notebook
  page: an "ESP32 WiFi radar" page converted to 0 blocks while a
  neighboring "Untitled page" held its actual YouTube embed.
- Confirmed on the bundled test notebook: a revision-duplicate
  declaration of a real table produced an entire spurious extra page
  ("Untitled page") holding a full second copy of that table.

Fixed in `parser.py`: `ordering._page_regions`'s own walk (already
computed, for `page.regions` itself) already builds the correct,
STRUCTURAL association between an outline box and its true page — every
entry in `regions_by_page[page_guid]` names that page's own outline boxes
by their full identity. Index it in reverse (an outline box's own base
guid -> its true page's guid) and remap every run's resolved guid through
it before grouping. Verified collision-free on every real file checked —
an outline box belongs to exactly one page, never shared between two, so
this is recovering a mapping the file's own structure already encodes
elsewhere, not a "which reference wins" guess like the reverted 0.1.0
"sibling consensus" fix documented above. Verified with a full
before/after content diff on every bundled test file: page/block counts
identical except for the one intentional, verified-correct merge (the
spurious "Untitled page" table duplicate, above) — no other regressions
found.

**Not fully closed by this fix**: a run/outline box whose own structural
path back to ANY page is missing entirely (not just to the WRONG page —
genuinely absent from `_page_regions`'s own walk too) has no signal this
mapping can recover, since there's nothing to index. Confirmed still
present on real files after this fix (the "2020-03-22" spurious page
in the bundled test notebook, holding scattered table-cell fragments; the
"ESP32 WiFi radar"/"Untitled page" split above specifically). This is a
deeper case of the same revision-store duplication family documented at
the top of this file — not attempted further for the same standing
reason: no verified, non-guessing signal was found to close it, and this
project doesn't land page-attribution fixes without one.

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
- **Still open, confirmed at real scale**: a page's own text runs can carry
  a `TextRunFormatting` reference pointing at a DIFFERENT page's guid
  entirely (not just a stale revision of their own page) — the run's text
  is correctly extracted, just grouped under the wrong page. Confirmed on a
  real ~250MB notebook: a page whose own heading read "Bugs" (a real
  checklist of "To Do"-tagged bug reports, verified present in the
  notebook's own MHT export) converted to **zero body blocks** — every one
  of its checklist items had a `TextRunFormatting` pointing at a
  *different, unrelated* page's guid, and duplicate declarations of the
  exact same run identity resolved to two DIFFERENT guids (one self,
  one foreign) depending on which revision-duplicate got processed. The
  content isn't lost — it lands on the OTHER page instead (verified: a
  page titled "ToDo" absorbed checklist items whose own text visibly
  belongs to several different, separately-titled pages, "Power" among
  them) — but the per-page split is wrong. This is the exact "sibling
  consensus" shape 0.1.0 tried and broke worse (below) — no fix attempted
  here for the same reason: don't guess at cross-page attribution without
  a verified, non-regressing signal. A page converting to an unexpectedly
  low block count with a plausible-looking title is the practical symptom
  to watch for if this needs revisiting.

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

## Embedded-file/image identity resolution: a real pyOneNote bug, found small then found big

**First pass (small, real .onepkg test data)**: a real PDF attachment was
silently failing to attach to its page at all (`report.failed['file.pdf']`)
despite its bytes clearly being present in the file. Root cause: the
`jcidEmbeddedFileNode`'s `EmbeddedFileContainer` reference pointed at
revision `,33` of its base object, but pyOneNote's own `get_files()`
(last-wins per real file) had recorded the actual content under revision
`,40` of that *same* base GUID. Fixed at the time in
`images._build_embedded_file`: on an exact-identity miss, retry by base
GUID — but ONLY when that resolves to **exactly one** non-image candidate
(a base GUID is not a safe 1:1 proxy for "one real file": verified that a
page with two genuine, different audio attachments, `.wav` and `.ogg`, has
both sharing one base GUID — an ambiguous match falls through to normal
failure reporting rather than guessing).

**Second pass (the real root cause, found testing a real ~250MB, 21-section
notebook)**: that first fix was treating a symptom. The actual bug is in
`OneDocment.get_files()` itself. It keys its own dict by the file-data
blob's storage GUID, and for each declaring node
(`ObjectDeclarationFileData3RefCountFND`) does
`self._files[guid]["identity"] = str(node.data.oid)` — **last-wins, once
per blob**. When the exact same underlying blob is legitimately declared
under multiple different object identities — confirmed on the real
notebook: the identical photo pasted on two different pages (two different
base GUIDs entirely, not even a revision-counter difference), byte-identical
content — only the LAST-processed identity survives as resolvable through
`get_files()`. Every other identity that legitimately references that same
blob is unreachable, even though the bytes are genuinely present. This
single bug accounted for **135 of ~350 real pasted images silently failing
to resolve in one section alone** (`image_missing_data`), and, at the whole
notebook level, real `.wav`/`.zip`/`.pdf` attachments failing too — the
`,33`-vs-`,40` PDF case above was actually this same bug, not a simple
stale-revision-counter issue: `,33` was ALSO a genuine, independent
declaration of the identical content, just the one `get_files()` didn't
keep.

Fixed properly in `_patches.py`'s `OneDocment.files_by_identity()` (an
additional method, `get_files()` itself is left untouched — the password-
decryption path still needs it called exactly where it already is, inside
the shadow-decrypt context window its lazy `FileDataStoreObject` read
depends on): index **both directions** directly — every declaration's own
`(oid -> blob guid)` pair, not just the last one per blob — then resolve
`oid -> bytes` through that. A strict superset of what `get_files()` alone
returns, never narrower. `parser.py` now uses this everywhere
`files_by_identity` is built; the original base-GUID fallback in
`images._build_embedded_file` is left in place as a safety net for a
genuinely-dangling reference (a stale identity that was truly never
declared at all — a real, if now much rarer, possibility) rather than
removed, since it's still correct, just no longer the common case's fix.

The end-of-parse "orphan file" check (unresolved, unattached files) needed
a matching fix: it now walks `files_by_identity` itself (every real
identity) instead of `document.get_files()`'s collapsed dict, with a
content-based dedup seeded from every already-consumed identity's own
bytes — otherwise a revision-duplicate declaration of content ALREADY
correctly attached under a sibling identity (the `,33`/`,40` PDF case)
would get double-reported as a false "still missing" failure.

Verified end-to-end on the real 250MB+ notebook: `image_missing_data`
257 -> 0, `file.wav`/`file.zip` failures resolved, `image` (successfully
converted) 656 -> 704, `embedded_preview` 20 -> 31 — all on the SAME real
data, same run, only this fix changed. Also verified with a full before/
after content diff against every smaller real test file: page/block counts
unchanged everywhere, and every page's content is byte-identical **except**
where this fix recovers something the old code genuinely missed (one real
file: 2 of 22 pages differ, both gaining a previously-unresolvable audio/
video preview thumbnail — `embedded_preview` 6 -> 9, `image_missing_data`
3 -> 0 on that file) — no regressions found anywhere.

## Math equations: detected, replaced with an honest placeholder

OneNote's inline equation editor doesn't store a typeset formula — it
stores plain Unicode text (the real math glyphs, e.g. italic 𝑥/𝑎/𝑛/𝑘) mixed
with structural control characters from Microsoft's UnicodeMath "linear
format" (fraction/superscript/radical delimiters drawn from the
U+FDD0–FDEF noncharacter range — reserved by Unicode itself to never be
assigned a real glyph, invisible or shown as tofu in any real font),
flagged via `MathFormatting` on the run's character-style object
(`jcidParagraphStyleObjectForText`, referenced through `TextRunFormatting`
same as every other run-level style override). Rendering that text as-is,
which this tool used to do, produced exactly that: real math glyphs glued
together with literal noncharacter soup. Verified on a real file (the
binomial theorem expansion) and cross-checked against the MHT ground
truth — which turns out not to help here either: OneNote's own MHT export
doesn't attempt a text/MathML form of an equation, it rasterizes it to a
PNG (`<img src=...>`), same as it does for some other content this tool
can't match pixel-for-pixel.

Given no fraction/superscript layout engine exists here to interpret those
delimiters, and matching OneNote's own raster-image fallback isn't
feasible without a math typesetting engine, `parser._run_is_equation`
detects `MathFormatting` and swaps in the same visible-placeholder
convention used for every other genuinely-unsupported element
(`rendering._placeholder_run`, tracked via `report.fail('equation')`).
Stripping just the delimiter characters and keeping the bare glyphs was
considered and rejected: flattening a nested formula loses which delimiter
meant "start of an exponent" vs. "start of the whole fraction", so the
result would read as a *plausible but wrong* formula — worse than an
honest "not supported" marker, not better.

## Page background color / rule lines: implemented, verified against 13 real pages

The first attempt at this (see the superseded write-up this replaces) found
one candidate property on `jcidPageMetaData` and couldn't confirm what it
meant — no ground truth existed to check a guess against, so it was
deliberately left unimplemented. A follow-up test file added a dedicated
section with 13 pages — one per built-in OneNote rule-line pattern (Narrow
Lines, College Ruled, Standard Rules, Wide Ruled, Small/Medium/Large/Very
Large Grid), one with a plain page color, one with color + lines, one with
neither ("None") — each page's own **title** stating exactly what was set,
turning "no ground truth" into "13 independent ground-truth data points".

Both real properties live on `jcidPageNode` (not `jcidPageMetaData`, the
earlier guess), unnamed in pyOneNote's table the same way the table-merge
and table-column-width properties were (read via the raw `body` escape
hatch, `parser._page_style`):

- **Rule-line pattern** (`0x0C00352D`, a 1-byte enum): values 1–8 matched
  the 8 pattern-named pages' titles exactly, in order (1 = Narrow Lines,
  ..., 8 = Very Large Grid) — not inferred, read directly off each test
  page's own name. Absent on "None"/"Page color"/"Page size as A4", exactly
  as expected.
- **Page background color** (`0x14001D2A`, the same 4-byte RGB+alpha-flag
  shape as every other color property in this codebase): present only on
  "Page color" (`#f0f8fa`) and "Page colour and lines" (`#fceeee`) — both
  plausible pastel swatches, absent on all 11 other pages.

Exposed as plain `PageResult` fields (`background_color`: `'#rrggbb'` or
`None`; `rule_lines`: one of 8 string names or `None`) — deliberately
**not** baked into any block's `html`, unlike cell color/alignment: this is
page-level presentation metadata, not page content, and whether/how to
visualize it (a solid CSS background, an actual grid/ruled-line pattern
image, neither) is a choice for the caller, same reasoning already used for
`PageRegion`'s x/y/width/height.

## Rule-line COLOR (separate from the page-color/rule-line-pattern above): not found, future work

OneNote's "Rule Lines" submenu has its own **Rule Line Colour** picker,
independent of both page color and the ruled/grid pattern above (an 18-swatch
named palette — <none>/Light Blue (default)/Blue/Yellow/Green/Red/Purple/
Cyan/Orange/Magenta/Blue Mist/Purple Mist/Tan/Lemon Lime/Apple/Teal/Red
Chalk/Silver). Two real test pages confirm it's a genuine, independent
per-page setting: "Coloured lines" (body text: *"This page has orange
colored lines"*) and "Page colour and lines" (screenshotted directly by the
user: pale-red page color + body text *"This page has 'Red' colour and
lines with 'Red Chalk' colour"*) — same rule-line pattern (`college_ruled`)
as plain "College Ruled", same or absent page-color as their siblings, but
a visibly different line tint neither `rule_lines` nor `background_color`
above accounts for.

**Exhaustively searched, not found**, in two independent passes:

1. A full key-by-key diff of "College Ruled" (default line color) against
   "Coloured lines" (orange) — every raw PropertyID on their respective
   `jcidPageNode` objects, not just the unnamed ones `_page_style` already
   reads. Identical key sets; the only value differences are structural
   refs and timestamps.
2. A whole-file scan of every unnamed ("Unknown"-named) PropertyID across
   all 13 real pages in the test section, looking for anything whose
   occurrence count matched "exactly the pages known to override the
   default line color" (2). Nothing did — every unnamed property found
   is already accounted for by `rule_lines`/`background_color` above or by
   unrelated per-object bookkeeping (author records, revision counters).

Also disproved, along the way, this project's own earlier guess from before
this test data existed: an unnamed `jcidPageMetaData` property
(`0x1C0035CD`) once suspected of being a page-theme/style reference turns
out to be a monotonically-increasing per-notebook counter (a fixed 16-byte
prefix + a trailing value that increases by 4 on every single page in
creation order) — unrelated to color, confirmed now that real comparative
data across many pages exists to check it against.

**Conclusion**: rule-line color isn't a property of the page's own object
space at all. Left as real future work — the next things worth trying, in
order of likely payoff: (a) check whether it's stored in the *notebook's*
own metadata rather than per-page, since the palette's "default" checkmark
(Light Blue) suggests a notebook- or app-level default that a page might
only override via a *reference* rather than an inline value; (b) check
whether it hangs off the page's outline/canvas structure object instead of
`jcidPageNode` directly; (c) get a raw byte-level diff of two `.one` files
that are identical except for this one setting (the fastest path, if a
contributor can produce one) rather than continuing to reason from the
already-decoded property names. Not attempted: guessing a value from the
palette's swatch position (e.g. "Orange" = index 8) and shipping it
unverified — same standing rule as everywhere else in this file, a property
whose real storage location is unconfirmed doesn't get landed on a guess.

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

## CLI: a Windows console encoding crash, found converting a real notebook

Real bug, found running `onenote-tool convert` on a real ~250MB notebook:
it crashed with `UnicodeEncodeError` after **1m18s of successful
conversion work**, deep inside `typer`/`click`'s own `echo()`, printing a
page title. Windows' console defaults to a legacy codepage (cp1252), not
UTF-8 — any printed text containing a character outside that codepage
(a real page title here: `'￼￼￼'`, Unicode's OBJECT
REPLACEMENT CHARACTER, from a page whose title area holds only embedded
objects) crashes the whole process, losing the conversion report for a
display-only reason with no connection to parsing correctness at all.

Fixed in `cli/main.py`: reconfigure `sys.stdout`/`sys.stderr` to UTF-8 with
`errors='replace'` (never raises; an unencodable character prints as
U+FFFD instead of crashing) at **import** time, not only under
`if __name__ == '__main__':` — the installed `onenote-tool` console script
calls `app` directly without going through that guard, so a fix placed
there wouldn't apply to a real install. Verified: the same real 250MB
notebook now converts and prints its full report without error.

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
