# OneNote file format — what this project has actually learned

This is a practical field guide to `.onepkg`/`.one`, written from what was
verified against real files while building this tool, not a restatement of
Microsoft's published [MS-ONESTORE]/[MS-ONE] specs (which this project
relies on `pyOneNote` to implement the low-level parsing for — this doc is
about the parts *around and beyond* what that library gets right).

## The container: `.onepkg`

A `.onepkg` export is a **CAB archive** (Microsoft Cabinet format)
containing one `.one` file per section, with subfolders representing
OneNote **section groups** (nested folders of sections). This tool unpacks
it with Windows' own `expand.exe` when available, falling back to the
`cabextract` CLI on Linux/macOS (see
[known-limitations.md](known-limitations.md) for details) — a section's
group path is just its relative directory inside the extracted CAB.

The section's *display name* the user sees in OneNote is generally **not**
recoverable from the `.one` file itself — pyOneNote can't extract it, and
neither can this tool; the `.one` filename (minus extension) is used as the
section name instead, which is usually — but not always — what the user
actually named it.

## The section format: `.one` (MS-ONESTORE)

Each `.one` file is a binary tree of **FileNode**s, organized into
**FileNodeList**s (fragmented into chunks with continuation pointers).
Object content lives in **PropertySet**s, addressed by an
**ObjectSpaceObjectPropSet** (which itself references three compact-ID
streams — OIDs, OSIDs, ContextIDs — used to resolve references to other
objects). Every object has a **jcid** (a type tag) and an **oid**
(`CompactID` → `ExtendedGUID`, i.e. a base GUID plus a small revision
counter).

Key structural facts, all verified empirically against real files:

- **A `FileNode`'s own position in the file (`ref.stp`, or the node's own
  start offset for an inline-`baseType` node) is structural — real,
  unencrypted plaintext, even inside a password-protected section.** Only
  each object's PropertySet body and each embedded file's payload are
  ciphertext — see
  [ms-offcrypto-encryption.md](ms-offcrypto-encryption.md).
- **A page's declarations sit contiguously, in file-position order.** A
  page's `jcidPageMetaData` (its title), `jcidRichTextOENode`s (its body
  runs), and file/ink declarations are laid out one after another, ending
  exactly where the next page's title declaration begins — *usually*. See
  the revision-duplication note below for the real exception this runs
  into.
- **`pyOneNote`'s own jcid name table is a small hardcoded subset.**
  Anything outside it (ink/drawing objects, in particular — see below)
  decodes as the literal string `"Unknown"`, indistinguishable by name from
  any other unhandled type. The *raw* jcid integer is always available
  though, and is what this tool matches on for anything pyOneNote doesn't
  name.
- **Shared character/paragraph style containers** (bold/italic/color/
  highlight/heading defaults) live in a separate GUID space
  (`df993cda-…`) as `ReadOnlyObjectDeclaration2*`/`*Large*` nodes, which
  pyOneNote's own `get_properties()` never reads at all (only
  `ObjectDeclaration2RefCountFND` is handled upstream) — without patching
  that in, every `TextRunFormatting`/`ParagraphStyle` reference resolves to
  nothing and all formatting is silently lost, even for a completely
  unprotected file.

### Font size is half-points; the paragraph default is not the run override

`FontSize` (on both `ParagraphStyle` and `TextRunFormatting` objects) is 2
bytes, little-endian, in **half-points** — not documented anywhere found,
confirmed empirically: a run labelled "X Large 36" carried the raw value
`0x0048` = 72 half-points = 36pt; "X small 9" carried `0x0012` = 18 = 9pt.
`Font` (the family name) is a plain NUL-terminated string on the same two
object types.

