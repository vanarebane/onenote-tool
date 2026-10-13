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

**Root cause pinned down AND now fixed — comma specifically, confirmed
twice.** A hex dump of the SOURCE `.onepkg`'s own `Open Notebook
.onetoc2` bytes (not anything either extraction tool produces) shows the
name field already reads `Skills` + `5e 00 4a 00` (`^` U+005E, `J`
U+004A, literal two-character `^J`, NOT a control character — a real line
feed would be `0a 00`) + ` Tools.one` — confirmed at the exact byte
offset, both before and after CAB extraction, identical either way. This
is baked into the notebook file Microsoft's own OneNote client produced,
not something `expand.exe`/`cabextract`/this package introduces.

Originally left unfixed ("one observed example, no safe un-escaping
rule"), then confirmed as a real, general, SAFE-to-fix pattern: purpose-
built real test data covering every OTHER ordinary punctuation mark a
OneNote section name can contain (semicolon, period, exclamation,
apostrophe, hyphen, equals, parens — `?*\/:<>|&#"%` are rejected outright
by OneNote's own section-naming UI, confirmed by the user directly) found
**zero** other corrupted names — only a second, independent comma case
("Coma, Test" -> "Coma^J Test"), byte-identical corruption shape to the
original ("Skills, Tools" -> "Skills^J Tools"). Two independent real
reproductions with no counter-example among 7 other punctuation marks is
enough to fix, unlike a single observed example. Fixed with
`convert._fix_comma_corruption` (`'^J'` -> `','`), composed with the
existing mojibake fix via `convert._fix_extracted_name`. Unlike the
mojibake fix, this has no self-verifying round-trip property — confidence
comes from the two clean reproductions plus the negative-result punctuation
sweep, not from a mathematical property of the transform. If a real
section name is ever found that's supposed to contain a literal `^J`,
this will incorrectly mangle it — accepted as a real but very unlikely
risk. `convert.unpack_onepkg`'s section-order fix matches a section by its
still-corrupted on-disk filename — ordering is unaffected by this fix,
only the display name is (the fix runs on `SectionResult.name`/
`group_path`, not on `unpack_onepkg`'s own returned file paths).

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

**Not fully closed by this fix — since closed**: a run or outline box with
no structural path back to any page (the "2020-03-22" spurious page in the
bundled test notebook, holding scattered table-cell fragments) had no
signal this mapping could use. The object-space attribution described in
"Revision-store duplication" below gives one: every declaration lives in
its page's own object space, so it no longer depends on references at all.
That page is gone, and its fragments are in their real table.

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

## Thread safety

`_patches.py`'s parse state (`_active_decrypt`, `_active_marker_probe`) is
thread-local, so parsing different files in different threads is safe —
verified: four sections (two protected) parsed concurrently give the same
output as parsing them one after another. A parse is synchronous, so
asyncio tasks sharing a thread can't interleave inside one.

## Importing this package has a process-wide side effect

`onenote_tool._patches` monkeypatches `pyOneNote`'s classes **at import
time**, unconditionally, for the lifetime of the process. If your
application also imports `pyOneNote` directly for something unrelated, it
will observe these patches too. This is deliberate (it's the only way to
fix real bugs/gaps in a third-party library without forking it), but worth
knowing if you're debugging something that touches `pyOneNote` directly.

## Revision-store duplication: root cause found and fixed

Every page is its own object space, and each object space keeps several
revisions, each in a *revision context*. Only the default context (no
context id) is the page as it is now. The others are:

- one per older version of the page (OneNote's page version history): a
  complete snapshot with its own PageNode, TitleNode, PageMetaData and text,
  often more than half of all declarations in a section;
- one shared context (`7111497f-1b6b-4209-9491-c98b04cf4c5a`) holding
  version-history bookkeeping (`jcidVersionHistoryMetaData`,
  `jcidVersionProxy`).

pyOneNote reads every declaration in every revision into one flat list.
That single fact explains the problems this section used to list as open:
a renamed page's old and new titles under one identity (the old one is in a
snapshot), runs carrying another page's guid, and spurious extra pages.
Verified on real notebooks: every page has exactly one default-context
revision, the snapshots hold the earlier titles (e.g. "Breaks" for a page
now titled "Pidurid"), and OneNote's own MHT export contains only
default-context content.

Fixed in two parts:

1. `_patches._get_properties` reads only each object space's current
   revision — the default-context revision (or the one a
   `RevisionRole*DeclarationFND` assigns the default role), plus its
   `ridDependent` chain, with identities already seen skipped.
2. Each property records the object space it came from, and
   `parser.parse_one_file` assigns a declaration to the page whose object
   space it lives in (`page_of_space`). The reference-derived guesses
   (`_run_guid` from `TextRunFormatting`, `PictureContainer`, iteration
   order) only fill in for declarations outside any page's object space.

Measured against OneNote's own MHT export, by page title and word recall:

| | before | after |
|---|---|---|
| Test.onepkg pages / tool-only titles | 69 / 1 | 66 / 0 |
| DIY.onepkg pages (MHT: 325, some sections not exported) | 425 | 340 |
| DIY word recall / precision vs MHT | 0.900 / 0.926 | 0.960 / 0.948 |
| Shared.onepkg pages (no MHT available) | 1131 | 917 |

Words that disappeared from the Shared notebook were checked: they exist
only in version-history snapshots, i.e. text the user had since deleted.
The same goes for attachments: a file that only an old snapshot still has
was removed from its page, and is no longer reported as a failed import.
The "2020-03-22" spurious page and the duplicate "Mixed content" page in
the test notebook are gone (the corpus manifest now asserts both).

Still open: a page made of several floating boxes is read box by box in the
page's own box order, while the MHT export orders them by position — e.g. a
box of To Do items can come before a heading that sits above it.

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
the decrypt context window its lazy `FileDataStoreObject` read
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

## Math equations: MathML for verified structures, placeholder otherwise

An equation run's text is RichEdit's internal math form: the glyphs plus
U+FDD0 (start of a math object), U+FDEE (argument separator) and U+FDEF
(end of object). The text doesn't say what each object is. That's in the
run's `TextRunData` — an ArrayOfPropertyValues, readable only since the
nested-property-set fix below — with one nested set per text run (run
boundaries in `TextRunIndex`). The run that starts an object carries its
type (`0x1400344F`) and its operator/delimiter characters (`0x10003453`,
`0x10003454`). The type values are RichEdit's own math object types (TOM:
`tomBrackets` 13, `tomNary` 21, `tomStack` 27, `tomSuperscript` 31, ...).

