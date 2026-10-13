"""Monkeypatches the third-party `pyOneNote` library in place.

`pyOneNote` does the low-level MS-ONESTORE binary parsing (FileNode/
FileNodeList/PropertySet/CompactID/...), but has real bugs and real gaps this
package depends on being fixed — see each patch's own comment for exactly
what and why. This module has NO public API of its own beyond the names
listed below; importing it is a side effect (it patches `pyOneNote`'s classes
process-wide, once, the first time `onenote_tool` is imported).

Exported names other modules in this package rely on:
- `PYONENOTE_AVAILABLE` (bool), `PYONENOTE_IMPORT_ERROR` (str | None) — whether
  `pyOneNote` imported and patched successfully.
- `PyOneNoteUnavailable` (exception), `require_pyonenote()` — the public way
  to fail loudly and clearly when `pyOneNote` isn't installed.
- `OneDocment`, `check_valid`, `_pyonenote_filenode` — re-exported (as `None`
  if unavailable) so other modules can `from . import _patches` without a
  separate availability check of their own.
- `_active_decrypt` — single-slot list coordinating a
  password-verified re-parse (see `parser.parse_one_file`). Defined
  unconditionally (not inside the try/except below) so importing them never
  fails just because `pyOneNote` itself is missing.

Concurrency note: `_active_decrypt`/`_active_marker_probe` are
thread-local single-slot holders, so parses in different threads don't see
each other's state. A parse is fully synchronous, so asyncio tasks sharing
a thread can't interleave inside one either.
"""
from __future__ import annotations

import io
import struct
import threading
import uuid

from . import decrypt as onenote_decrypt

class _ThreadSlot(threading.local):
    """A per-thread single value, indexed like a one-element list
    (`slot[0]`) so call sites read the same as a plain list would."""

    def __init__(self, default):
        self._value = default

    def __getitem__(self, index):
        if index != 0:
            raise IndexError(index)
        return self._value

    def __setitem__(self, index, value):
        if index != 0:
            raise IndexError(index)
        self._value = value


# Set unconditionally so these are always importable regardless of whether
# pyOneNote itself is available — see module docstring.
_active_decrypt = _ThreadSlot(None)
# When True, `_filenode_init` skips PropertySet construction entirely (see
# its own comment) — used for `parser.parse_one_file`'s first,
# marker-detection-only pass over a section, before it's known whether the
# file is password-protected.
_active_marker_probe = _ThreadSlot(False)


class PyOneNoteUnavailable(RuntimeError):
    """Raised when a conversion function is called but `pyOneNote` isn't
    installed or failed to import. Subclasses `RuntimeError` so existing
    `except RuntimeError` handlers keep working."""


def require_pyonenote() -> None:
    if not PYONENOTE_AVAILABLE:
        raise PyOneNoteUnavailable(
            'The OneNote parser (pyOneNote) is not available in this '
            f'environment. Install it with `pip install pyOneNote` and retry. '
            f'(import error: {PYONENOTE_IMPORT_ERROR})'
        )