The two objects play different roles, same pattern as bold/italic/color
elsewhere in this doc: `ParagraphStyle` always carries the paragraph's own
default font size/family (present on essentially every run), while
`TextRunFormatting` only carries a `FontSize`/`Font` at all when OneNote
itself decided a specific run's font differs from that default — confirmed
directly: a page demonstrating four sizes had `TextRunFormatting.FontSize`
on the three non-default runs and *none at all* on the "Normal"-sized one,
even though `ParagraphStyle.FontSize` was present on all four regardless.
`rendering._resolve_run_format` reads font size/family from
`TextRunFormatting` only, for exactly this reason — reading
`ParagraphStyle`'s copy too would wrap every paragraph in a same-as-default
span.

### Paragraph alignment lives on the run itself, not a style object

`ParagraphAlignment` — unlike every other formatting property in this
doc — is a single raw byte read directly off the `jcidRichTextOENode`
run's own property set, not off its referenced `ParagraphStyle` or
`TextRunFormatting` object. Confirmed against a real file with four
alignments demonstrated: a "center-middle" labelled box carried `b'\x01'`,
two "right corner" boxes both carried `b'\x02'`, and every left-aligned
box (the common/default case) had no `ParagraphAlignment` property at all
— same "only written when it differs from the default" pattern as
`TextRunFormatting.FontSize` above, just on a different object. No `0` or
`3` (presumably justify) value has been observed yet, so
`rendering._paragraph_alignment` only maps `1`→`center`/`2`→`right` and
leaves anything else unmapped rather than guessing.

### A run's text lives in EITHER TextExtendedAscii OR RichEditTextUnicode, never both

Verified directly on a real file (checked every `jcidRichTextOENode` in a
section): 0 runs ever had both `TextExtendedAscii` and `RichEditTextUnicode`
set — it's strictly either/or, not a fallback pair. `RichEditTextUnicode`
isn't exclusively for hyperlink fields either, despite every example
elsewhere in this doc involving one: a run's PLAIN text (no `HYPERLINK`
marker at all) lands there instead of `TextExtendedAscii` whenever it needs
a character `TextExtendedAscii` can't represent — confirmed with a real
table column of currency values ("13911€") where every cell's content was
`RichEditTextUnicode`-only, `TextExtendedAscii` genuinely absent (`None`).

**Real bug this caused, fixed in three call sites** (`parser.parse_one_file`,
`rendering._resolve_cell_html`, `recovery._pages_from_structural_groups`'s
`_run_entry`): all three used to gate reading `RichEditTextUnicode` at all
on it containing the hyperlink marker (`_RTU_MARKER`) — a run whose RTU had
real plain text but no marker fell through to `TextExtendedAscii`, found it
`None`, and produced empty text, silently dropping the run's entire content.
`rendering._rtu_to_inline_html` already handled the no-marker case
correctly (it just returns the plain text, linkified) — the bug was purely
in the callers' gating condition, not the decoder itself. Fixed by checking
"is RichEditTextUnicode present and non-empty" instead of "does it contain
the hyperlink marker" — `_youtube_from_rtu`/`_rtu_to_inline_html` still
correctly fall through to plain-text handling on their own when no marker
is present, so nothing needed to change downstream of that check.

### ISO-format dates are real content, not a OneNote auto-stamp