`equations.equation_mathml` turns that into MathML for the four types
verified against the real test equation (the binomial theorem): brackets,
n-ary operator with limits, stack (binomial coefficient) and superscript.
An equation containing any other type keeps the placeholder, so no layout
is guessed. Adding a type needs one real sample to confirm its argument
order. OneNote's own MHT export rasterizes equations, so it isn't a check.

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

## Rule-line color: found, implemented

OneNote's "Rule Line Colour" setting is property `0x14001CD5` on the page's
`jcidPageNode`: an ARGB uint32 (little-endian, so the bytes are B, G, R, A),
written on every page that has rule lines. Verified on the 13-page test
section: every page with the default color reads `0xFFCAEBFD` (light blue),
"Coloured lines" (orange) reads `0xFFF8E0C8`, the "Red Chalk" page
`0xFFF8DCDC`, and pages without rule lines have no value. Exposed as
`PageResult.rule_line_color` (`'#rrggbb'`).

Two earlier exhaustive searches missed it. Both diffed decoded properties
at a time when the parser mixed in version-history snapshots and truncated
any object after an ArrayOfPropertyValues (see below), so this value never
showed a clean per-page difference.

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
`ObjectDataEncryptionKeyV2FNDX` marker, before any password/decrypt
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
`_active_marker_probe` flag; the real decrypting second pass, and every
unprotected-file parse, are untouched. Verified byte-identical
`PageResult` output (all three real test sections) and unchanged
`password_status` (`needs_password`/`wrong_password` cases included) before
vs. after, by toggling just this one patch in isolation. Real-file result:
**~39s to ~1.7s** for the password-protected section.

## Password-protected sections: full parity with a correct password

With a verified correct password, a protected section decrypts exactly (see
[ms-offcrypto-encryption.md](ms-offcrypto-encryption.md) for the format)
and goes through the same pipeline as an unprotected one. Verified against
the test notebook's protected section and its unprotected twin: identical
output for all 20 pages — text and formatting, lists, tables (incl. merged
cells and cell images), links, YouTube embeds, ink, author attribution,
image and attachment bytes, `page.regions`, dates, background/rule lines,
page levels.

What remains:

- Only one real protected section with a known password has been tested.
  The format was consistent across all 2203 objects in it, but a section
  saved by a different OneNote version, or with a different cipher in its
  `<encryption>` descriptor, hasn't been seen.
- With **no** or a **wrong** password, nothing past the structure is
  readable: the section becomes one placeholder page (`password_status`
  `needs_password` / `wrong_password`). Page titles are encrypted too, so
  even the page count isn't recoverable.
- `recovery._flat_recover_protected_pages` (structural page recovery built
  for the old, partial decryption) is now only a fallback for a section
  that decrypts but yields no pages through the normal pipeline. It hasn't
  been hit on any real file since the exact decryption landed.

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

