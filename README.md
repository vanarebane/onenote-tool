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

## License

MIT — see [LICENSE](https://github.com/vanarebane/onenote-tool/blob/main/LICENSE).
