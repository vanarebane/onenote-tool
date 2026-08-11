# Recovery techniques — what was tried, what worked, what didn't

A chronological log of the approaches tried for recovering readable content
(and correctly attributing it to the right page) from a password-protected
OneNote section, once the password itself is known to be correct. This is
the reasoning trail behind `recovery.py` — read it before "improving" the
current approach; several tempting-looking alternatives below were already
tried and are dead ends, for reasons that aren't obvious until you've hit
them on a real file.

All of this assumes the encryption itself is already solved — see
[ms-offcrypto-encryption.md](ms-offcrypto-encryption.md).

## The core problem

Once a password verifies and property *values* decrypt correctly, the
**normal** per-page grouping mechanism (group runs by the object-space GUID
referenced in their `TextRunFormatting`, `parser.parse_one_file`'s main
loop) still doesn't work for protected content — because that GUID is
itself resolved through the very OID-stream mechanism that (for a long
time) looked completely unavailable for protected objects (see
[ms-offcrypto-encryption.md](ms-offcrypto-encryption.md)'s OID-streams
section for how that was eventually solved for *some* objects, and why it's
still not reliable enough to lean on as the *primary* mechanism — see
"OID streams get partially solved" below). Every run gets decrypted
correctly but never resolves to a page. Something else has to correlate
content to the correct page.

## Rejected: byte-offset proximity of CONTENT

First instinct: assign a run to whichever title's *decoded content* was
seen most recently in traversal order. **Empirically wrong** — on a real
protected section, pyOneNote visits *every* title before *any* body text,
so this heuristic puts all recovered text under whichever title happened
to be seen last. Content-order proximity and structural-position proximity
are not the same thing (see the next entry, and the successful mechanism
below) — this is the mistake of conflating them.

## Rejected: object-space/gosid grouping

The mechanism used for normal-section page *ordering*
(`ordering._object_space_order`, gosid-based) was tried for page
*attribution* too. Rejected: verified against a real file that gosid
granularity is **section-level, not page-level** — every file/run in a
whole section mapped to the same single gosid. A dead end for anything
finer-grained than "which section is this in."

## Rejected: file/title identity-OID equality

Observed once, on a small single-image test section: an embedded file's
own identity OID sometimes exactly equals the identity OID of its real
page's title/stamp properties. Looked like a real mechanism. **Turned out
to be coincidence, not a mechanism** — verified on a real multi-image
section (15 files, 12 pages): only 1 of 15 file OIDs actually matched a
title OID. Not reliable enough to use even as a tiebreaker.

## Working: structural (file-position) clustering

**This is the mechanism `recovery._structural_page_groups` actually uses.**
Found by testing a much simpler idea directly: does a page's *declarations*
(as opposed to its decoded content) sit near each other in raw file
position? Yes — verified against a real multi-page, multi-image protected
section: a page's `jcidPageMetaData` (title), its `jcidRichTextOENode`s
(body runs), and its file/ink declarations are laid out **contiguously**,
in file-position order, ending exactly where the next title's declaration
begins.

This needs **zero** OID-stream resolution — `ref.stp` (a declaration's own
out-of-line position) and a `FileNode`'s own start offset
(`_patches._filenode_init`'s `self._structural_start`, added specifically
for this) are both structural framing, real plaintext regardless of
encryption (see [onenote-file-format.md](onenote-file-format.md)).

### The revision-duplication complication, and its partial fix

Real files have stale/superseded generations of a page's declarations
scattered elsewhere in the file (see
[onenote-file-format.md](onenote-file-format.md)'s revision-duplication
section) — naively, "everything between two titles belongs to the first
title" swallows stale spillover as if it were real content.

**Fixed for the "widely-separated cluster" case**: found, on a real bug
report, that a title's own text ended cleanly ~2KB in, then the *very
next* declaration was **24.5 million bytes later** and belonged to a
totally different (stale, title-decode-failed) page — silently absorbed as
the first title's own content purely because no other title happened to
intervene first. Fix: track the position gap between consecutive
text/ink declarations; a gap far past any legitimate same-page span (a
generous 1,000,000-byte threshold, verified real same-page spans stay
under ~6KB) starts a fresh, synthetic "untitled" group instead of
continuing to attach to the current title.

**Explicitly NOT applied to file declarations** — a real regression, caught
before landing: the same gap-based split, applied to `ObjectDeclaration
FileData3RefCountFND` declarations too, broke a page that was working
correctly (an "Image" page with 6 images lost all of them). Cause: unlike
text, a page's own file declarations can legitimately sit millions of
bytes from its title in some files — a 6-image page's *text* stayed close
to its title, but its own 6 file declarations lived in the same distant
region the (unrelated) text spillover also occupied. File declarations
always attach to whatever group is structurally "current," unconditionally
— accepting the (smaller) residual misattribution risk this reintroduces,
because the alternative actively breaks working pages.

