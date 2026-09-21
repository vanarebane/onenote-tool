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
    xfail_reason: str | None = None


@dataclass
class ScaleCheck:
    """A crash-resilience / regression check that doesn't require quoting
    any real (possibly private) page/section title — just structural facts.
    """

    min_page_count: int
    min_section_count: int
    max_section_parse_errors: int = 0


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
            # Known revision-duplicate spurious page (recovery-techniques-log.md)
            # — its CONTINUED EXISTENCE is the regression signal (a genuine,
            # accepted, unfixed artifact), not a bug to xfail. If a future
            # change makes this page disappear, re-read the known-limitations.md
            # entry before assuming that's an improvement — it could mean real
            # content silently stopped landing anywhere at all instead.
            Expectation('Rich content', '2020-03-22'),
            # --- Backgrounds: unprotected background_color / rule_lines coverage ---
            Expectation('Backgrounds', 'Page color', background_color_set=True),
            Expectation('Backgrounds', 'Page colour and lines', background_color_set=True, rule_lines_set=True),
            Expectation('Backgrounds', 'College Ruled', rule_lines_set=True),
            # --- Section-name corruption regression (already-public repro) ---
            Expectation('Coma, Test', 'Test, test'),
            # NOTE: no "Password protected" section entry yet — with no real
            # content in that section beyond the generic placeholder page,
            # there's nothing content-correct to assert until checklist item
            # C (protected equation / page background / rule-lines) is
            # authored. Add real Expectation entries here once it exists;
            # they'll only actually verify anything once
            # ONENOTE_TOOL_TEST_PW_TEST_PROTECTED is also set locally.
        ],
        scale_check=ScaleCheck(min_page_count=50, min_section_count=12),
    ),
    CorpusFile(
        filename='DIY.onepkg',
        scale_check=ScaleCheck(min_page_count=425, min_section_count=22),
    ),
    CorpusFile(
        filename='Shared.onepkg',
        password_env={},  # one protected section exists; name withheld (real personal notebook)
        scale_check=ScaleCheck(min_page_count=1131, min_section_count=66),
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
