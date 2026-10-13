"""Declarative real-file corpus manifest for `tests/test_corpus_content.py`.

See CORPUS_MANIFEST.md (same directory) for the human-readable version —
keep the two in sync by hand when the corpus changes.

Passwords are never literals here: `CorpusFile.password_env` maps a
section name to the NAME of an environment variable holding its real
password, so nothing secret is ever committed. An entry whose file isn't
present in the configured corpus dir is skipped individually, not the
whole suite (the corpus is expected to be partial on most machines).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Expectation:
    """One real-content assertion against a single converted page."""

    section: str
    page_title: str
    must_contain_block_kind: str | None = None
    must_contain_text: str | None = None
    min_regions: int | None = None
    background_color_set: bool | None = None
    rule_lines_set: bool | None = None
    # True: the page must NOT exist (a previously-produced spurious page).
    absent: bool = False
    xfail_reason: str | None = None


@dataclass
class ScaleCheck:
    """A crash-resilience / regression check that doesn't require quoting
    any real (possibly private) page/section title — just structural facts.
    """

    min_page_count: int
    min_section_count: int
    max_section_parse_errors: int = 0
    min_resolved_authors: int = 0


@dataclass
class CorpusFile:
    filename: str
    password_env: dict[str, str] = field(default_factory=dict)
    expectations: list[Expectation] = field(default_factory=list)
    scale_check: ScaleCheck | None = None


CORPUS: list[CorpusFile] = [
    CorpusFile(
        filename='Test.onepkg',
        password_env={'Password protected': 'ONENOTE_TOOL_TEST_PW_TEST_PROTECTED'},
        expectations=[
            # --- Normal section: existing, verified-passing content ---
            Expectation('Normal', '5 images', must_contain_block_kind='image'),
            Expectation('Normal', 'Ink drawing', must_contain_block_kind='ink'),
            Expectation('Normal', 'Multi floating-box page', min_regions=6),
            Expectation('Normal', 'Floating box sizes', min_regions=4),
            Expectation('Normal', '2. Real audio files', must_contain_block_kind='audio'),
            Expectation('Normal', '3. Real video files', must_contain_block_kind='video'),
            Expectation('Normal', 'Table', must_contain_block_kind='table'),
            # --- Rich content: page-title-rename repro (checklist item B — already exists) ---
            Expectation('Rich content', 'Rename title third time'),
            # Formerly a spurious extra page built from stray table-cell runs
            # (their TextRunFormatting pointed into another object space).
            # OneNote's own MHT export has no such page; object-space page
            # attribution removed it.
            Expectation('Rich content', '2020-03-22', absent=True),
            # Same mechanism: the second "Mixed content" page was a split.
            Expectation('Normal', 'Mixed content', must_contain_block_kind='table'),
            # --- Backgrounds: unprotected background_color / rule_lines coverage ---
            Expectation('Backgrounds', 'Page color', background_color_set=True),
            Expectation('Backgrounds', 'Page colour and lines', background_color_set=True, rule_lines_set=True),
            Expectation('Backgrounds', 'College Ruled', rule_lines_set=True),
            # --- Section-name corruption regression (already-public repro) ---
            Expectation('Coma, Test', 'Test, test'),
            # --- Password protected: same pages as Normal, encrypted. Only
            # verifies anything once ONENOTE_TOOL_TEST_PW_TEST_PROTECTED is set.
            Expectation('Password protected', '5 images', must_contain_block_kind='image'),
            Expectation('Password protected', 'Ink drawing', must_contain_block_kind='ink'),
            Expectation('Password protected', 'Multi floating-box page', min_regions=6),
            Expectation('Password protected', 'Floating box sizes', min_regions=4),
            Expectation('Password protected', '2. Real audio files', must_contain_block_kind='audio'),
            Expectation('Password protected', '3. Real video files', must_contain_block_kind='video'),
            Expectation('Password protected', 'Table', must_contain_block_kind='table'),
            Expectation('Password protected', 'Lot of text', must_contain_text='ReBarUEFI'),
        ],
        scale_check=ScaleCheck(min_page_count=50, min_section_count=12),
    ),
    CorpusFile(
        filename='DIY.onepkg',
        scale_check=ScaleCheck(min_page_count=340, min_section_count=22),
    ),
    CorpusFile(
        filename='Shared.onepkg',
        password_env={},  # one protected section exists; name withheld (real personal notebook)
        # min_resolved_authors: the per-author-attribution regression check —
        # real run count observed was 14901/20235 rendered paragraph tags
        # carrying a resolved `data-author` attribute; well under half of
        # that as the floor, same margin-below-observed convention as the
        # page/section counts above.
        scale_check=ScaleCheck(min_page_count=917, min_section_count=66, min_resolved_authors=5000),
    ),
]

# --- Checklist content not yet authored (see CORPUS_MANIFEST.md) ---
# Once built, add real Expectation entries here (or above) rather than
# guessing at page titles ahead of time:
#   A. "Visio & Tags" section in Test.onepkg — Visio object + OneNote Tags,
#      currently the ONLY Table A row marked "never tested at all".
#   C. Protected-section equation / page background / rule-lines pages,
#      inside Test.onepkg's existing "Password protected" section (or a
#      new protected section) — the unprotected versions are already
#      covered by the "Backgrounds" section and the existing normal-section
#      equation (see the `failed:equation` key in CORPUS_MANIFEST.md's
#      ground-truth table).
#   D. A page with Arabic/Hebrew (RTL) and Chinese/Cyrillic paragraphs,
#      e.g. inside "Rich content" — Table A has no row for this at all.
