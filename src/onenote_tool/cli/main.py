"""onenote-tool CLI entry point.

Subcommands live in `cli/commands/` and are registered here — adding a new
one (e.g. a future `search`, `export`-to-.onepkg, or OneNote-online-API
command family) is one new module plus one registration line below.
"""
from __future__ import annotations

import typer

from .commands import convert as _convert
from .commands import doctor as _doctor
from .commands import inspect as _inspect
from .commands import unpack as _unpack

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
