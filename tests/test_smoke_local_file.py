"""Real-file smoke tests — SKIPPED unless the developer points them at a
local file (see tests/fixtures/README.md). These are the tests that
actually exercise pyOneNote parsing, decryption, and structural recovery;
the rest of the suite is synthetic and can't stand in for these.
"""
import io

from onenote_tool.convert import convert_onepkg
from onenote_tool.models import ElementReport
from onenote_tool.parser import parse_one_file

_VALID_KINDS = {'text', 'image', 'audio', 'video', 'table', 'ink', 'file_links'}


def _assert_valid_blocks_contract(page):
    for block in page.blocks:
        assert 'x' not in block and 'y' not in block, f'block still carries position keys: {block}'
        assert block.get('kind') in _VALID_KINDS, f'unexpected block kind: {block}'
        assert 'html' in block
        if block['kind'] == 'ink':
            assert block['html'].startswith('<svg') and block['html'].endswith('</svg>')
        if block['kind'] == 'image':
            assert block.get('image_src')
        if block['kind'] == 'audio':
            assert block.get('audio_src')
        if block['kind'] == 'video':
            assert block.get('video_src')


def _assert_images_decode(page):
    from PIL import Image
    for img in page.images:
        im = Image.open(io.BytesIO(img.data))
        im.verify()  # raises if the bytes aren't a valid image


def _assert_valid_regions_contract(page):
    for region in page.regions:
        assert isinstance(region.x, int) and isinstance(region.y, int)
        assert isinstance(region.width, int) and isinstance(region.height, int)
        assert region.blocks, 'an empty region should have been dropped, not returned'
        _assert_valid_blocks_contract_list(region.blocks)


def _assert_valid_blocks_contract_list(blocks):
    for block in blocks:
        assert 'x' not in block and 'y' not in block, f'block still carries position keys: {block}'
        assert block.get('kind') in _VALID_KINDS, f'unexpected block kind: {block}'
        assert 'html' in block
        if block['kind'] == 'ink':
            assert block['html'].startswith('<svg') and block['html'].endswith('</svg>')
        if block['kind'] == 'image':
            assert block.get('image_src')
        if block['kind'] == 'audio':
            assert block.get('audio_src')
        if block['kind'] == 'video':
            assert block.get('video_src')


def _assert_valid_blocks_contract(page):
    _assert_valid_blocks_contract_list(page.blocks)
    _assert_valid_regions_contract(page)


def test_parse_one_file_contract(local_one_file):
    report = ElementReport()
    pages, password_status = parse_one_file(local_one_file, report, passwords=None)
    assert isinstance(pages, list)
    for page in pages:
        _assert_valid_blocks_contract(page)
        _assert_images_decode(page)


def test_convert_onepkg_contract(local_onepkg):
    result = convert_onepkg(local_onepkg)
    assert result.notebook_name
    assert result.page_count >= 0
    for section in result.sections:
        for page in section.pages:
            _assert_valid_blocks_contract(page)
            _assert_images_decode(page)