Password-protected sections get `regions` too, the same way (earlier
partial decryption never recovered the object chain it needs; exact
decryption does). `blocks` remains authoritative for content either way;
`regions`
is a purely additive, best-effort second view when it's available at all.

## YouTube thumbnail matching needs network access

`images._youtube_thumb_hashes` makes a real outbound HTTP request (to
`img.youtube.com`) to perceptually match an embedded preview image to the
actual YouTube thumbnail. This is the **only** network call anywhere in
this package, and it's best-effort — a failure just leaves the embed
without a matched preview. Turn it off with `ONENOTE_TOOL_OFFLINE=1` (or
`onenote-tool convert --offline`).

## pyOneNote dropped every property after a nested property set

pyOneNote's PropertySet reader doesn't implement property type 0x10
(ArrayOfPropertyValues — raises) or 0x11 (a nested PropertySet — calls the
constructor with four arguments missing). The object was truncated there
and every later property lost. A paragraph with a note tag carries
`NoteTagStates` (an ArrayOfPropertyValues) before its text, so **every
tagged paragraph silently lost its text** — on a real notebook, a whole page
of To Do items converted to nothing. `_patches._read_propset` implements all
types per [MS-ONESTORE] 2.6.6–2.6.9 (nested sets read their object/space/
context references from the parent's streams, in order), and
`get_properties()` returns nested sets as dicts. pyOneNote's name table also
had NoteTagStates under the wrong id (`0x04003489`; the real id carries the
type bits, `0x40003489`).

Note tags are now rendered too (`rendering._note_tags`): label and shape from
the tag's `jcidNoteTagSharedDefinitionContainer`, checked state from
`ActionItemStatus` bit 0 (agrees with a non-zero `NoteTagCompleted` on all
~390 real tags checked). Shape 3 is the checkbox in every real To Do-style
tag seen (four labels, two languages).

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

## Per-author attribution: implemented at paragraph granularity, real properties found

Table A used to mark this "confirmed present, nothing reads it" — `Author`/
`AuthorOriginal`/`AuthorMostRecent` are named properties in pyOneNote's own
`_property_id_name_mapping` (`0x1C001D75`/`0x20001D78`/`0x20001D79`), just
never wired into anything here. Raw-scanning a real 66-section/1131-page
multi-author shared notebook (synced between 2 real people over years)
found the real structural chain:

- Every `jcidRichTextOENode` (a run's own text node) is a direct child —
  via `ContentChildNodesOfPageManifest`, the same child-list property
  `list_runs` already reads for list membership — of exactly one
  `jcidOutlineElementNode` (a paragraph). The run's own node carries no
  author info at all; only its enclosing paragraph does. This makes
  per-*run* attribution (as Table A's row used to phrase it) not something
  the format actually supports — per-*paragraph* is the real ceiling, and
  what's implemented.
- `jcidOutlineElementNode.AuthorOriginal` is a single-element `ArrayOfObjectID`
  reference to a `jcidReadOnlyPersistablePropertyContainerForAuthor` object,
  whose own `Author` property is the real display name as a plain UTF-16
  string — no further indirection needed once you have that object.
- Resolving `AuthorOriginal` -> container -> `Author` string across every
  real text run in the whole file (all 66 sections, both `.one` files that
  make up the notebook's revision-store trees) succeeded for 72293/72339
  runs (99.9%). `AuthorMostRecent` resolves for slightly more (72339/72339
  in the same run) but was deliberately NOT used — "who wrote it" (the
  row's own stated goal) is `AuthorOriginal`; `AuthorMostRecent` answers a
  different question (who last touched it) that Table A doesn't ask.
- `parser.py`'s `author_by_run` builds this map once per section, the same
  way `list_runs` does; `rendering._author_attr` emits it as
  `data-author="..."` on the paragraph's own `<p>`/`<h#>`/list-item tag
  (not a nested `<span>` — same reasoning as paragraph alignment: this is a
  paragraph-level property, not a per-character one). A run whose author
  can't be resolved (the 0.1% gap above) gets no attribute at all — the
  same "never guess" fallback the row already specified before this was
  implemented.
- **Protected sections: same result as unprotected.** With exact
  decryption the protected test section resolves the same `data-author`
  values as its unprotected twin. (The earlier partial decryption resolved
  0/564.)
- **Table cells: wired in, verified.** A cell's own text runs are collected
  by a separate walk (`rendering._collect_cell_runs`, reached via
  `_resolve_table_grid`/`_resolve_cell_html`) that didn't originally have
  access to `author_by_run` — now threaded through all three as an optional
  trailing parameter, same pattern `list_runs`/`image_builder` already use.
  A cell's text run is the same `jcidRichTextOENode` type as body text, so
  no cell-specific resolution logic was needed, just the plumbing. Verified
  against the same real multi-author file: 1225/1394 real table paragraph
  tags resolved a `data-author`.

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
