import io
import struct
import threading
import uuid

import pytest

from onenote_tool import _patches

pytestmark = pytest.mark.skipif(not _patches.PYONENOTE_AVAILABLE, reason='pyOneNote not installed')


class _Doc:
    cur_revision = None


class _IDs:
    def __init__(self, items=()):
        self._items = list(items)

    def read(self):
        return self._items.pop(0) if self._items else None


def _propset(raw: bytes, oids=None):
    return _patches._pyonenote_filenode.PropertySet(io.BytesIO(raw), oids or _IDs(), _IDs(), _IDs(), _Doc())


def test_array_of_property_values_is_read_and_later_properties_survive():
    # [MS-ONESTORE] 2.6.9: cProperties u32, an element PropertyID of type
    # 0x11, then that many nested PropertySets. A property AFTER the array
    # (here a 4-byte LanguageID) used to be lost with the array.
    nested = struct.pack('<HI', 1, 0x14003467) + b'\x01\x00\x00\x00'
    raw = (
        struct.pack('<H', 2) + struct.pack('<II', 0x40003489, 0x14001C3B)
        + struct.pack('<I', 1) + struct.pack('<I', 0x44000811) + nested
        + b'\x09\x04\x00\x00'
    )
    ps = _propset(raw)
    assert ps.cProperties == 2
    props = ps.get_properties()
    assert props['NoteTagStates'] == [{'NoteTagPropertyStatus': str(b'\x01\x00\x00\x00')}]
    assert 'LanguageID' in props


def test_nested_property_set_type_0x11():
    nested = struct.pack('<HI', 1, 0x14003467) + b'\x02\x00\x00\x00'
    raw = struct.pack('<H', 1) + struct.pack('<I', 0x44000811 | 0x3467) + nested
    ps = _propset(raw)
    assert ps.cProperties == 1
    assert len(ps.rgData[0].rgPrids) == 1


def test_empty_array_of_property_values():
    raw = struct.pack('<H', 1) + struct.pack('<I', 0x40003489) + struct.pack('<I', 0)
    ps = _propset(raw)
    assert ps.rgData == [[]]


def test_truncated_property_set_keeps_fully_read_properties():
    raw = struct.pack('<H', 2) + struct.pack('<II', 0x14001C3B, 0x14001C3B) + b'\x09\x04\x00\x00' + b'\x01'
    ps = _propset(raw)
    assert ps.cProperties == 1


def test_thread_slot_is_per_thread():
    slot = _patches._ThreadSlot('default')
    slot[0] = 'main'
    seen = []
    t = threading.Thread(target=lambda: seen.append(slot[0]))
    t.start()
    t.join()
    assert seen == ['default']
    assert slot[0] == 'main'
    with pytest.raises(IndexError):
        slot[1]


# --- revision scoping -------------------------------------------------------

class _Header:
    def __init__(self, ntype):
        self.file_node_type = ntype


class _XGuid:
    def __init__(self, n=0, guid=None):
        self.guid = guid or uuid.UUID(int=0)
        self.n = n


class _Node:
    def __init__(self, ntype, data=None, children=(), propset=False):
        self.file_node_header = _Header(ntype)
        self.data = data
        self.children = list(children)
        if propset:
            self.propertySet = object()


class _List:
    def __init__(self, nodes):
        frag = type('F', (), {})()
        frag.fileNodes = nodes
        self.fragments = [frag]


class _RevStart:
    def __init__(self, rid, dep=None, ctx=None):
        self.base = type('B', (), {'rid': rid, 'ridDependent': dep or _XGuid(), 'RevisionRole': 1})()
        self.gctxid = ctx or _XGuid()


def _decl(oid):
    body = type('Body', (), {'oid': oid})()
    return _Node('ObjectDeclaration2RefCountFND', type('D', (), {'body': body})(), propset=True)


def _group(*decls):
    return _Node('ObjectGroupListReferenceFND', children=[_List(list(decls))])


def test_current_revision_skips_version_history_contexts():
    rid_now = _XGuid(1, uuid.uuid4())
    rid_old = _XGuid(1, uuid.uuid4())
    history_ctx = _XGuid(1, uuid.uuid4())
    current = _decl('current')
    snapshot = _decl('snapshot')
    revisions = _List([
        _Node('RevisionManifestStart6FND', _RevStart(rid_now)), _group(current), _Node('RevisionManifestEndFND'),
        _Node('RevisionManifestStart7FND', _RevStart(rid_old, ctx=history_ctx)), _group(snapshot),
        _Node('RevisionManifestEndFND'),
    ])
    assert _patches._current_revision_nodes(revisions) == [current]


def test_current_revision_follows_dependency_chain_without_duplicates():
    base_rid = _XGuid(1, uuid.uuid4())
    top_rid = _XGuid(2, base_rid.guid)
    base_a, base_b, top_a = _decl('a'), _decl('b'), _decl('a')
    revisions = _List([
        _Node('RevisionManifestStart6FND', _RevStart(base_rid)), _group(base_a, base_b),
        _Node('RevisionManifestEndFND'),
        _Node('RevisionManifestStart6FND', _RevStart(top_rid, dep=base_rid)), _group(top_a),
        _Node('RevisionManifestEndFND'),
    ])
    assert _patches._current_revision_nodes(revisions) == [top_a, base_b]
