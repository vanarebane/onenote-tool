from onenote_tool.images import _attachment_kind


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
