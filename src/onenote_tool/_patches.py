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
- `_active_shadow`, `_active_decrypt` — single-slot lists coordinating a
  password-verified re-parse (see `parser.parse_one_file`). Defined
  unconditionally (not inside the try/except below) so importing them never
  fails just because `pyOneNote` itself is missing.

Concurrency note: `_active_shadow`/`_active_decrypt`/`_active_expected_jcid`
are plain single-slot lists, NOT thread-local or async-safe. This whole
pipeline is meant to run synchronously, one `.one` file at a time, never
concurrently within a process. If you need concurrent conversion, run
multiple processes, not multiple threads/tasks sharing one interpreter.
"""
from __future__ import annotations

import io
import struct
import uuid

from . import decrypt as onenote_decrypt

# Set unconditionally so these are always importable regardless of whether
# pyOneNote itself is available — see module docstring.
_active_shadow = [None]
_active_decrypt = [None]
# When True, `_filenode_init` skips PropertySet construction entirely (see
# its own comment) — used for `parser.parse_one_file`'s first,
# marker-detection-only pass over a section, before it's known whether the
# file is password-protected.
_active_marker_probe = [False]


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
    # parses (including pass 2's real shadow-decrypted read) are untouched.
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

    # Raw jcid int of the FileNode currently constructing its propertySet (set
    # just before, reset right after) — lets _ospropset_init's Attempt 2 know
    # what KIND of object it's scanning for when the generic scan fails, so a
    # title-specific rescue pass can require the actual expected property
    # (CachedTitleString) instead of accepting any plausible-looking neighbor.
    _active_expected_jcid = [None]

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
            # rebuilds everything through the shadow buffer) or unprotected
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
        _active_expected_jcid[0] = jcid.jcid
        try:
            file.seek(ref.stp)
            self.propertySet = _pyonenote_filenode.ObjectSpaceObjectPropSet(file, document)
        except Exception:
            pass  # e.g. ArrayOfPropertyValues (0x10) unimplemented -> skip this object
        finally:
            file.seek(cur)
            _active_expected_jcid[0] = None

    _pyonenote_filenode.FileNode.__init__ = _filenode_init

    def _get_properties(self):
        if self._properties:
            return self._properties
        nodes = []
        OneDocment.traverse_nodes(self.root_file_node_list, nodes, [])
        self._properties = []
        for node in nodes:
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
                })
            except Exception:
                pass
        return self._properties

    OneDocment.get_properties = _get_properties

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

    # pyOneNote raises NotImplementedError on property type 0x10 (ArrayOfPropertyValues)
    # mid-way through reading an object's property set, which aborts the ENTIRE .one
    # file (every page in the section is lost). Each object's property set is read at
    # an absolute offset and the caller restores the file cursor afterwards, so a
    # partially-read set is safe: stop gracefully at the unsupported property and pad
    # the rest, and the rest of the section keeps parsing. Loses only the trailing
    # properties of the one object that contains the array.
    _orig_propset_init = _pyonenote_filenode.PropertySet.__init__

    # cProperties is a raw u16 (up to 65535) — landing on a misaligned/garbage
    # shadow position can make it claim tens of thousands of properties, and
    # the vendor loop below has no cap, so it burns real wall-clock time
    # (measured: tens of seconds on a real ~30MB section) reading and
    # discarding PropertyID after PropertyID before the except-branch throws
    # the whole thing away anyway. A real OneNote object has at most a
    # handful to a few dozen properties, never hundreds — peek cProperties
    # BEFORE paying for that loop and short-circuit to the same degenerate
    # empty result the exception handler already produces.
    _MAX_PLAUSIBLE_CPROPERTIES = 300

    def _implausible_cprops(buf: bytes, start: int) -> bool:
        head = buf[start:start + 2]
        if len(head) < 2:
            return True
        return struct.unpack('<H', head)[0] > _MAX_PLAUSIBLE_CPROPERTIES

    def _propset_init(self, *args, **kwargs):
        file = args[0] if args else kwargs.get('file')
        shadow = _active_shadow[0]
        if shadow is not None and file is not None:
            # The only genuinely-encrypted part of a protected file is each
            # PropertySet's own body (see decrypt.py) — everything else
            # (FileNode headers, ObjectDeclaration/jcid/oid, chunk
            # references) is real plaintext and must keep reading from the
            # ORIGINAL file. Redirect just this read to the decrypted shadow
            # buffer at the same offset, then resync the real file's cursor
            # to wherever the shadow read left off (encryption is length-
            # preserving, so offsets in both buffers always agree).
            start = file.tell()
            if _implausible_cprops(shadow, start):
                # Same degenerate result the except-branch below produces,
                # without paying for the loop — feed the real constructor a
                # stream that reports cProperties=0 itself so it still sets
                # every OTHER attribute (current_revision, indent, ...) the
                # normal way, just correctly-fast instead of correctly-slow.
                _orig_propset_init(self, io.BytesIO(b'\x00\x00'), *args[1:], **kwargs)
                self.current = start
                file.seek(start + 2)
                return
            shadow_stream = io.BytesIO(shadow)
            shadow_stream.seek(start)
            shadow_args = (shadow_stream,) + args[1:]
            try:
                _orig_propset_init(self, *shadow_args, **kwargs)
            except (NotImplementedError, ValueError, AttributeError, TypeError, struct.error):
                read = len(getattr(self, 'rgData', []) or [])
                self.rgPrids = getattr(self, 'rgPrids', [])[:read]
                self.cProperties = read
            finally:
                file.seek(shadow_stream.tell())
            return
        try:
            _orig_propset_init(self, *args, **kwargs)
        except (NotImplementedError, ValueError, AttributeError, TypeError, struct.error):
            # AttributeError: a stream (e.g. ContextIDs) that a property type
            # expects turns out to be None — happens when reading into an
            # encrypted property-set body with no password supplied (see the
            # encryption-key fix above) — or genuinely corrupt data.
            # TypeError: pyOneNote's own nested-PropertySet handler (property
            # type 0x11) calls `PropertySet(file)` with 4 required args missing
            # — a genuine pre-existing bug, independent of encryption.
            # Keep only the fully-read (prid, value) pairs; truncate the count so
            # get_properties() iterates just those (padding with None would crash
            # its struct.unpack on typed values).
            read = len(getattr(self, 'rgData', []) or [])
            self.rgPrids = getattr(self, 'rgPrids', [])[:read]
            self.cProperties = read

    _pyonenote_filenode.PropertySet.__init__ = _propset_init

    # ObjectSpaceObjectPropSet reads its OIDs/OSIDs/ContextIDs compact-id
    # streams directly off `file`, BEFORE constructing the PropertySet body —
    # those streams live in the same encrypted region as the property values
    # that reference them (a run's ObjectSpaceID-typed "which page am I on"
    # property, an image's PictureContainer reference, ...). The PropertySet
    # patch above only redirects the body itself; without also redirecting
    # here, every ID resolved through OIDs/OSIDs/ContextIDs stays garbage even
    # once the property VALUES decrypt correctly, so runs/images never
    # resolve to a page group. Same shadow-buffer swap, one level up.
    _orig_ospropset_init = _pyonenote_filenode.ObjectSpaceObjectPropSet.__init__

    class _EmptyIDStream:
        """Stand-in for a missing OIDs/OSIDs/ContextIDs stream (see Attempt 2
        below, where the real one was never read): `.read()` behaves like
        `_object_stream_read` at end-of-stream (returns None) so an
        ObjectID/ObjectSpaceID/ContextID-typed property just resolves to
        None instead of crashing PropertySet.__init__ and losing every
        property that comes after it in the same object."""

        def read(self):
            return None

    _EMPTY_ID_STREAM = _EmptyIDStream()

    class _ReplayIDStream:
        """A REAL OIDs/OSIDs/ContextIDs stream, pre-read (see
        `_read_plaintext_id_streams` below) — `.read()` replays its
        CompactIDs in order, same sequential-consumption contract as
        `_object_stream_read`, so `PropertySet.__init__`'s OID/ObjectSpaceID/
        ContextID-typed property resolution gets the ACTUAL referenced
        object instead of `_EmptyIDStream`'s permanent None."""

        def __init__(self, items):
            self._items = items
            self._head = 0

        def read(self):
            if self._head < len(self._items):
                v = self._items[self._head]
                self._head += 1
                return v
            return None

    def _read_plaintext_id_streams(file, start, document):
        """OIDs/OSIDs/ContextIDs compact-id streams turn out to be
        STRUCTURAL, not content — real plaintext even for a protected
        object, same as everything else in this module that isn't a
        PropertySet's own property VALUES (FileNode headers, jcid/oid,
        chunk references, and now this). Verified empirically, not by
        inspection: Attempt 1 (below) reads these same 3 streams through the
        SHADOW (decrypted) buffer and gets implausible garbage counts every
        time — decrypting already-plaintext bytes with AES produces noise,
        which is exactly the "implausible stream count" failure Attempt 1
        always hits. Reading them from the REAL file instead, at the same
        object start Attempt 1 already uses, gives small, consistent,
        genuinely-valid counts (confirmed: the SAME 2-OID/no-OSID/no-Context
        shape recurred across a dozen unrelated real objects in a real
        file) whose resolved ExtendedGUIDs are real, plausible object
        references — this is what finally makes `ParagraphStyle`/
        `TextRunFormatting`/`PictureContainer` resolve for protected content
        at all, previously undocumented as even possible.

        Returns `(oids, osids, ctxids)` (each a list of CompactID, `osids`/
        `ctxids` empty when their stream isn't present) or None if the
        plaintext at `start` doesn't parse as this structure (caller falls
        back to the pre-existing empty-stream behavior, unchanged).
        """
        try:
            cur = file.tell()
            file.seek(start)
            oids_hdr = _pyonenote_filenode.ObjectSpaceObjectStreamHeader(file)
            if oids_hdr.Count > _MAX_PLAUSIBLE_ID_COUNT:
                return None
            oids = [_pyonenote_filenode.CompactID(file, document) for _ in range(oids_hdr.Count)]
            osids = []
            if not oids_hdr.OsidStreamNotPresent:
                osids_hdr = _pyonenote_filenode.ObjectSpaceObjectStreamHeader(file)
                if osids_hdr.Count > _MAX_PLAUSIBLE_ID_COUNT:
                    return None
                osids = [_pyonenote_filenode.CompactID(file, document) for _ in range(osids_hdr.Count)]
            ctxids = []
            if oids_hdr.ExtendedStreamsPresent:
                ctx_hdr = _pyonenote_filenode.ObjectSpaceObjectStreamHeader(file)
                if ctx_hdr.Count > _MAX_PLAUSIBLE_ID_COUNT:
                    return None
                ctxids = [_pyonenote_filenode.CompactID(file, document) for _ in range(ctx_hdr.Count)]
            return oids, osids, ctxids
        except Exception:
            return None
        finally:
            file.seek(cur)

    def _propset_known_score(body) -> int:
        """How many of a decoded PropertySet's own properties resolved to a
        real (non-'Unknown') name — a cheap, purely-structural signal of
        "did we actually land on a real PropertySet", used below to tell a
        correct decrypt apart from decrypted-garbage that happens not to
        raise (e.g. a small cProperties by chance)."""
        if body is None:
            return 0
        return sum(1 for p in getattr(body, 'rgPrids', []) if str(p) != 'Unknown')

    _SHADOW_REALIGN_WINDOW = 128
    _SHADOW_REALIGN_MIN_SCORE = 2

    def _score_shadow_body_at(shadow, off) -> int:
        """Cheap structural peek: does `shadow[off:]` look like a PropertySet
        body (cProperties, rgPrids) on its own, no OIDs/OSIDs/ContextIDs
        prefix? Scores by how many prids resolve to a real property name."""
        if off < 0 or off + 2 > len(shadow):
            return -1
        cprops, = struct.unpack('<H', shadow[off:off + 2])
        if not (0 < cprops <= 32):
            return -1
        pos = off + 2
        known = 0
        for _ in range(cprops):
            if pos + 4 > len(shadow):
                return -1
            val, = struct.unpack('<I', shadow[pos:pos + 4])
            if val in _pyonenote_filenode.PropertyID._property_id_name_mapping:
                known += 1
            pos += 4
        return known

    # Reverse-looked-up (not hardcoded) so this stays correct if pyOneNote's
    # own tables ever change: jcidPageMetaData's raw jcid, and
    # CachedTitleString's raw property id — used by the title-rescue scan
    # below.
    _JCID_PAGE_META_DATA = next(
        (k for k, v in _pyonenote_filenode.JCID._jcid_name_mapping.items() if v == 'jcidPageMetaData'), None,
    )
    _PRID_CACHED_TITLE_STRING = next(
        (k for k, v in _pyonenote_filenode.PropertyID._property_id_name_mapping.items() if v == 'CachedTitleString'),
        None,
    )
    _TITLE_RESCUE_WINDOW = 4096

    def _scan_contains_prid(shadow, off, target_prid) -> bool:
        """Same cheap structural peek as `_score_shadow_body_at`, but asks a
        narrower question: is `target_prid` specifically among this
        candidate's property ids? Used only by the title-rescue scan, where
        "some plausible-looking properties" isn't enough evidence — a
        neighboring, unrelated object's body can score just as well by
        chance (verified: a real false positive here, `LastModifiedTime`/
        `OffsetFromParentHoriz`/... with no title, outscored the genuine
        title's position when the window was widened) — but only the REAL
        title object will ever have `CachedTitleString`."""
        if off < 0 or off + 2 > len(shadow):
            return False
        cprops, = struct.unpack('<H', shadow[off:off + 2])
        if not (0 < cprops <= 32):
            return False
        pos = off + 2
        for _ in range(cprops):
            if pos + 4 > len(shadow):
                return False
            val, = struct.unpack('<I', shadow[pos:pos + 4])
            if val == target_prid:
                return True
            pos += 4
        return False

    def _rescue_title_scan(shadow, start):
        """A wider (4096-byte) scan used ONLY for `jcidPageMetaData` objects
        that the normal 128-byte Attempt-2 scan couldn't place — gated by
        REQUIRING `CachedTitleString` specifically among the candidate's own
        properties, not just a plausible-looking score, which is what makes
        widening the window safe here when it wasn't safe generically (see
        `_scan_contains_prid`'s docstring). Returns the first matching
        offset or None."""
        for d in range(_TITLE_RESCUE_WINDOW):
            off = start + d
            if _scan_contains_prid(shadow, off, _PRID_CACHED_TITLE_STRING):
                return off
        return None

    def _ospropset_init(self, file, document):
        shadow = _active_shadow[0]
        if shadow is None:
            _orig_ospropset_init(self, file, document)
            return
        start = file.tell()

        # Attempt 1: the normal OIDs -> OSIDs -> ContextIDs -> body chain,
        # read from the decrypted shadow buffer at the same offset as the
        # real file (correct when a protected object still uses the same
        # framing an unprotected file would).
        shadow_stream = io.BytesIO(shadow)
        shadow_stream.seek(start)
        try:
            _orig_ospropset_init(self, shadow_stream, document)
            if _propset_known_score(getattr(self, 'body', None)) > 0:
                file.seek(shadow_stream.tell())
                return
        except Exception:
            pass

        # Attempt 2: empirically, a protected object's real content is NOT
        # always reachable via that same OIDs/OSIDs/ContextIDs framing at
        # `ref.stp` — the genuinely-encrypted region for these objects turns
        # out to start a short, small distance further in, as a bare
        # PropertySet body with no ID-stream prefix at all (verified against
        # a real protected section: this recovers real CachedTitleString /
        # RichEditTextUnicode / ParagraphStyle content that Attempt 1 can't
        # reach). Scan a small forward window for the first position that
        # looks like a real property set (mostly-recognized property names,
        # not just a plausible-looking count) and use that instead.
        #
        # The OIDs/OSIDs/ContextIDs streams THEMSELVES are structural
        # plaintext (see `_read_plaintext_id_streams`) even though the body
        # scanned for above is real ciphertext — read them from the real
        # file at this object's own start, same position Attempt 1 already
        # tried (and failed at, because it read them through the WRONG,
        # shadow/decrypted buffer instead). When this succeeds, an OID/
        # ObjectSpaceID/ContextID-typed property (ParagraphStyle,
        # TextRunFormatting, PictureContainer, ...) resolves to the real
        # referenced object instead of unconditionally None — previously
        # undocumented as even possible, confirmed empirically against a
        # real file. Falls back to the empty stub, unchanged, if this
        # object's plaintext doesn't parse as this structure.
        real_ids = _read_plaintext_id_streams(file, start, document)

        best_off, best_score = None, 0
        for d in range(_SHADOW_REALIGN_WINDOW):
            s = _score_shadow_body_at(shadow, start + d)
            if s > best_score:
                best_score, best_off = s, start + d

        # A title whose CachedTitleString didn't turn up in the normal
        # (narrow, generic) scan gets ONE more chance: a much wider scan
        # that's safe specifically BECAUSE it requires CachedTitleString by
        # name, not just a plausible score — see `_rescue_title_scan`'s
        # docstring for the real false positive this guard was built to
        # reject. Only attempted for objects the caller (`_filenode_init`)
        # already knows are a jcidPageMetaData declaration.
        if (
            _active_expected_jcid[0] == _JCID_PAGE_META_DATA
            and _PRID_CACHED_TITLE_STRING is not None
            and not (best_off is not None and _scan_contains_prid(shadow, best_off, _PRID_CACHED_TITLE_STRING))
        ):
            rescued = _rescue_title_scan(shadow, start)
            if rescued is not None:
                best_off, best_score = rescued, max(best_score, _SHADOW_REALIGN_MIN_SCORE)

        if best_off is not None and best_score >= _SHADOW_REALIGN_MIN_SCORE:
            # Build with the plaintext-derived real ID streams AND with the
            # empty stub, then keep whichever actually decoded MORE
            # properties (`cProperties` reflects how far the read got before
            # any truncating exception — see `_propset_init`'s own graceful-
            # truncation contract). Necessary safety net, not paranoia:
            # verified real regression on a real file — the real-ID-stream
            # read is only validated against the simple 2-OID text-run shape
            # it was found with; for other object shapes (images, titles,
            # ...) a real-but-WRONG-for-this-object stream can make an
            # OID/ObjectSpaceID-typed property misbehave partway through and
            # truncate the object early, losing content the empty-stream
            # path decoded fine. Comparing after the fact means real streams
            # only ever help, never regress.
            # One shared stream, reseeked before each attempt — avoids
            # copying the whole (possibly tens-of-MB) shadow buffer twice
            # per object; `io.BytesIO(shadow)` was already a known hot-path
            # cost here even before this change (see the cProperties-cap
            # performance fix above).
            cand_stream = io.BytesIO(shadow)

            def _try_build(oids, osids, ctx):
                cand_stream.seek(best_off)
                try:
                    return _pyonenote_filenode.PropertySet(cand_stream, oids, osids, ctx, document), oids, osids, ctx
                except Exception:
                    return None

            candidates = []
            if real_ids is not None:
                real_streams = tuple(_ReplayIDStream(x) for x in real_ids)
                built = _try_build(*real_streams)
                if built is not None:
                    candidates.append(built)
            built_empty = _try_build(_EMPTY_ID_STREAM, _EMPTY_ID_STREAM, _EMPTY_ID_STREAM)
            if built_empty is not None:
                candidates.append(built_empty)

            if candidates:
                body, oids, osids, ctx = max(candidates, key=lambda c: getattr(c[0], 'cProperties', 0))
                self.OIDs = oids
                self.OSIDs = osids
                self.ContextIDs = ctx
                self.body = body
                return

        # Attempt 3: not every object declaration in a protected section
        # actually sits inside the encrypted region (some structural/shared
        # ones stay real plaintext) — if nothing above panned out, fall back
        # to reading it as-is instead of aborting the whole file's parse.
        file.seek(start)
        _orig_ospropset_init(self, file, document)

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
