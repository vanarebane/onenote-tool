# Real-file test corpus — what each notebook covers

None of the files below are committed (see [README.md](README.md) — a
`.onepkg` is someone's actual notebook). This is the index that used to be
missing: a single place describing what each locally-held file is *for*,
instead of having to reconstruct it from scattered references across
`CHANGELOG.md` / `docs/knowledge/known-limitations.md`. Point
`ONENOTE_TOOL_TEST_CORPUS_DIR` at a folder containing whichever of these you
have locally; `tests/test_corpus_content.py` skips per-file, not
all-or-nothing, so a partial corpus is fine.

`tests/fixtures/corpus_manifest.py` is the machine-readable version of this
file — the two are kept in sync by hand; update both together.

## `Test.onepkg` — the deliberately purpose-built test notebook

~80MB, notebook name `Test`. Entirely synthetic dummy content built to
exercise specific format features — nothing personal, safe to name exactly.
As of the last real-file run (see "Ground truth" below): 50 pages across
12 sections (4 top-level + 8 inside one section group).

- **`Normal` section** (20 pages) — the general-purpose regression section.
  Notable pages: `5 images` (dedup/multi-image), `Mixed content` ×2 (text+
  table, and text+image, the second name is a deliberate duplicate-title
  case), `Font sizes` (per-run font-size override), `Lot of text` (long-body
  page + a file attachment), `Table` / `1. Real table with actual data`
  (table rendering), `Spreadsheet` / `File` (non-image attachments),
  `YT video` / `YouTube video embeded` (two independently-named YouTube
  cases), `Text colours`, `Ink drawing` (ink/SVG), `Background` (title vs.
  a page containing a stray content spillover — see known-limitations.md),
  `List` (list rendering, 2 regions), `Multi floating-box page` (6 floating
  regions — the reference "many independent boxes" page) and
  `Floating box sizes` (4 regions, region size verification), `2. Real audio
  files` / `3. Real video files` (real `<audio>`/`<video>` attachments, each
  with an image-preview icon alongside).
- **`Password protected` section** (20 pages) — an encrypted copy of
  `Normal`, same page titles and content. Without its password (env var,
  see `corpus_manifest.py`) it is one `needs_password` placeholder page and
  its expectations skip; with it, it must convert identically to `Normal`.
- **`Rich content` section** (5 pages) — the "interesting real-world content"
  section. `Section page 1` (a citation-styled paragraph with its own
  distinct font-size — the "silently-dropped font size" regression case),
  `Random large table` (2 tables), `Image single`, **`Rename title third
  time`** (a page renamed multiple times — its old titles live in
  version-history snapshots, see known-limitations.md), `Embedded objects`
  (mixed image+text+file page). This section used to also produce a
  spurious `2020-03-22` page (stray table-cell fragments); the manifest now
  asserts it's absent. `Normal`'s `Mixed content` page holds the one real
  equation (binomial theorem), which converts to MathML.
- **`Backgrounds` section** (13 pages) — one page per built-in rule-line
  pattern (`None`, `Narrow Lines`, `College Ruled`, `Standard Rules`,
  `Wide Ruled`, `Small/Medium/Large/Very Large Grid`, `Coloured lines`) plus
  `Page color`, `Page colour and lines`, `Page size as A4` — the full
  `background_color`/`rule_lines` regression set, all in **unprotected**
  sections. This is what makes the protected-section version of the same
  two properties (checklist item C) worth adding — the unprotected side is
  already fully covered.
- **`Section name testing` group** (8 sections) — one section per
  punctuation character in the section name itself (semicolon, period,
  exclamation, apostrophe, hyphen, comma, equals, parens) — the
  CAB-extraction name-corruption regression set. `Coma, Test` is the
  already-public repro for the comma-corruption fix (`CHANGELOG.md`); it
  currently round-trips correctly.
- **Not present at all**: any Visio object, any OneNote page Tag
  ("Important"/"To Do"/custom), any non-Latin or RTL script. These are
  checklist items A and D — genuinely new content, not yet covered anywhere
  in this file.

## `DIY.onepkg` — large real hobbyist notebook (electronics/PC-building/making)

~258MB, notebook name `DIY`, 22 sections across 3 groups, 340 pages (425 before version-history snapshots were excluded). Used as
a scale/crash-resilience check, not fine-grained content assertions (real
personal notebook — page/section titles beyond what's already public via
`CHANGELOG.md` aren't reproduced here). One section is named `Skills, Tools`
— the same already-public comma-corruption repro also present in
`Test.onepkg`'s section-name-testing group, found independently in this
file; converts correctly (uncorrupted) as of the last run. No password-
protected sections. Heavy ink content (261 `ink_drawing` conversions) and
real `.emf`/`.xps` attachments (both already-known unsupported-format
placeholders, per Table A) — good regression coverage for both without any
new content needed.

## `Shared.onepkg` — large real multi-author notebook (~220MB)

66 sections across 6 groups, 917 pages (1131 before version-history snapshots were excluded), synced between 2 real users over
years — the "different shape of complexity" stress file (more revision
generations, more concurrent-edit history than a single-author notebook).
Originally surfaced 2 real parser crashes (`FileNode` object has no
`data` attribute; `get_json()` crashing on an incomplete file declaration —
both fixed, see `CHANGELOG.md`) and the mojibake section-name bug (the
already-public `"Hommikusöögiks"` example). As of the last real-file run:
**zero `section_parse_error` entries** — both original crash repros stay
fixed; this is the regression check for them. One password-protected
section present (`needs_password` until a password is supplied). Real
personal/family content — section and page titles beyond the two
already-public examples above are deliberately not reproduced here; use
this file for scale/crash-resilience assertions (page/section counts,
zero unexpected `section_parse_error`) rather than per-page content checks.
**Basis for the per-author-attribution row (now implemented, see
`docs/knowledge/known-limitations.md`)**: `Author`/`AuthorOriginal`/
`AuthorMostRecent` real properties resolved for 99.9% of all real text runs
across this file's unprotected sections. `corpus_manifest.py`'s
`ScaleCheck.min_resolved_authors` is the regression check for this — a
structural count (`data-author=` occurrences), not a named page/title, so
it's safe against a real personal file. The one password-protected section
here stays untested for this (and everything else) — no working password is
configured for it.

## Ground truth snapshot (informational — will drift as content is added)

Captured via `onenote_tool.convert.convert_onepkg` against the files as they
exist today, before any checklist item (A–D) content is added:

| File | page_count | sections | notable `report.failed` keys already covered |
|---|---:|---:|---|
| `Test.onepkg` (with the test password) | 66 | 12 | `image_ext.emf`, `file.emf` |
| `DIY.onepkg` | 340 | 22 | `image_ext.emf`, `file.emf`, `image_ext.xps`, `file.xps`, `image_duplicate`, `file_duplicate` |
| `Shared.onepkg` | 917 | 66 | `image_missing_data`, `image_duplicate`, `image_ext.emf`, `file.emf`, `image_ext.tmp`, `file.tmp`, `password_protected_section:wrong_password` — **zero `section_parse_error`** |

Re-run `python -c` against `onenote_tool.convert.convert_onepkg(path)` (or
`onenote-tool convert <path>` with the `[cli]` extra) and diff against this
table after adding checklist content, and update both this file and
`corpus_manifest.py` together.
