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
- **`Password protected` section** (1 page) — protected, `password_status`
  starts as `needs_password` until a real password is supplied via env var
  (see `corpus_manifest.py`). **Currently the ONLY content in this section
  is a single generic page** — this is where checklist item C (protected
  math equation + page background color + rule-line page) should land once
  authored; see the main plan for what to add.
- **`Rich content` section** (6 pages) — the "interesting real-world content"
  section. `Section page 1` (a citation-styled paragraph with its own
  distinct font-size — the "silently-dropped font size" regression case),
  `Random large table` (2 tables), `2020-03-22` (the revision-duplicate
  **spurious page** repro — a page that shouldn't semantically exist,
  documented at length in known-limitations.md; content is scattered
  table-cell fragments, not a real authored page — do not "fix" its content,
  its EXISTENCE alongside the real pages is the regression signal),
  `Image single`, **`Rename title third time`** (a page renamed multiple
  times — the real-world repro for the page-title old/new-ambiguity ⚠️ row;
  already exists, no new authoring needed for checklist item B),
  `Embedded objects` (mixed image+text+file page). One page in this section
  already produces a `failed:equation` report entry — a math equation
  already exists in a NORMAL section (Table A's "Normal: ⚠️ placeholder"
  row is already covered); only the PROTECTED-section equation row is still
  genuinely untested.
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

~258MB, notebook name `DIY`, 22 sections across 3 groups, 425 pages. Used as
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

66 sections across 6 groups, 1131 pages, synced between 2 real users over
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
Real properties confirmed to exist on this file (per `Documentation.md`):
`Author`/`AuthorOriginal`/`AuthorMostRecent` — the basis for the deferred
per-author-attribution row, not yet read by any code.

## Ground truth snapshot (informational — will drift as content is added)

Captured via `onenote_tool.convert.convert_onepkg` against the files as they
exist today, before any checklist item (A–D) content is added:

| File | page_count | sections | notable `report.failed` keys already covered |
|---|---:|---:|---|
| `Test.onepkg` | 50 | 12 | `equation`(normal only), `image_ext.emf`, `file.emf`, `file.zip/xlsx/f3d/f3z/ogg/wav/mp4/wmv/webm`, `image_missing_data`, `password_protected_section:needs_password` |
| `DIY.onepkg` | 425 | 22 | `image_ext.emf`, `file.emf`, `image_ext.xps`, `file.xps`, `image_duplicate`, `file_duplicate` |
| `Shared.onepkg` | 1131 | 66 | `image_ext.emf`, `file.emf`, `image_ext.tmp`, `file.tmp`, `password_protected_section:needs_password` — **zero `section_parse_error`** |

Re-run `python -c` against `onenote_tool.convert.convert_onepkg(path)` (or
`onenote-tool convert <path>` with the `[cli]` extra) and diff against this
table after adding checklist content, and update both this file and
`corpus_manifest.py` together.
