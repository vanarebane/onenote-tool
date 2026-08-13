"""onenote-tool CLI entry point.

Subcommands live in `cli/commands/` and are registered here — adding a new
one (e.g. a future `search`, `export`-to-.onepkg, or OneNote-online-API
command family) is one new module plus one registration line below.
"""
from __future__ import annotations

import sys

import typer

from .commands import convert as _convert
from .commands import doctor as _doctor
from .commands import inspect as _inspect
from .commands import unpack as _unpack

# Real bug, found converting a real notebook on Windows: a page title (or
# any other printed text) containing a character outside the console's
# active codepage (cp1252 by default on Windows, not UTF-8) crashes the
# WHOLE conversion with UnicodeEncodeError from deep inside typer/click's
# own echo — after the actual parse work already succeeded, losing the
# report for no parsing-related reason. Reconfigure std streams to UTF-8
# with 'replace' (never raise, worst case an unencodable character prints
# as U+FFFD instead of crashing) — this must run at IMPORT time, not only
# under `if __name__ == '__main__'`, since the installed `onenote-tool`
# console script calls `app` directly without going through that guard.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, ValueError):
        pass  # e.g. a stream that isn't a real TextIOWrapper (rare, harmless to skip)

app = typer.Typer(
    name='onenote-tool',
    help='Parse and convert Microsoft OneNote .onepkg/.one files.',
    no_args_is_help=True,
)

app.command('convert')(_convert.convert)
app.command('unpack')(_unpack.unpack)
app.command('inspect')(_inspect.inspect_one)
app.command('doctor')(_doctor.doctor)


if __name__ == '__main__':
    app()
