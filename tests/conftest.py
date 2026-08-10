"""Shared pytest fixtures.

No real OneNote bytes are committed to this repo (see tests/fixtures/README.md
for why) — tests that need a real `.onepkg`/`.one` file read its path from an
environment variable and skip cleanly when it isn't set, so a fresh clone or
CI run never fails for a missing fixture.
"""
import os

import pytest


@pytest.fixture
def local_onepkg() -> str:
    """Path to a real, locally-supplied `.onepkg` file, or skip."""
    path = os.environ.get('ONENOTE_TOOL_TEST_ONEPKG')
    if not path or not os.path.exists(path):
        pytest.skip(
            'Set ONENOTE_TOOL_TEST_ONEPKG to a local .onepkg file to run this test '
            '(see tests/fixtures/README.md).',
        )
    return path


@pytest.fixture
def local_one_file() -> str:
    """Path to a real, locally-supplied single `.one` section file, or skip."""
    path = os.environ.get('ONENOTE_TOOL_TEST_ONE_FILE')
    if not path or not os.path.exists(path):
        pytest.skip(
            'Set ONENOTE_TOOL_TEST_ONE_FILE to a local .one file to run this test '
            '(see tests/fixtures/README.md).',
        )
    return path
