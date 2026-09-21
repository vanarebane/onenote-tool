"""Real-file CONTENT-correctness tests, driven by tests/fixtures/corpus_manifest.py.

Unlike test_smoke_local_file.py (generic contract shape against one file),
this asserts specific, real content against a named multi-file corpus — see
tests/fixtures/CORPUS_MANIFEST.md for what each file covers and why. Every
entry skips individually when its file isn't present in
ONENOTE_TOOL_TEST_CORPUS_DIR, so a partial corpus still runs what it can.

Recommended as a pre-release gate before tagging/publishing — see
tests/fixtures/README.md.
"""
import os

import pytest

from onenote_tool.convert import convert_onepkg
from tests.fixtures.corpus_manifest import CORPUS, CorpusFile, Expectation, ScaleCheck


def _converted(corpus_file: CorpusFile, corpus_dir: str):
    path = os.path.join(corpus_dir, corpus_file.filename)
    if not os.path.exists(path):
        pytest.skip(f'{corpus_file.filename} not present in ONENOTE_TOOL_TEST_CORPUS_DIR')
    passwords = {}
    for section_name, env_var in corpus_file.password_env.items():
        value = os.environ.get(env_var)
        if value:
            passwords[section_name] = value
    return convert_onepkg(path, section_passwords=passwords or None)


def _find_page(result, section_name, page_title):
    for section in result.sections:
        if section.name != section_name:
            continue
        for page in section.pages:
            if page.title == page_title:
                return page
    return None


def _assert_expectation(result, exp: Expectation):
    page = _find_page(result, exp.section, exp.page_title)
    assert page is not None, (
        f'page {exp.page_title!r} not found in section {exp.section!r} '
        f'(sections present: {[s.name for s in result.sections]!r})'
    )
    if exp.must_contain_block_kind is not None:
        kinds = {b.get('kind') for b in page.blocks}
        assert exp.must_contain_block_kind in kinds, (
            f'expected block kind {exp.must_contain_block_kind!r} on '
            f'{exp.section!r}/{exp.page_title!r}, got {kinds!r}'
        )
    if exp.must_contain_text is not None:
        haystack = ' '.join(b.get('html', '') for b in page.blocks)
        assert exp.must_contain_text in haystack, (
            f'expected text {exp.must_contain_text!r} on {exp.section!r}/{exp.page_title!r}'
        )
    if exp.min_regions is not None:
        assert len(page.regions) >= exp.min_regions, (
            f'expected >= {exp.min_regions} regions on {exp.section!r}/{exp.page_title!r}, '
            f'got {len(page.regions)}'
        )
    if exp.background_color_set:
        assert page.background_color, f'expected background_color on {exp.section!r}/{exp.page_title!r}'
    if exp.rule_lines_set:
        assert page.rule_lines, f'expected rule_lines on {exp.section!r}/{exp.page_title!r}'


def _assert_scale(result, check: ScaleCheck):
    assert result.page_count >= check.min_page_count, (
        f'page_count regressed: {result.page_count} < {check.min_page_count}'
    )
    assert len(result.sections) >= check.min_section_count, (
        f'section count regressed: {len(result.sections)} < {check.min_section_count}'
    )
    parse_errors = sum(
        count for key, count in result.report.failed.items()
        if key.startswith('section_parse_error')
    )
    assert parse_errors <= check.max_section_parse_errors, (
        f'{parse_errors} section_parse_error entries, expected <= {check.max_section_parse_errors}: '
        f'{result.report.failed!r}'
    )


@pytest.mark.parametrize(
    'corpus_file',
    [cf for cf in CORPUS if cf.scale_check is not None],
    ids=lambda cf: cf.filename,
)
def test_corpus_scale(local_corpus_dir, corpus_file):
    result = _converted(corpus_file, local_corpus_dir)
    _assert_scale(result, corpus_file.scale_check)


def _expectation_cases():
    cases = []
    for cf in CORPUS:
        for exp in cf.expectations:
            marks = [pytest.mark.xfail(reason=exp.xfail_reason, strict=False)] if exp.xfail_reason else []
            cases.append(pytest.param(cf, exp, marks=marks, id=f'{cf.filename}::{exp.section}::{exp.page_title}'))
    return cases


@pytest.mark.parametrize('corpus_file,expectation', _expectation_cases())
def test_corpus_expectation(local_corpus_dir, corpus_file, expectation):
    # xfail entries run this SAME assertion — a fix flips them to XPASS
    # (visible, non-fatal since strict=False) instead of a silent pass.
    result = _converted(corpus_file, local_corpus_dir)
    _assert_expectation(result, expectation)
