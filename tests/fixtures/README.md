# Test fixtures

This repository deliberately does **not** bundle any real OneNote export.
A `.onepkg`/`.one` file is, by nature, someone's actual notebook — it can
contain photos, names, addresses, and (for a password-protected section) a
real password. None of that belongs in a public git history.

If you're developing this package locally and want the real-file tests to
run (`tests/test_smoke_local_file.py`), point the test suite at your own
file via environment variables — nothing here needs to live inside the repo
or this `fixtures/` directory (it's gitignored anyway, see the root
`.gitignore`):

```bash
export ONENOTE_TOOL_TEST_ONEPKG="/path/to/your/Notebook.onepkg"
export ONENOTE_TOOL_TEST_ONE_FILE="/path/to/a/single/Section.one"
pytest
```

Without these set, the real-file tests are **skipped**, not failed — a
fresh clone or CI run is always green.

## Making your own `.onepkg` for testing

If you don't have a real export handy, OneNote (desktop) can export any
notebook via **File → Export → Notebook → OneNote Package (.onepkg)**. For
testing password-protected recovery specifically, protect a section first
(**Review → Password**) before exporting, and set
`ONENOTE_TOOL_TEST_ONEPKG` together with whatever password fixture your own
test invocation supplies (the `inspect`/`convert` CLI commands both take a
`--password`/`--section-password` flag).