**Not fully solved: the "interleaved near-duplicate" case.** A stale
generation can also land *inside* a different, unrelated *live* page's own
window (not separated by a large gap at all) — confirmed on a real file:
"Background"'s structural window also contained a stale copy of a
completely different page ("List")'s own body lines. See "Global content
claiming" below for the mitigation in place, and its own known limits.

## Global content claiming (dedup across groups)

A related but distinct problem from the above: the exact same run/file
content can appear, fully decoded, in **two different groups** — not just
"this group has a stale leftover," but "this exact text is claimed by two
separate page groups." Verified on a real file: a "File" page (with its
real image) and a separate near-empty "Untitled page" both carried the
*exact same* 4 body lines.

First attempt — claim shared content for whichever group is *richer*
(more total items) — **caused a real regression**: verified against the
"Background"/"List" case above, richness-based claiming gave List's own 3
lines to Background (which had one more line of its own, so "won" on
total count), actively **removing** List's only content from List's own,
correctly-titled page. Worse than the pre-existing spillover it was meant
to fix.

**Current mitigation**: a group is only ever allowed to *lose* content this
way if it has **no real decoded title of its own** (a strong signal it's a
stale/ghost revision with no independent identity). A group whose own
title *did* decode keeps everything it structurally collected,
unconditionally, even if that duplicates another group's content. This
correctly resolves the ghost-duplicate case (an untitled duplicate loses
to a real page and gets dropped) while protecting real, titled pages from
ever losing their own content to a "richer" neighbor. It does **not**
fully solve the interleaved-near-duplicate case above when *both* groups
have real titles (e.g. Background still shows List's spillover lines, on
top of its own) — deliberately left as a smaller, accepted residual rather
than risk removing real content again.

A further, smaller cleanup on top of this: once a title *does* decode
correctly, any OTHER group's stray text that happens to equal that exact
title string is filtered out globally (not just from its own group) — a
duplicate title-echo landing in the wrong structural window is otherwise
indistinguishable from real one-word body content, and was showing up as
its own near-empty ghost page next to the real one.

## Title decode — a related, still-open gap

Even with content correctly clustered, a given page's `CachedTitleString`
property frequently just doesn't decode — the same `_ospropset_init`
3-attempt chain (see
[ms-offcrypto-encryption.md](ms-offcrypto-encryption.md)) that recovers
most body text fails for a meaningful fraction of titles on a real file
(observed as low as ~50% success on a harder test file).

**One real fix landed**: `by_id`'s identity→property map used strict
first-occurrence-wins deduplication. For one real bug, a title object had
two FileNode declarations sharing one identity — the *first* one's body
genuinely never decoded (empty), the *second* one's did (real
`CachedTitleString`, confirmed structurally correct) — but first-wins
permanently discarded the working one. Fixed: an empty first-seen entry now
gets upgraded by a later, non-empty duplicate of the same identity. This
recovers some titles that were previously permanently lost, but only when
a *better* duplicate happens to exist somewhere in the file — it does
nothing for a title with no recoverable copy anywhere.

**Attempted and explicitly rejected: widening the generic scan window.**
The obvious next idea — just search further than 128 bytes — was tested
directly and found a real false positive: a *neighboring, unrelated*
object's body (properties like `LastModifiedTime`/`OffsetFromParentHoriz`,
nothing resembling a title) scored just as well by the same "plausible
property count" metric as the real title, and won by being found first in
a wider search. Blindly widening the window is unsafe in general.

