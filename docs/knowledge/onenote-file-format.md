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
  unencrypted plaintext, even inside a password-protected section.** Only a
  PropertySet's own property *values* are ever real ciphertext. This
  distinction is the entire basis for this tool's protected-content
  recovery — see
  [ms-offcrypto-encryption.md](ms-offcrypto-encryption.md) and
  [recovery-techniques-log.md](recovery-techniques-log.md).
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

### Embedded files and images — two different node shapes

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

### Revision-store duplication

A `.one` file is not a clean single snapshot — OneNote's on-disk format is
itself a **revision store**: editing a page over time leaves multiple
generations of its objects in the file, not just the current one. Verified
directly: the SAME logical text run or title can appear at 20+ different
`oid` revision-counter values, scattered across the file. Two shapes this
takes, both confirmed against real files:

- **Widely-separated clusters.** A stale/superseded generation's
  declarations can sit in an entirely different region of the file,
  megabytes away from the live generation's — a gap far larger than any
  legitimate same-page proximity. (This tool uses exactly that gap size as
  a signal — see
  [recovery-techniques-log.md](recovery-techniques-log.md).)
- **Interleaved near-duplicates.** A stale generation can *also* land
  right next to (structurally "inside the window of") a completely
  different, unrelated live page — not just its own page's stale copy.
  This is the harder case, and not fully solved — see the same log for
  what was tried.

This isn't only a password-protected-recovery problem — the manifest tree
(`ordering._page_content_order`'s tree walk, used for content order/title
detection) has its own revision-store failure mode: a stale, superseded
`jcidPageManifestNode` generation can structurally claim (via its own
child-reference properties) content that legitimately belongs to a
different, current page's manifest.

**Tried and reverted (0.1.1)**: an earlier version had `parser.parse_one_file`
trust that tree walk to override a run's own `_run_guid` (its
`TextRunFormatting`-derived object-space guid) whenever the two disagreed,
reasoning that the tree was the more authoritative signal for "which page is
this run really on." A/B testing against the verified-working pre-extraction
code on a real notebook found the opposite: the tree walk's own failure mode
above made it claim a run that belonged to a genuinely separate page,
silently **dropping that page entirely** from the output (not just
misattributing a run — the whole page, title included, vanished). See
[known-limitations.md](known-limitations.md) for the full account. Current
behavior (matches the verified baseline): group a run purely by its own
`_run_guid`, no tree-based override. `ordering._page_content_order`'s walk
also deliberately uses a narrower child-key set (`_CONTENT_CHILD_KEYS`) than
`_object_space_order`'s (`_ORDER_CHILD_KEYS`) — widening it to match was
part of what let the walk cross into the wrong page's subtree.

- **Still open: which title snapshot is current.** Page titles have the same
  duplication problem `page_title_by_guid` doesn't yet resolve: a renamed
  page can leave both its old and new `CachedTitleString` snapshots in the
  file under the exact same `(guid, revision)` identity tuple pyOneNote
  exposes — no ordering signal currently available (revision counter,
  timestamp) reliably distinguishes which is current. First-occurrence-wins
  (today's behavior) is right on some real pages and stale on others in the
  *same file* — there's no consistent direction to flip the tiebreak in
  without a real signal to key it on. Left as a known gap rather than
  guessed at; see
  [known-limitations.md](known-limitations.md).

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