`stamps.classify_stamp` used to also match bare `YYYY-MM-DD` as a page
date/time auto-stamp (meant to strip OneNote's own auto-inserted "created"
line from body text). **Real false positive, verified against a real
file**: a data table's "Join Date" column (genuine typed content, e.g.
"2021-01-15") was silently dropped from every row — matched as a stamp,
which classifies it as metadata rather than cell text. Every REAL OneNote
auto-stamp actually observed (see `stamps.py`'s own docstring examples)
uses a written month name ("19. juuli 2014", "April 08, 2025", ...) — never
ISO format. The ISO-matching branch appears to have been added
speculatively, without a real example ever confirming OneNote writes stamps
that way. Removed; an ISO-format real auto-stamp, if one ever turns up,
would just render as an ordinary paragraph instead of setting `created_at`
— a far cheaper failure mode than silently eating real content.

### Table cell styling properties

Found in pyOneNote's property table, confirmed against a real richly-styled
test table:

- `CellShadingColor` — a cell's own background, the SAME 4-byte RGB+
  alpha-flag shape as `FontColor`/`Highlight` elsewhere in this doc (alpha
  `0x00` = explicit color) — `rendering._color_from_bytes` already handled
  this shape, no new decoder needed.
- `ParagraphAlignment` — read directly off a cell's own text runs, exactly
  the same property (and same object — the run itself, not `ParagraphStyle`
  or `TableCellNode`) as the page-body alignment finding above. Cell-level
  and body-paragraph alignment are the same mechanism, not a separate one.
- `TableColumnWidths`, `TableBordersVisible`, `TableColumnsLocked` — live on
  the `jcidTableNode` itself (table-wide, not per-cell/-row). Not yet
  exposed anywhere in this tool's output — no field for table-level styling
  exists in the `blocks` schema today. `TableColumnsLocked`'s value shape
  wasn't fully decoded (came back as what looks like a 2-byte flag,
  `TableBordersVisible` as a plain `'True'`/`'False'` string) — connecting
  these to the "autofit columns" / "show/hide borders" UI toggles they
  correspond to hasn't been verified against multiple real examples of each
  state.
- **No merge/span property found anywhere** — checked every property on
  `jcidTableNode`/`jcidTableRowNode`/`jcidTableCellNode` this tool can read.
  A merged cell's content is stored as plain duplication instead (two
  separate `jcidRichTextOENode` objects, different identities, identical
  text, one in each cell the merge visually spans) — OneNote's own MHT
  export resolves this to a real `<td colspan=2>`, so genuine merge
  information exists somewhere, just not anywhere pyOneNote's property
  table exposes it. See
  [known-limitations.md](known-limitations.md) for why a content-equality
  heuristic wasn't used to approximate it.

### A ground-truth source worth knowing about: OneNote's own MHT export

OneNote's "Export → Single File Web Page (.mht)" produces a real,
Microsoft-authored rendering of a notebook as literal HTML+CSS — page
titles at `font-size:20pt;font-family:'Calibri Light'`, each floating box
as a `<div style='margin-top:...;margin-left:...;width:...'>` (inches,
not this tool's internal units, but the same position concept `page.regions`
exposes), real `<table>` markup, inline `font-size`/`font-family`/`color`/
`text-align` CSS matching this tool's own mappings almost field-for-field.
Both real bugs on this page were found by decoding a `.mht` file (a
standard MIME `multipart/related` document — Python's stdlib `email`
module parses it directly, no special tooling needed) and diffing its
actual rendered content against this tool's own output for the same
notebook — a full-text word-level diff surfaced both false-negatives (this
tool dropping content the MHT export has) almost immediately. A much
stronger verification signal than eyeballing block kinds/counts, since it's
Microsoft's own tooling asserting what the "correct" answer is.

An embedded file's actual bytes are reachable two different ways, and a
real notebook uses **both**, sometimes for the same logical file:

- **`ObjectDeclarationFileData3RefCountFND`** — has `.oid` directly
  (`baseType=0`, inline), plus `.Extension` and `.FileDataReference` (a
  `<ifndf>{guid}`-style reference string). This is the *declaration* —
  structurally page-local, i.e. it obeys the "declarations sit near their
  page's title" rule above.
- **`FileDataStoreObjectReferenceFND`** — has `.ref` + `.guidReference`, no
  `.oid`. This is where the *actual bytes* live, in a shared data-store
  area with **no per-page position at all** — do not expect it to be near
  anything.

