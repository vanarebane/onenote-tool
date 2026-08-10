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
it with Windows' own `expand.exe` (see
[known-limitations.md](known-limitations.md) for why that's not portable
yet) — a section's group path is just its relative directory inside the
extracted CAB.

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
