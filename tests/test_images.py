from onenote_tool.images import _attachment_kind, _build_embedded_file
from onenote_tool.models import ElementReport


def test_attachment_kind_audio():
    assert _attachment_kind('.mp3') == 'audio'
    assert _attachment_kind('.WAV') == 'audio'


def test_attachment_kind_video():
    assert _attachment_kind('.mp4') == 'video'
    assert _attachment_kind('.MOV') == 'video'


def test_attachment_kind_other_falls_back_to_file():
    assert _attachment_kind('.zip') == 'file'
    assert _attachment_kind('.xlsx') == 'file'
    assert _attachment_kind('') == 'file'
    assert _attachment_kind(None) == 'file'


def test_build_embedded_file_exact_identity_match():
    # files_by_identity is keyed by _extract_guid's raw, un-normalized
    # output (the literal '<ExtendedGUID> (guid, n)' string, comma-space
    # included) — same shape parser.py builds it in, not `_norm_identity`'s
    # comma-no-space form.
    val = {'EmbeddedFileContainer': ['<ExtendedGUID> (aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa, 5)'],
           'EmbeddedFileName': 'report.pdf\x00'}
    files_by_identity = {
        '<ExtendedGUID> (aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa, 5)': {'extension': '.pdf', 'content': b'%PDF'},
    }
    consumed = set()
    f = _build_embedded_file(val, files_by_identity, [0], consumed, ElementReport())
    assert f is not None and f.filename == 'report.pdf' and f.data == b'%PDF'
    assert '<ExtendedGUID> (aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa, 5)' in consumed


def test_build_embedded_file_falls_back_to_base_guid_on_stale_reference():
    # Real bug, verified against a real PDF: the EmbeddedFileContainer
    # reference pointed at a stale revision (,33) of the same base object
    # get_files() actually recorded the content under (,40).
    base = '<ExtendedGUID> (aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
    val = {'EmbeddedFileContainer': [f'{base}, 33)']}
    files_by_identity = {
        f'{base}, 40)': {'extension': '.pdf', 'content': b'%PDF'},
        f'{base}, 41)': {'extension': '.png', 'content': b'preview'},  # a preview image, must be ignored
    }
    consumed = set()
    f = _build_embedded_file(val, files_by_identity, [0], consumed, ElementReport())
    assert f is not None and f.data == b'%PDF'
    # The RESOLVED identity (,40) must be marked consumed, not the stale
    # requested one (,33) — otherwise the end-of-parse orphan-file check
    # (which looks up get_files()'s own identities) double-reports this
    # as an unresolved failure despite having just attached correctly.
    assert f'{base}, 40)' in consumed


def test_build_embedded_file_ambiguous_base_guid_does_not_guess():
    # Real shape, verified against a real file: a page with two genuine,
    # different non-image attachments (.wav and .ogg) sharing one base
    # GUID. A base-GUID fallback must not pick one arbitrarily.
    base = '<ExtendedGUID> (bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'
    val = {'EmbeddedFileContainer': [f'{base}, 99)']}
    files_by_identity = {
        f'{base}, 20)': {'extension': '.wav', 'content': b'RIFF'},
        f'{base}, 29)': {'extension': '.ogg', 'content': b'OggS'},
    }
    consumed = set()
    f = _build_embedded_file(val, files_by_identity, [0], consumed, ElementReport())
    assert f is None
    assert not consumed