`pyOneNote`'s own `OneDocment.get_files()` merges both node shapes into one
dict, but — this is a real, easy-to-miss gotcha — **keyed by
`FileDataReference.StringData`/`guidReference` (a reference string), not by
either node's own `oid`.** The OID only ends up as that merged entry's
`identity` *value*, and only for the `ObjectDeclarationFileData3RefCountFND`
half of the merge; entries that only ever got a
`FileDataStoreObjectReferenceFND` leave `identity` blank. Matching a file to
its owning page by OID equality therefore silently drops every file that
happened to arrive via the reference-only shape — this tool matches by the
same normalized reference-string key `get_files()` itself uses instead.

### Ink/drawing objects — deliberately undocumented

Ink strokes have **no public documentation at all** — a Microsoft Open
Specifications team member stated outright, in a public Q&A, "we have
decided not to document these drawing objects." They also have no entry in
pyOneNote's jcid name table, so they decode as `"Unknown"` and are
otherwise indistinguishable from any other unhandled type.

The object graph (jcids in parentheses, confirmed against a real file and
matching an independent reverse-engineering effort,
[github.com/msiemens/onenote.rs](https://github.com/msiemens/onenote.rs)):

```
InkContainer (0x60014) -[InkData]-> InkDataNode (0x2003B)
                                          -[InkStrokes]-> InkStrokeNode(s) (0x20047)
                                                                |
                                                    [InkStrokeProperties]
                                                                v
                                                StrokePropertiesNode (0x120048)
```

An `InkContainer` with no `InkData` instead has `[ContentChildNodes]`
pointing at *nested* `InkContainer`s (a group of drawings) — recurse
(capped at depth 16 in this implementation, arbitrarily but generously).

The actual stroke path encoding (`InkPath`) turns out to be the
genuinely-Microsoft-published **Ink Serialized Format** (ISF, from the
Tablet PC SDK) — specifically, its "multi-byte encoding of signed
numbers." Microsoft just never cross-referenced ISF from the OneNote specs
— same stonewalling shape as the encryption case. Values decoded from an
`InkPath` blob are **differential** (delta-encoded per dimension), not
absolute — confirmed empirically, not from any spec, by finding a
"pressure" dimension (declared range 0–32767) whose *raw* decoded deltas go
negative (impossible for a pressure reading) while the *running cumulative
sum* stays entirely in range for all points of a real stroke.

Absolute coordinate units for ink points are not documented anywhere
(onenote.rs included) — this tool renders strokes at their own relative
scale (auto-fit SVG viewBox), not matched to any particular page pixel
size.

### Revisions and revision contexts (where the "duplication" comes from)

A `.one` file is a revision store. Its structure, as read by this tool:

```
root FileNodeList
  ObjectSpaceManifestListReferenceFND (gosid)        one per object space
    RevisionManifestListReferenceFND
      RevisionManifestStart6FND / 7FND (rid, ridDependent, role, [gctxid])
        ObjectGroupListReferenceFND -> object declarations of this revision
        RootObjectReference3FND ...
      RevisionManifestEndFND
      ... more revisions ...
```

Each page is one object space; the section has its own space(s) too (page
series, TOC cache). A revision with no context id (`6FND`, or `7FND` with a
nil `gctxid`) is in the **default context** — the page as it is now.
Revisions in other contexts are:

- **page version history**: one context per saved older version of the
  page, each a complete snapshot (its own PageNode, TitleNode,
  PageMetaData, runs) — commonly more than half of a section's
  declarations, and where old titles and deleted text live;
- one shared context `7111497f-1b6b-4209-9491-c98b04cf4c5a`:
  `jcidVersionHistoryMetaData`, `jcidVersionHistoryContent`,
  `jcidVersionProxy` bookkeeping.

On every real notebook checked, each page had exactly one default-context
revision and every `ridDependent` was nil (each revision is self-contained),
but the reader follows a dependency chain if one is present. OneNote's own
MHT export contains only default-context content.

pyOneNote flattens all of this into one list of declarations; that is the
"revision-store duplication" earlier versions of this tool fought with
heuristics (stale titles, runs grouped under another page, spurious extra
pages). `_patches._get_properties` now returns only each object space's
current revision, tagged with its object space, and `parser.py` assigns
content to pages by object space. See
[known-limitations.md](known-limitations.md) for the before/after numbers.

**History (kept because the reasoning still applies):** an earlier version
had `parser.parse_one_file` let the manifest tree walk override a run's own
`_run_guid` whenever the two disagreed. On a real notebook the walk crossed
into another page's subtree via a stale manifest generation — itself a
snapshot artifact — and dropped a whole page. Reverted in 0.1.1. The
object-space attribution that replaced both doesn't depend on any
reference resolving.

### Page/section ordering

OneNote's true page order (as the user sees it, top-to-bottom in the page
list) is stored via `jcidSectionNode` → an ordered `jcidPageSeriesNode` →
ordered `ChildGraphSpaceElementNodes` (a list of **gosid**s — object-space
identifiers). This is a completely different ID space from an individual
run/title's own `oid` — bridging the two requires walking the file-node
tree and tracking which object-space gosid each node's `oid` falls under
(`ordering._object_space_order`).

A page's own **content** order (which paragraph comes before which) is a
*third*, separate thing again: pyOneNote emits an object space's properties
in *storage* order, not visual order — the real order has to be recovered
by walking each page's manifest tree
(`jcidPageManifestNode` → its ordered child-reference lists,
`ordering._page_content_order`).

### Floating boxes (canvas layout): reached via `jcidPageNode`, not the manifest

A page's title (`jcidTitleNode`) and its independently-movable content boxes
(`jcidOutlineNode`, one per floating box the user can drag anywhere on the
canvas) are **siblings**, not nested inside each other — both hang directly
off the page's own `jcidPageNode`, just via different reference keys: the
title via `StructureElementChildNodes`, the outline boxes via
`ElementChildNodesOfVersionHistory`. Getting there from a `jcidPageManifestNode`
(the usual anchor for anything page-scoped in this codebase) takes one extra
hop most other tree walks don't need: `PageManifestNode.
ContentChildNodesOfPageManifest` → the page's own `jcidPageNode` → *its*
`ElementChildNodesOfVersionHistory` → each `jcidOutlineNode`.

Each `jcidOutlineNode` carries its own canvas position/size directly:
`OffsetFromParentHoriz`/`OffsetFromParentVert` (top-left, relative to the
page) and `LayoutMaxWidth`/`LayoutMaxHeight`. Unlike `FontSize` above, these
arrive from pyOneNote as **plain ints already**, not packed little-endian
bytes — verified directly against a real 3-box page (3 distinct `(x, y)`
pairs matching its visual layout exactly); using `text._le_int` on them
(the byte-packed-value reader) silently produced all-zero coordinates,
caught before landing by cross-checking the parsed output against the
manual property dump. `ordering._page_regions` has its own small `_int`
coercion for this reason — see `models.PageRegion`.

**A page's multiple outline boxes share the SAME base guid as each other
and as the page itself** — the general MS-ONESTORE rule ("objects in one
object space share a base guid," used correctly elsewhere in this codebase
for ink containers and page grouping) doesn't mean "one guid per object,"
it means "one guid per PAGE, shared by every object in it, distinguished
only by the small revision counter." Verified directly on a real 6-box
page: all 6 `jcidOutlineNode`s' identities were `{page_base_guid},N` for 6
different `N`, not 6 different base guids. A first version of
`ordering._page_regions` keyed each region by `identity.split(',')[0]`
(correct for page-level lookups, where there's only ever one object needing
that key per page) and silently collapsed all 6 boxes onto one identical
key as a result — caught by checking actual per-region *content* against
the real page, not just that the right number of `(x, y)` pairs came back.
Fixed by keying on the outline node's full `(base, revision)` identity
instead. General lesson: base-guid truncation is only safe for something
that is genuinely singular per page (the page's own metadata/title/manifest)
— anything that can occur more than once per page needs the full identity.
