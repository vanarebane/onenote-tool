# onenote-tool

A Python library and CLI for parsing and converting Microsoft OneNote
`.onepkg` (notebook export) and `.one` (section) files — including
best-effort content recovery for password-protected sections, which
Microsoft's own tooling doesn't expose.

This is **not** an official Microsoft project, and it is **lossy by
design**: OneNote's binary format ([MS-ONESTORE]) is large, partly
undocumented, and this tool reads what it can, reports what it can't, and
never claims perfect fidelity. See [Documentation.md](https://github.com/vanarebane/onenote-tool/blob/main/Documentation.md) for
exactly what's currently supported.

Scope note: a `.onepkg` export never contains recycle-bin content — a
deleted page or a whole deleted section leaves no trace in the file at all,
confirmed directly (not a decode gap this tool could close). Nothing here
recovers deleted OneNote content; it only converts what's actually in the
export you give it.

## What it's for

- Pulling text, images, audio/video, tables, hyperlinks, ink/drawings, and
  embedded files out of a `.onepkg` export into a neutral, semantic
  HTML-per-block format your own code can render however it likes — with an
  optional, additive floating-box layout view (`page.regions`) for an
  application that wants to reproduce OneNote's own free-form canvas.
- Recovering as much as possible from a **password-protected** section,
  given the password — OneNote's own export/print tools generally refuse
  to touch protected content at all.
- A quick command-line tool for poking at a notebook's structure while
  developing against it (`onenote-tool inspect`), without writing a script
  each time.

It is explicitly **not** trying to be a pixel-perfect OneNote renderer —
the output is deliberately generic (see "The `blocks` format" in
[Documentation.md](https://github.com/vanarebane/onenote-tool/blob/main/Documentation.md)) so that a consuming application does
its own layout/styling, rather than this library making that decision for
everyone.

## Capability overview

A quick-glance summary of [Documentation.md](https://github.com/vanarebane/onenote-tool/blob/main/Documentation.md)'s full content-type
table (Table A) — the percentage of tracked content types that are fully
supported (✅), partially/conditionally supported (⚠️), or not supported
(❌), split by whether a section is password-protected. These are a share
of *content types this project tracks*, weighted one row = one type
regardless of how common that type is in a real notebook — not a
measurement against any particular notebook's actual content, and not a
promise that a "✅" type converts perfectly on every file. See Table A
itself for what each row actually means and what's been verified.

| Software used, date tested | Normal, unprotected | Password-protected |
|---|:---:|:---:|
| OneNote (Copilot-integrated Microsoft 365 build), August 2026 | ✅ ~84% supported | ✅ ~23% supported |
| | ⚠️ ~6% partial | ⚠️ ~45% partial |
| | ❌ ~10% not supported | ❌ ~32% not supported |

## Suggested usage

This tool was originally written to solve one specific, personal problem —
getting a real OneNote notebook's content out, once, including a
password-protected section Microsoft's own tools wouldn't touch — not to
become a general-purpose, guaranteed-fidelity migration product. It's
shared as-is, in an actively-tested state, on the chance it's useful to
someone else in the same situation.

Before relying on it for anything you can't easily double-check by hand:

- **Cross-check against OneNote's own export.** Export the same notebook
  (or the sections you care about) to `.mht` ("Single File Web Page") from
  OneNote itself, and compare it against this tool's output before trusting
  a real, serious import. This is exactly how this project verifies its own
  work (see `docs/knowledge/`) — a `.mht` comparison catches real content
  differences no amount of code review will.
- **Keep your original notebooks.** Don't delete or stop paying for the
  source OneNote notebook once you've converted it. If a gap in this tool's
  conversion is found later — including one nobody's found yet — the
  original is the only way to recover what was missed.
- **No liability, no fidelity guarantee.** This project has been tested
  against a handful of real notebooks (see [Documentation.md](https://github.com/vanarebane/onenote-tool/blob/main/Documentation.md)'s
  progress tables for specifics) — not a representative sample of every way
  OneNote has ever been used. It is provided **as-is, with no warranty of
  any kind**; the author is not liable for any data loss, corruption, or
  other damages arising from its use. There is no goal, implicit or
  explicit, of ever reaching 100% conversion fidelity — only of converting
  what can be verified correct, and being honest in `ElementReport.failed`
  and this documentation about what can't.

## Known limitations

The full, unvarnished list lives in
[docs/knowledge/known-limitations.md](https://github.com/vanarebane/onenote-tool/blob/main/docs/knowledge/known-limitations.md)
and every specific content type's status is in
[Documentation.md](https://github.com/vanarebane/onenote-tool/blob/main/Documentation.md)'s progress tables — this is the short version:

- **Password-protected sections don't convert reliably.** Best-effort
  recovery exists (see "What it's for" above), but it's a real ceiling, not
  a bug waiting to be fixed — see the capability table above. If you have
  the notebook open and the password, the most reliable path is removing
  the password (on the notebook, section, or individual pages) in OneNote
  itself before exporting.
- **Deleted content is gone before this tool ever sees it.** A `.onepkg`
  export never contains recycle-bin content — a deleted page or section
  leaves no trace in the file at all. The same applies to most fine-grained
  edit history: who changed what and when, beyond a page's own
  last-modified stamp, generally isn't preserved in a way this tool can
  read or reconstruct from the export.
- **Long-lived, many-year notebooks are undertested.** Test data so far
  comes from notebooks that are real but comparatively recent; a notebook
  that's accumulated content across many years and several OneNote format
  revisions may exercise structure this tool hasn't seen yet.
- **Tested mostly against English-language OneNote.** The notebooks used to
  verify this tool have been in English (with some non-English page
  *content*, e.g. Estonian). Issues specific to a non-English OneNote
  *application* (UI language, locale-specific stamp formats, etc.) aren't
  known one way or the other.
- **Text encoding**: OneNote's own file format stores text as UTF-16LE
  internally, which this tool decodes directly — not a matter of "UTF-8 vs.
  something else" at that layer. What's actually been diversity-tested is
  Latin-script content with European diacritics (Estonian ö/ü/õ/š,
  confirmed correct end-to-end). Non-Latin scripts (Cyrillic, CJK, Arabic,
  Hebrew, etc.) and right-to-left text specifically haven't been tested and
  could surface real issues — this tool's text-handling code makes no
  special allowance for bidi/RTL layout.

## Install

```bash
pip install onenote-tool          # library only
pip install onenote-tool[cli]     # + the `onenote-tool` command-line tool
```

Published on [PyPI](https://pypi.org/project/onenote-tool/). See
[docs/knowledge/known-limitations.md](https://github.com/vanarebane/onenote-tool/blob/main/docs/knowledge/known-limitations.md)
for current status. To install from a local clone instead: `pip install -e ".[cli,dev]"`.

**Platform note:** unpacking a `.onepkg` (a CAB archive) uses Windows'
`expand.exe` when present, otherwise falls back to the `cabextract` CLI
(install via `apt`/`brew`/`dnf`) on Linux/macOS. Run `onenote-tool doctor`
to check which one it found. See
[docs/knowledge/known-limitations.md](https://github.com/vanarebane/onenote-tool/blob/main/docs/knowledge/known-limitations.md).

## Quick start — CLI

```bash
onenote-tool convert MyNotebook.onepkg --dump-dir ./out
onenote-tool convert MyNotebook.onepkg --section-password "My Section"=hunter2
onenote-tool inspect SomeSection.one --password hunter2
onenote-tool doctor
```

## Quick start — Python API

```python
from onenote_tool import convert_onepkg

result = convert_onepkg("MyNotebook.onepkg", section_passwords={"My Section": "hunter2"})

print(result.notebook_name, result.page_count)
for section in result.sections:
    print(section.name, section.password_status)
    for page in section.pages:
        print(" ", page.title, [b["kind"] for b in page.blocks])
```

See [Documentation.md](https://github.com/vanarebane/onenote-tool/blob/main/Documentation.md) for the full API/CLI reference,
the `blocks` format contract, how passwords are handled, and the
progress-reporting flow for a caller that wants to show live conversion
progress (e.g. a web backend importing a large notebook).

## Learning how this works

If you're picking up development on this project, start with
[docs/knowledge/](https://github.com/vanarebane/onenote-tool/tree/main/docs/knowledge/) — it has everything that was reverse
engineered to make this possible (the file format, the encryption scheme,
and a chronological log of what was tried and what actually worked), so you
don't have to re-derive it.

## Privacy policy

- This software does not process or send your notebook data to any
  third-party service. It's a local library: it reads the `.onepkg`/`.one`
  files you give it and returns data structures in the same process — it
  makes no network calls of its own and has no telemetry.
- If you build something on top of this library that *does* talk to a
  third-party provider (cloud storage, another app's import API, an AI
  service, etc.), that integration is your own code, not this library —
  know what that provider does with the data you send it and where it's
  stored before you wire it up.
- The developer is not liable for any data loss, data corruption, or data
  leak arising from the use of this library. It's written first and
  foremost to be run in your own environment (your own machine or your own
  server) and, again, does not itself try to communicate with any other
  service.
- **If you're posting an issue or a commit to this repository**: anything
  that looks like real, sensitive personal data (from a real notebook,
  screenshot, or file) will be removed on sight, without further
  discussion. If you're contributing a reproduction case, obfuscate it or
  build synthetic sample data with no real personal content — don't paste
  real notebook content into an issue or a commit. If some sensitive data
  turns out not to be fully removable once posted (e.g. it's already been
  indexed or cached elsewhere), responsibility for that rests solely with
  whoever posted it. Double-check and sanitize before you post. You've been
  warned.

## License

MIT — see [LICENSE](https://github.com/vanarebane/onenote-tool/blob/main/LICENSE).
