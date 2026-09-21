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

## Content-correctness corpus (`tests/test_corpus_content.py`)

`test_smoke_local_file.py` above only checks generic *shape* (blocks are
well-formed) against one file. `test_corpus_content.py` checks real, named
*content* against a whole folder of real notebooks — see
[CORPUS_MANIFEST.md](CORPUS_MANIFEST.md) for what each file covers and
[corpus_manifest.py](corpus_manifest.py) for the machine-readable version:

```bash
export ONENOTE_TOOL_TEST_CORPUS_DIR="/path/to/a/folder/of/.onepkg/files"
# only needed for protected-section entries in the manifest:
export ONENOTE_TOOL_TEST_PW_TEST_PROTECTED="..."
pytest tests/test_corpus_content.py -v
```

Same discipline as above: unset → every test in this file skips, never
fails. A partial corpus (some manifest files missing) still runs whatever
it has. **Run this before tagging/publishing a release** — it's the closest
thing this project has to a real correctness gate, and directly the kind of
check that would have caught 0.1.0's page-dropping regression before it
reached PyPI (see `CHANGELOG.md`'s `[0.1.1]` entry).

Passwords are never literals in the manifest — each protected section maps
to an environment-variable *name*, resolved at test time, so nothing secret
is ever committed.

## Making your own `.onepkg` for testing

If you don't have a real export handy, OneNote (desktop) can export any
notebook via **File → Export → Notebook → OneNote Package (.onepkg)**. For
testing password-protected recovery specifically, protect a section first
(**Review → Password**) before exporting, and set
`ONENOTE_TOOL_TEST_ONEPKG` together with whatever password fixture your own
test invocation supplies (the `inspect`/`convert` CLI commands both take a
`--password`/`--section-password` flag).