**The safe exception found for titles specifically**: a title-only rescue
scan, gated on the caller already knowing (via `_patches._filenode_init`
telling `_ospropset_init` what jcid it's building) that this object is
specifically a `jcidPageMetaData` declaration, and requiring
`CachedTitleString`'s own raw property ID to be present among the
candidate's matched properties — not just a plausible-looking score. This
is what makes widening safe here where it wasn't safe generically: the
false positive above had no `CachedTitleString` at all and would be
rejected by this guard. In practice, on the files tested so far, this
rescue scan hasn't recovered any *additional* titles beyond what the
`by_id`-upgrade fix above already accounts for — kept in as a low-risk,
zero-cost-when-unused safety net, not because it's been proven to help yet.

## Two real regressions from the OID-streams breakthrough

Once OID/OSID/ContextID streams were found to be plaintext (see
[ms-offcrypto-encryption.md](ms-offcrypto-encryption.md)), wiring them in
naively broke two things that had been working — both caught by testing
against real files before landing, both fixed:

1. **Per-object-type unsafety.** The real-stream read was only validated
   against the simple 2-OID text-run shape it was discovered with. Trusting
   it unconditionally for every object type — including images and titles,
   with different reference shapes — made two previously-correct pages
   lose *all* their content (a real-but-wrong-for-that-object stream can
   make an OID-typed property misbehave partway through and truncate the
   object early). **Fix**: build the candidate body both ways (real
   streams, and the old empty-stream stub) and keep whichever actually
   decoded more properties (`cProperties` — a `PropertySet`'s own
   graceful-truncation contract already makes this a reliable "how far did
   this get" signal). Real streams only ever help now, never regress.

2. **Main-pipeline partial success skipping recovery entirely.** With OID
   resolution partially working, the *normal* per-page pipeline's own page-
   grouping (`_run_guid`, `PictureContainer` matching) started
   *sometimes* succeeding for protected content too — which made the
   overall page list non-empty and skipped structural recovery altogether,
   even though the main pipeline's per-object resolution is still
   unreliable enough that its "partial success" was demonstrably **worse**
   (images vanishing, several pages losing all content) than running
   structural recovery would have been. **Fix**: for a verified-password
   section, always run structural recovery and prefer its result over
   whatever the main pipeline found, regardless of whether the main
   pipeline's own page list ended up non-empty. Structural recovery
   doesn't depend on any reference resolving at all, so it's never worse.

## Performance: two real hangs, both fixed with the same shape of cap

Two completely unrelated call sites turned a garbage 32-bit length field
(landing inside what's supposed to be — but, when misaligned, isn't — a
real count) directly into an uncapped Python loop count:
`ObjectSpaceObjectStreamOfIDs` reading `Count` CompactIDs, and
`PropertySet.get_compact_ids` reading `count` more from an ID stream (a
garbage count here loops calling `.read()` past the stream's real end,
which just returns `None` forever — no natural termination). Either could
hang for a very long time on a real protected file. Both capped at
`_MAX_PLAUSIBLE_ID_COUNT = 5000` — well past anything a legitimate object
would ever have, and unambiguously a misaligned/encrypted read past that
point, safe to treat as a normal parse failure rather than a hang.

A third, separately-found hang: `cProperties` (a raw `u16`, up to 65535)
landing on a misaligned shadow position could claim tens of thousands of
properties, and the underlying `PropertySet.__init__` loop had no cap —
measured costing tens of real seconds on a ~30MB section, reading and
discarding one `PropertyID` at a time before the exception handler threw
the whole result away anyway. Fixed by peeking the 2-byte count *before*
paying for the loop and short-circuiting to the same degenerate empty
result the exception handler already produced
(`_MAX_PLAUSIBLE_CPROPERTIES = 300`) — same end state, correctly fast
instead of correctly slow.

## Lists: wired in and confirmed partially working; tables: wired in, confirmed dead

Both content types were missing from structural recovery not because
they'd been tried and failed, but because nobody had tried — `parser.
parse_one_file`'s normal per-page pipeline already builds list membership
(`jcidOutlineElementNode` → `ListNodes` → `NumberListFormat`) and table
grids (`rendering._resolve_table_grid`) from `by_id`, the SAME decrypted
property map `_pages_from_structural_groups` already has. Wiring both in
needed no new decode logic, no touching of page attribution/grouping (the
part of this file that has actually regressed real files before — see the
"sibling consensus" incident in [known-limitations.md](known-limitations.md))
— just reading the same properties an already-working code path reads.

**Lists: real, verified improvement.** On the same test file used
throughout this doc, a page that previously rendered as entirely flat
paragraphs (no `<ul>`/`<ol>` at all) now recovers at least one real list
item. Partial, not complete — bounded by the standard per-object 3-attempt
decode ceiling (`_ospropset_init`), same as titles/formatting/text runs
always have been: on that file, `jcidOutlineElementNode` decoded non-empty
~47% of the time (254/539) and `jcidNumberListNode` ~25% (3/12). An item
whose own list-membership object didn't decode simply renders as a plain
paragraph — never dropped, never crashes.

**Tables: wired in, but confirmed non-functional on the only file
available to test against.** Every single `jcidTableNode` property set
found — 6 of 6, across both protected sections in the test notebook —
decoded to a completely empty `val` (`{}`), not partially like every other
object type checked. This is a MUCH harder failure than the usual partial
ceiling and looks systematic rather than statistical, though the root
cause isn't confirmed: a table's own properties (row/cell child
references) may depend more heavily on the OID-stream reference
resolution documented above as "still not reliable enough to lean on" than
a plain scalar-heavy text run's properties do — plausible, not verified.
**Left in the code anyway** (degrades to silently contributing nothing,
same as an unset list item) rather than reverted: it's inert-safe on data
that can't decode, and would start working the moment this specific
ceiling is ever improved, without needing to be rediscovered. Don't expect
real tables from a protected section today, though — if you're chasing
this further, get a *different* real protected file with a table first and
check whether `jcidTableNode.val` is *always* empty there too before
assuming this generalizes.