try:  # pyOneNote is a required dependency at parse time, but importing
      # onenote_tool itself must never fail just because it's missing or
      # broken — every call site that actually needs it goes through
      # require_pyonenote() instead.
    from pyOneNote.Main import check_valid
    from pyOneNote.OneDocument import OneDocment
    import pyOneNote.FileNode as _pyonenote_filenode

    # pyOneNote bug fix: ObjectSpaceObjectStreamOfIDs.read() returns the current
    # id but never advances `head`, so EVERY object reference in a property set
    # resolves to the first id. That collapses all structure — most visibly, a
    # table's rows/cells all point at one object. Consume the stream in order
    # (correct MS-ONESTORE behaviour) so references resolve distinctly and the
    # real object tree (table -> rows -> cells -> content) can be walked.
    def _object_stream_read(self):
        res = None
        if self.head < len(self.body):
            res = self.body[self.head]
            self.head += 1
        return res

    _pyonenote_filenode.ObjectSpaceObjectStreamOfIDs.read = _object_stream_read

    # pyOneNote gap: FileNode type 0x07C (ObjectDataEncryptionKeyV2FNDX — marks
    # a password-protected section's object space as encrypted) has no data-shape
    # class at all; FileNode.__init__'s else-branch silently reads NOTHING for
    # it, leaving `self.data` unset and the file cursor sitting right after the
    # 4-byte node header instead of past the node's own `ref` field (a
    # FileNodeChunkReference — see [MS-ONESTORE] 2.4.24). Every node read after
    # that is misaligned, and parsing derails immediately
    # ("struct.error: unpack requires a buffer of 36 bytes" a few nodes later).
    # Reading `ref` ourselves — exactly like FileDataStoreObjectReferenceFND
    # does — resyncs the cursor so the REST of the file's structure (revision
    # manifests, object declarations — all cleartext) parses normally instead
    # of the whole section aborting.
    #
    # The out-of-line blob `ref` points at is, despite [MS-ONESTORE] saying its
    # contents "MUST be ignored", a completely ordinary ECMA-376/MS-OFFCRYPTO
    # Agile Encryption `<encryption>` XML descriptor — the SAME format used for
    # password-protected .docx/.xlsx/.pptx (see decrypt.py, which does the
    # actual key derivation + decryption; confirmed correct end-to-end against
    # a real protected section with a known password). Capture the raw blob
    # here so parse_one_file can hand it to decrypt.py when a password is
    # supplied.
    def _encryption_key_fnd_fix(self, file, document):
        if self.file_node_header.file_node_type == 'ObjectDataEncryptionKeyV2FNDX' and getattr(self, 'data', None) is None:
            ref = _pyonenote_filenode.FileNodeChunkReference(
                file, self.file_node_header.stpFormat, self.file_node_header.cbFormat,
            )
            self.data = ref
            document._saw_encryption_marker = True
            if getattr(document, '_encryption_blob', None) is None:
                cur = file.tell()
                try:
                    file.seek(ref.stp)
                    document._encryption_blob = file.read(ref.cb)
                except Exception:
                    pass
                finally:
                    file.seek(cur)

    # pyOneNote gap: a stream/array length field that lands inside an encrypted
    # property-set body (see above) decodes as near-random 32-bit garbage. Two
    # call sites turn that directly into a Python loop count with no upper
    # bound: ObjectSpaceObjectStreamOfIDs reading `Count` CompactIDs, and
    # PropertySet.get_compact_ids reading `count` more from an OID/OSID/
    # ContextID stream (a garbage count here loops calling .read() past the
    # stream's real end, which just returns None forever — no natural
    # termination at all). Either can hang the import for a very long time on
    # a password-protected file. Cap both at a count no legitimate object
    # would ever have; over the cap is unambiguously a misaligned/encrypted
    # read, not real data, and is safe to treat as a normal parse failure
    # (falls into the PropertySet recovery below).
    _MAX_PLAUSIBLE_ID_COUNT = 5000

    def _capped_stream_of_ids_init(self, file, document):
        self.header = _pyonenote_filenode.ObjectSpaceObjectStreamHeader(file)
        if self.header.Count > _MAX_PLAUSIBLE_ID_COUNT:
            raise ValueError(f'implausible stream count {self.header.Count} (misaligned/encrypted read)')
        self.body = []
        self.head = 0
        for _ in range(self.header.Count):
            self.body.append(_pyonenote_filenode.CompactID(file, document))

    _pyonenote_filenode.ObjectSpaceObjectStreamOfIDs.__init__ = _capped_stream_of_ids_init

    def _capped_get_compact_ids(stream_of_ids, count):
        if count > _MAX_PLAUSIBLE_ID_COUNT:
            raise ValueError(f'implausible compact-id count {count} (misaligned/encrypted read)')
        return [stream_of_ids.read() for _ in range(count)]

    _pyonenote_filenode.PropertySet.get_compact_ids = staticmethod(_capped_get_compact_ids)

    # Real perf bug found and fixed: the marker-probe pass (see
    # `_active_marker_probe` below) reads every PropertySet on a protected
    # section's raw ciphertext just to learn `_saw_encryption_marker` (which
    # doesn't need any of this — it's set by `_encryption_key_fnd_fix`, off
    # the FileNode's own type). Type-0x7 properties (PrtFourBytesOfLength-
    # FollowedByData — variable-length blobs) decode their length prefix
    # from ciphertext too, so `cb` comes out as near-random 32-bit garbage
    # on real files; the ORIGINAL code unconditionally does
    # `file.read(cb)`, materializing and copying up to that many bytes
    # (bounded only by EOF) — measured as the actual bottleneck of the probe
    # pass on a real protected section (94 calls, ~33s of a ~34s pass,
    # profiled with cProfile). Nothing in the probe pass ever reads
    # `.Data` afterward, so during probe mode skip straight to a `seek`
    # instead of a `read` — same cursor effect (needed so sibling
    # properties in the same PropertySet keep resolving at the right
    # offset), none of the copy cost. Scoped strictly to probe mode: normal
    # parses (including pass 2's real decrypted read) are untouched.
    _orig_prt_four_bytes_init = _pyonenote_filenode.PrtFourBytesOfLengthFollowedByData.__init__

    def _capped_prt_four_bytes_init(self, file, propertySet):
        if _active_marker_probe[0]:
            self.cb, = struct.unpack('<I', file.read(4))
            self.Data = b''
            file.seek(self.cb, 1)
            return
        _orig_prt_four_bytes_init(self, file, propertySet)

    _pyonenote_filenode.PrtFourBytesOfLengthFollowedByData.__init__ = _capped_prt_four_bytes_init

    # pyOneNote bug fix: a type-0x2 (Bool) PropertyID stores its VALUE inline,
    # in bit 31 of the property ID itself ([MS-ONESTORE] 2.6.14 —
    # PropertyID.boolValue), not as separate data. `_property_id_name_mapping`
    # is keyed on the FULL 32-bit ID including that bit, but only ever has an
    # entry for bit31=0 (False) — so a True instance of ANY boolean property
    # (Bold, Italic, Underline, Strikethrough, Superscript, Subscript,
    # TableBordersVisible, EnableHistory, OutlineElementRTL, ...) hashes to a
    # value that was never registered, decodes as the name "Unknown", and
    # `get_properties()` silently drops it (see its own `if propertyName !=
    # 'Unknown'` filter). Since OneNote only ever WRITES a boolean property
    # when it overrides the (always-False) default, this meant essentially
    # every real occurrence of every boolean formatting property was being
    # thrown away. Confirmed on a real file: before this fix, `Bold` appeared
    # 25 times in decoded properties and every single one read `False`
    # (0 `True`); with it, 5 of those 25 correctly read `True`, and
    # `Italic`/`Underline`/`Strikethrough`/`TableBordersVisible` — previously
    # invisible in `val` in their True form — appeared for the first time.
    # Masking bit 31 off before the name lookup (falling back to the
    # unmasked value, so any genuinely-unknown property still resolves to
    # "Unknown" as before) fixes every boolean property in one place; bit 31
    # is spec-reserved/0 for every non-Bool type, so this is a no-op for
    # every other property.
    def _bool_aware_get_property_name(self):
        mapping = self._property_id_name_mapping
        if self.value in mapping:
            return mapping[self.value]
        return mapping.get(self.value & 0x7FFFFFFF, 'Unknown')

    _pyonenote_filenode.PropertyID.get_property_name = _bool_aware_get_property_name

    # pyOneNote gap: only ObjectDeclaration2RefCountFND reads its property set,
    # and get_properties() lists only that node type. OneNote stores shared
    # character/paragraph style containers (bold/italic/color/highlight/heading,
    # GUID space df993cda-…) as ReadOnlyObjectDeclaration2* / *Large* nodes, whose
    # property set pyOneNote never reads — so every TextRunFormatting/ParagraphStyle
    # reference resolved to nothing. Read those property sets too and surface them.
    _orig_filenode_init = _pyonenote_filenode.FileNode.__init__

    def _filenode_init(self, file, document):
        # This node's OWN position — structural/plaintext regardless of
        # whether the section is protected (only PropertySet BODIES are
        # ciphertext). Used as a last-resort correlation mechanism for
        # protected-section content recovery (see
        # recovery._structural_page_groups): verified against a real
        # multi-page, multi-image protected section that each page's
        # declarations (title, then its body text runs, then its images/
        # files) are laid out CONTIGUOUSLY in file-position order, ending
        # exactly where the next title's declaration begins — a signal that
        # survives even when OIDs-stream resolution (every OTHER correlation
        # this package tries) is completely unavailable.
        self._structural_start = file.tell()

        # pyOneNote bug, found and fixed on a real ~20MB section from a
        # real, large, multi-author shared notebook: file_node_id 0 and 255
        # are BOTH end-of-fragment terminator markers — pyOneNote's OWN
        # `FileNodeListFragment.__init__` loop already checks for exactly
        # this (`if ... file_node_id == 255 or ... == 0: break`,
        # FileNode.py) — but that check only runs AFTER unconditionally
        # constructing a full FileNode for the terminator first, and that
        # construction can itself crash before the loop ever gets a chance
        # to recognize it: id 0 isn't in pyOneNote's own `_FileNodeIDs`
        # table at all, so it falls into the generic `else: pass`
        # (unhandled type) branch, leaving `self.data` unset — then the
        # SAME `__init__` unconditionally does `self.data.ref` for any node
        # whose header bits happen to decode `baseType == 2` (confirmed:
        # true for a real id-0 terminator's header value), crashing with
        # `AttributeError` before the caller's loop-terminator check is
        # ever reached. Confirmed on the real file: this happened 93%
        # through a 19MB section — losing the parse this deep discards
        # nearly all of that section's real content for what both
        # pyOneNote's own loop AND [MS-ONESTORE] agree is just a normal,
        # expected end-of-fragment marker, not a genuinely unsupported
        # type. Peek the header first (4 bytes, exactly what
        # `FileNodeHeader.__init__` itself reads — nothing to rewind, the
        # enclosing fragment loop seeks to an absolute offset right after
        # breaking regardless of where this leaves the cursor) and treat a
        # terminator ID as one immediately — no data, no children, matching
        # the "no data part" shape pyOneNote's own dispatch already uses
        # for other structural end-markers — instead of running the
        # crash-prone vendor dispatch for it at all.
        header = _pyonenote_filenode.FileNodeHeader(file)
        if header.file_node_id in (0, 255):
            self.document = document
            self.file_node_header = header
            self.children = []
            self.data = None
            return
        if header.file_node_id not in _pyonenote_filenode.FileNodeHeader._FileNodeIDs and header.baseType == 2:
            # A DIFFERENT unhandled-type crash, found on the same real
            # notebook, one directory over: file_node_id 2 — not a
            # terminator (only 0/255 are), genuinely absent from
            # pyOneNote's own `_FileNodeIDs` table, so it falls into the
            # same crash-prone "no data assigned, but baseType==2 still
            # tries `self.data.ref`" gap above. Tried reading a generic
            # FileNodeChunkReference here first (the same structural field
            # every OTHER baseType==2 node has, working for a different
            # unhandled type in `_encryption_key_fnd_fix` below) and
            # recursing into its children the way pyOneNote's own dispatch
            # would have — but on the real file this decoded a garbage stp
            # offset (`ValueError: cannot fit 'int' into an offset-sized
            # integer`), meaning this ID's real byte layout isn't a plain
            # chunk reference at all (or this is genuinely corrupted data,
            # not a rare-but-valid type). Recursing into that would risk
            # building on garbage rather than just losing this one node's
            # subtree — the same "no data, no children" fallback as the
            # terminator case above is the safer choice here: it caps the
            # damage to whatever's structurally beneath this ONE anomalous
            # node, instead of either crashing the whole section or
            # potentially misreading further content from a bad offset.
            self.document = document
            self.file_node_header = header
            self.children = []
            self.data = None
            return
        file.seek(self._structural_start)
        _orig_filenode_init(self, file, document)
        _encryption_key_fnd_fix(self, file, document)
        if _active_marker_probe[0]:
            # Real perf bug found and fixed: `parse_one_file`'s first pass
            # exists ONLY to learn `_saw_encryption_marker`/`_encryption_blob`
            # (both already set above, by `_encryption_key_fnd_fix`, which
            # doesn't need a PropertySet at all — it fires on the FileNode's
            # own TYPE). Building every OTHER node's PropertySet during this
            # pass anyway used to burn real time on a real file — 37s for
            # one section — for values that get thrown away unconditionally:
            # either the file turns out protected (this pass's decoded
            # values are ciphertext garbage no caller ever reads — pass 2
            # rebuilds everything with decryption on) or unprotected
            # (`parse_one_file` reruns this same construction with this flag
            # off, since THAT'S the only pass an unprotected file gets).
            # This `return` only skips the EXTRA PropertySet construction
            # this patch adds below (for ReadOnly* declaration types
            # pyOneNote itself doesn't handle) — it does NOT prevent
            # `_orig_filenode_init` above from building a PropertySet the
            # way it normally would for `ObjectDeclaration2RefCountFND`
            # nodes (pyOneNote's own, unconditional code path, un-gated).
            # That path turned out to hold the REAL cost, and specifically
            # not in the property-set attempt-chain logic itself but in one
            # call it makes: see `_capped_prt_four_bytes_init` above, which
            # is where the actual ~33s/34s was found and fixed.
            return
        if hasattr(self, 'propertySet'):
            return
        data = getattr(self, 'data', None)
        if data is None:
            return
        inner = getattr(data, 'base', data)  # ReadOnly* wraps the decl in .base
        ref = getattr(inner, 'ref', None)
        body = getattr(inner, 'body', None)
        jcid = getattr(body, 'jcid', None)
        if ref is None or jcid is None or not getattr(jcid, 'IsPropertySet', False):
            return
        cur = file.tell()
        try:
            file.seek(ref.stp)
            self.propertySet = _pyonenote_filenode.ObjectSpaceObjectPropSet(file, document)
        except Exception:
            pass  # e.g. ArrayOfPropertyValues (0x10) unimplemented -> skip this object
        finally:
            file.seek(cur)

    _pyonenote_filenode.FileNode.__init__ = _filenode_init

    # A page's object space holds several revisions, each in a revision
    # context: the default context (no context id, or a nil one) is the
    # page as it is now. Every other context is an older snapshot from the
    # page's version history — a complete copy of the page (its own
    # PageNode, TitleNode, PageMetaData, text runs ...) — or one shared
    # context of version-history bookkeeping (jcidVersionHistoryMetaData,
    # jcidVersionProxy). Verified on real notebooks: every page has exactly
    # one default-context revision, older snapshots hold the page's earlier
    # titles (e.g. "Breaks" for a page now titled "Pidurid"), and OneNote's
    # own MHT export contains only the default-context content. Snapshots
    # are often more than half of a section's declarations; mixing them in
    # is what produced stale titles, content landing on the wrong page and
    # the revision-duplicate pages the dedup passes in parser.py fight.
    _REVISION_START_TYPES = ('RevisionManifestStart4FND', 'RevisionManifestStart6FND', 'RevisionManifestStart7FND')
    _NIL_UUID = uuid.UUID(int=0)

    def _fnl_nodes(file_node_list):
        for fragment in file_node_list.fragments:
            yield from fragment.fileNodes

    _FILE_DATA_DECL_TYPES = ('ObjectDeclarationFileData3RefCountFND', 'ObjectDeclarationFileData3LargeRefCountFND')

    def _collect_propset_nodes(node, out):
        # Property-set nodes, plus embedded-file data declarations (no
        # property set of their own, but revision-scoped the same way).
        if hasattr(node, 'propertySet') or node.file_node_header.file_node_type in _FILE_DATA_DECL_TYPES:
            out.append(node)
        for child in node.children:
            for n in _fnl_nodes(child):
                _collect_propset_nodes(n, out)

    def _xguid_key(xguid):
        if xguid is None or xguid.guid == _NIL_UUID:
            return None
        return (xguid.guid, xguid.n)

    def _current_revision_nodes(revision_list):
        """Property-set nodes of one revision manifest list's current
        revision: the last default-context revision (or the one a
        RevisionRole*DeclarationFND assigns the default role to), followed
        by its ridDependent chain with already-seen identities skipped."""
        revisions = {}
        order = []
        cur = None
        role_rid = None
        for n in _fnl_nodes(revision_list):
            ntype = n.file_node_header.file_node_type
            data = getattr(n, 'data', None)
            if ntype in _REVISION_START_TYPES:
                base = getattr(data, 'base', data)
                cur = {
                    'rid': _xguid_key(base.rid),
                    'dep': _xguid_key(base.ridDependent),
                    'ctx': _xguid_key(getattr(data, 'gctxid', None)),
                    'nodes': [],
                }
                order.append(cur)
                if cur['rid'] is not None:
                    revisions[cur['rid']] = cur
            elif ntype in ('RevisionRoleDeclarationFND', 'RevisionRoleAndContextDeclarationFND'):
                base = getattr(data, 'base', data)
                if base.RevisionRole == 1 and _xguid_key(getattr(data, 'gctxid', None)) is None:
                    role_rid = _xguid_key(base.rid)
            elif cur is not None:
                _collect_propset_nodes(n, cur['nodes'])
        current = revisions.get(role_rid) if role_rid is not None else None
        if current is None:
            defaults = [r for r in order if r['ctx'] is None]
            current = defaults[-1] if defaults else None
        if current is None:
            # No default-context revision at all — unexpected; keep everything
            # rather than drop the object space.
            return [n for r in order for n in r['nodes']]
        result, seen, visited = [], set(), set()
        while current is not None and id(current) not in visited:
            visited.add(id(current))
            for n in current['nodes']:
                inner = getattr(n.data, 'base', n.data)
                decl = getattr(inner, 'body', inner)
                key = str(getattr(decl, 'oid', id(n)))
                if key not in seen:
                    seen.add(key)
                    result.append(n)
            current = revisions.get(current['dep']) if current['dep'] is not None else None
        return result

    def _current_propset_nodes(self):
        """`(node, object_space_id)` for every property-set node in the
        current revision of each object space (`object_space_id` is None
        for a node outside any object space)."""
        nodes = []
        for top in _fnl_nodes(self.root_file_node_list):
            if top.file_node_header.file_node_type != 'ObjectSpaceManifestListReferenceFND':
                found = []
                _collect_propset_nodes(top, found)
                nodes.extend((n, None) for n in found)
                continue
            space = str(top.data.gosid.guid)
            for space_list in top.children:
                for n in _fnl_nodes(space_list):
                    found = []
                    if n.file_node_header.file_node_type != 'RevisionManifestListReferenceFND':
                        _collect_propset_nodes(n, found)
                    else:
                        for revision_list in n.children:
                            found.extend(_current_revision_nodes(revision_list))
                    nodes.extend((m, space) for m in found)
        return nodes

    def _get_properties(self):
        if self._properties:
            return self._properties
        nodes = _current_propset_nodes(self)
        self._properties = []
        self._current_file_identities = {
            str(node.data.oid) for node, _space in nodes
            if node.file_node_header.file_node_type in _FILE_DATA_DECL_TYPES
        }
        for node, space in nodes:
            ps = getattr(node, 'propertySet', None)
            data = getattr(node, 'data', None)
            if ps is None or data is None:
                continue
            inner = getattr(data, 'base', data)
            decl = getattr(inner, 'body', None)
            if decl is None:
                continue
            ps.body.indent = '\t\t'
            try:
                self._properties.append({
                    'type': str(decl.jcid),
                    # pyOneNote's own jcid->name table (JCID._jcid_name_mapping)
                    # is a small hardcoded subset — anything outside it decodes
                    # as the string "Unknown", indistinguishable from every
                    # other unrecognized type. Keep the raw int too so callers
                    # can still identify SPECIFIC unnamed types (e.g. ink/
                    # drawing — see parser._INK_JCID_RAW) by their real jcid.
                    'jcid_raw': decl.jcid.jcid,
                    'identity': str(decl.oid),
                    'val': ps.body.get_properties(),
                    # The filtered dict above drops any property whose ID
                    # isn't in pyOneNote's small name table — real content for
                    # undocumented types (ink: see ink.py) as well as any name
                    # pyOneNote hasn't mapped. Keep the raw PropertySet so
                    # callers that know a property's raw ID can still read it
                    # directly.
                    'body': ps.body,
                    # The object space (page, or section-level space) this
                    # declaration lives in — a structural page membership
                    # signal, see parser's `page_of_space`.
                    'space': space,
                })
            except Exception:
                pass
        return self._properties

    OneDocment.get_properties = _get_properties

    def _current_file_identities(self):
        """Identities of embedded-file declarations in each object space's
        current revision (an older page snapshot can hold an attachment the
        page no longer has)."""
        self.get_properties()
        return self._current_file_identities

    OneDocment.current_file_identities = _current_file_identities

    # pyOneNote bug fix: `OneDocment.get_files()` keys its own dict by the
    # file-data blob's OWN storage guid, and for each declaration
    # (`ObjectDeclarationFileData3RefCountFND`) does
    # `self._files[guid]["identity"] = str(node.data.oid)` — last-wins,
    # once per blob. When the exact same underlying blob is legitimately
    # declared under MULTIPLE different object identities — verified on a
    # real 250+MB notebook: the identical photo pasted on two different
    # pages (two different base GUIDs, byte-identical content) — only the
    # LAST-processed identity survives as resolvable; every OTHER identity
    # that legitimately points at that same blob is unreachable through
    # `get_files()`'s own dict, even though the bytes are genuinely present
    # in the file. Silently dropped 135 of ~350 real pasted images on one
    # real section alone (all `PictureContainer` lookups miss, `images.py`
    # correctly reports `image_missing_data` for each — not a false
    # negative on THIS tool's part, but a real gap `get_files()` itself
    # leaves).
    #
    # Fixed by indexing both directions ourselves: every declaration's own
    # (oid -> blob guid) pair (not just the last one per blob), then
    # resolving oid -> bytes through that — a superset of what
    # `get_files()` returns, never a narrower one. `document.get_files()`
    # itself is left completely unpatched (still used by the password-
    # decryption path exactly as before, at the same file-position/window
    # its own lazy, decrypt-context-sensitive `FileDataStoreObject` read
    # needs) — this is an ADDITIONAL method, not a replacement.
    def _files_by_identity(self):
        nodes = []
        OneDocment.traverse_nodes(self.root_file_node_list, nodes, [
            'ObjectDeclarationFileData3RefCountFND', 'FileDataStoreObjectReferenceFND',
        ])
        blob_by_guidref = {}
        decl_by_oid = {}
        for node in nodes:
            d = getattr(node, 'data', None)
            if isinstance(d, _pyonenote_filenode.FileDataStoreObjectReferenceFND):
                blob_by_guidref[str(d.guidReference)] = d.fileDataStoreObject.FileData
            elif isinstance(d, _pyonenote_filenode.ObjectDeclarationFileData3RefCountFND):
                g = d.FileDataReference.StringData.replace('<ifndf>{', '').replace('}', '').lower()
                decl_by_oid[str(d.oid)] = (g, d.Extension.StringData)

        out = {}
        for oid, (g, ext) in decl_by_oid.items():
            content = blob_by_guidref.get(g)
            if content:
                out[oid] = {'content': content, 'extension': ext}
        return out

    OneDocment.files_by_identity = _files_by_identity

    # pyOneNote bug fix: `get_json()` calls `.hex()` unconditionally on
    # every `get_files()` entry's `content` — but an
    # `ObjectDeclarationFileData3RefCountFND` declaration with no matching
    # `FileDataStoreObjectReferenceFND` (the blob genuinely never showed up
    # anywhere in the file — a real, legitimate state for a heavily-pruned
    # revision history, confirmed on a real, large, multi-author shared
    # notebook: 6 of its sections hit this) leaves that entry's `content`
    # at `get_files()`'s own string default (`""`), and `"".hex()` raises
    # `AttributeError: 'str' object has no attribute 'hex'` — aborting the
    # ENTIRE section's parse (`parse_one_file` calls `get_json()`
    # unconditionally) for one incomplete, usually-irrelevant file
    # declaration. Guard the same way get_json() itself should have:
    # `.hex()` only when content is real bytes, `''` otherwise.
    def _safe_get_json(self):
        files_in_hex = {}
        for key, file_entry in (self.get_files() or {}).items():
            content = file_entry.get('content')
            files_in_hex[key] = {
                'extension': file_entry.get('extension', ''),
                'content': content.hex() if isinstance(content, (bytes, bytearray)) else '',
                'identity': file_entry.get('identity', ''),
            }
        return {
            'headers': self.header.convert_to_dictionary(),
            'properties': self.get_properties(),
            'files': files_in_hex,
        }

    OneDocment.get_json = _safe_get_json

    # pyOneNote's PropertySet reader doesn't implement the two nested types:
    # 0x10 (ArrayOfPropertyValues) raises NotImplementedError, and 0x11
    # (a single nested PropertySet) calls `PropertySet(file)` with four
    # required arguments missing. Either one aborted the object, and every
    # property after it was lost. That mattered more than it looks: a
    # paragraph with a note tag (To Do, Important, ...) carries
    # `NoteTagStates`, an ArrayOfPropertyValues, BEFORE its text, so every
    # tagged paragraph silently lost its text (verified on a real notebook:
    # a whole page of To Do items converted to nothing). This reader
    # implements every type per [MS-ONESTORE] 2.6.6-2.6.9; nested sets read
    # their own ObjectID/ObjectSpaceID/ContextID references from the same
    # streams as the outer set, in order.
    def _read_propset(self, file, OIDs, OSIDs, ContextIDs, document):
        fn = _pyonenote_filenode
        self.current = file.tell()
        self.cProperties, = struct.unpack('<H', file.read(2))
        self.indent = ''
        self.document = document
        self.current_revision = document.cur_revision
        self._formated_properties = None
        self.rgPrids = [fn.PropertyID(file) for _ in range(self.cProperties)]
        self.rgData = []
        for prid in self.rgPrids:
            ptype = prid.type
            if ptype == 0x1:
                self.rgData.append(None)
            elif ptype == 0x2:
                self.rgData.append(prid.boolValue)
            elif ptype in (0x3, 0x4, 0x5, 0x6):
                size = {0x3: 1, 0x4: 2, 0x5: 4, 0x6: 8}[ptype]
                data = file.read(size)
                if len(data) != size:
                    raise struct.error('truncated property value')
                self.rgData.append(data if ptype != 0x3 else data[:1])
            elif ptype == 0x7:
                self.rgData.append(fn.PrtFourBytesOfLengthFollowedByData(file, self))
            elif ptype in (0x8, 0x9, 0xA, 0xB, 0xC, 0xD):
                count = 1
                if ptype in (0x9, 0xB, 0xD):
                    count, = struct.unpack('<I', file.read(4))
                stream = OIDs if ptype in (0x8, 0x9) else OSIDs if ptype in (0xA, 0xB) else ContextIDs
                self.rgData.append(fn.PropertySet.get_compact_ids(stream, count))
            elif ptype == 0x10:
                count, = struct.unpack('<I', file.read(4))
                items = []
                if count:
                    element = fn.PropertyID(file)
                    if element.type != 0x11 or count > _MAX_PLAUSIBLE_ID_COUNT:
                        raise ValueError('implausible ArrayOfPropertyValues')
                    for _ in range(count):
                        child = fn.PropertySet.__new__(fn.PropertySet)
                        _read_propset(child, file, OIDs, OSIDs, ContextIDs, document)
                        items.append(child)
                self.rgData.append(items)
            elif ptype == 0x11:
                child = fn.PropertySet.__new__(fn.PropertySet)
                _read_propset(child, file, OIDs, OSIDs, ContextIDs, document)
                self.rgData.append(child)
            else:
                raise ValueError('rgPrids[i].type is not valid')

    def _propset_init(self, file, OIDs, OSIDs, ContextIDs, document):
        try:
            _read_propset(self, file, OIDs, OSIDs, ContextIDs, document)
        except (NotImplementedError, ValueError, AttributeError, TypeError, struct.error):
            # AttributeError: a stream (e.g. ContextIDs) that a property type
            # expects turns out to be None — happens when reading into an
            # encrypted property-set body with no password supplied (see the
            # encryption-key fix above) — or genuinely corrupt data.
            # Keep only the fully-read (prid, value) pairs; truncate the count so
            # get_properties() iterates just those (padding with None would crash
            # its struct.unpack on typed values).
            read = len(getattr(self, 'rgData', []) or [])
            self.rgPrids = getattr(self, 'rgPrids', [])[:read]
            self.cProperties = read

    _pyonenote_filenode.PropertySet.__init__ = _propset_init

    # Nested sets (types 0x10/0x11, above) surface in get_properties() as
    # their own decoded dicts (a list of dicts for 0x10) instead of the
    # vendor's str() of the object.
    _orig_propset_get_properties = _pyonenote_filenode.PropertySet.get_properties

    def _propset_get_properties(self):
        if self._formated_properties is not None:
            return self._formated_properties
        nested = {}
        for i, data in enumerate(self.rgData):
            if isinstance(data, _pyonenote_filenode.PropertySet):
                nested[i] = data
                self.rgData[i] = b''
            elif isinstance(data, list) and data and isinstance(data[0], _pyonenote_filenode.PropertySet):
                nested[i] = data
                self.rgData[i] = []
        try:
            props = _orig_propset_get_properties(self)
        finally:
            for i, data in nested.items():
                self.rgData[i] = data
        for i, data in nested.items():
            name = str(self.rgPrids[i])
            if name == 'Unknown':
                continue
            props[name] = [d.get_properties() for d in data] if isinstance(data, list) else data.get_properties()
        return props

    _pyonenote_filenode.PropertySet.get_properties = _propset_get_properties

    # pyOneNote's name table registers NoteTagStates as 0x04003489, but the
    # real id carries the ArrayOfPropertyValues type bits: 0x40003489.
    _pyonenote_filenode.PropertyID._property_id_name_mapping[0x40003489] = 'NoteTagStates'

    # A password-protected section encrypts each ObjectSpaceObjectPropSet's
    # PropertySet body individually (see decrypt.py's `decrypt_object_body`
    # for the exact framing, verified 100% against every object in a real
    # protected section). The OIDs/OSIDs/ContextIDs compact-id streams in
    # front of it stay plaintext; the body itself is replaced by a uint32
    # ciphertext length plus that many bytes of ciphertext. Read the ID
    # streams normally, decrypt the body into its own buffer, and parse the
    # PropertySet from that.
    _orig_ospropset_init = _pyonenote_filenode.ObjectSpaceObjectPropSet.__init__

    def _ospropset_init(self, file, document):
        decrypt_ctx = _active_decrypt[0]
        if decrypt_ctx is None:
            _orig_ospropset_init(self, file, document)
            return
        info, file_key = decrypt_ctx
        start = file.tell()
        try:
            oids = _pyonenote_filenode.ObjectSpaceObjectStreamOfIDs(file, document)
            osids = None
            if not oids.header.OsidStreamNotPresent:
                osids = _pyonenote_filenode.ObjectSpaceObjectStreamOfIDs(file, document)
            ctx = None
            if oids.header.ExtendedStreamsPresent:
                ctx = _pyonenote_filenode.ObjectSpaceObjectStreamOfIDs(file, document)
            cb, = struct.unpack('<I', file.read(4))
            body = onenote_decrypt.decrypt_object_body(file.read(cb), info, file_key)
        except (ValueError, struct.error):
            body = None
        if body is None:
            # Not an encrypted body after all — read it as plaintext.
            file.seek(start)
            _orig_ospropset_init(self, file, document)
            return
        self.OIDs = oids
        self.OSIDs = osids
        self.ContextIDs = ctx
        self.body = _pyonenote_filenode.PropertySet(io.BytesIO(body), oids, osids, ctx, document)

    _pyonenote_filenode.ObjectSpaceObjectPropSet.__init__ = _ospropset_init

    # An embedded file/image's actual bytes (OneDocment.get_files()) are read
    # by FileDataStoreObject at an absolute file offset (FileNodeChunkReference
    # -> a plaintext, structural reference — same story as everywhere else in
    # this file). This one's own 36-byte header (guidHeader/cbLength/unused/
    # reserved) stays real plaintext even in a protected section — verified
    # empirically (guidHeader decodes to [MS-ONESTORE]'s own documented fixed
    # constant {BDE316E7-2665-4511-A4C4-8D4D0B7A9EAC}; cbLength gives a
    # plausible file size). But unlike PropertySet content, the FileData
    # PAYLOAD after that header is NOT part of the whole-file segmented
    # stream everything else uses — it's independently encrypted as its OWN
    # OOXML-style "EncryptedPackage": segment 0 starts fresh at the payload's
    # own offset (not the absolute file offset), and the first 8 bytes of the
    # decrypted payload are a little-endian uint64 giving the true content
    # size (cbLength itself is that size padded up to a 16-byte AES block,
    # plus the 8-byte prefix — confirmed against this exact file: cbLength
    # 3450128 == ceil((3450108 + 8) / 16) * 16). Verified end-to-end: this
    # recovers a real, valid JPEG (magic bytes present) from a real protected
    # section's embedded image.
    _orig_filedatastore_init = _pyonenote_filenode.FileDataStoreObject.__init__

    def _filedatastore_init(self, file, fileNodeChunkReference):
        decrypt_ctx = _active_decrypt[0]
        if decrypt_ctx is None:
            _orig_filedatastore_init(self, file, fileNodeChunkReference)
            return
        info, file_key = decrypt_ctx
        start = file.tell()
        self.guidHeader, self.cbLength, self.unused, self.reserved = struct.unpack('<16sQ4s8s', file.read(36))
        try:
            ciphertext = file.read(self.cbLength)
            decrypted = onenote_decrypt.decrypt_file_payload(ciphertext, info, file_key)
            true_size, = struct.unpack('<Q', decrypted[:8])
            self.FileData = decrypted[8:8 + true_size]
        except Exception:
            file.seek(start + 36)
            self.FileData, = struct.unpack(f'{self.cbLength}s', file.read(self.cbLength))
        file.seek(fileNodeChunkReference.stp + fileNodeChunkReference.cb - 16)
        self.guidFooter, = struct.unpack('16s', file.read(16))
        self.guidHeader = uuid.UUID(bytes_le=self.guidHeader)
        self.guidFooter = uuid.UUID(bytes_le=self.guidFooter)

    _pyonenote_filenode.FileDataStoreObject.__init__ = _filedatastore_init

    PYONENOTE_AVAILABLE = True
    PYONENOTE_IMPORT_ERROR = None
except Exception as _exc:  # pragma: no cover - import guard
    PYONENOTE_AVAILABLE = False
    PYONENOTE_IMPORT_ERROR = f'{type(_exc).__name__}: {_exc}'
    OneDocment = None
    check_valid = None
    _pyonenote_filenode = None
