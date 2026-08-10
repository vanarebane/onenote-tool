"""`onenote-tool unpack` — extract a .onepkg's .one section files."""
from __future__ import annotations

import os
import tempfile
from typing import Optional

import typer

from onenote_tool.convert import unpack_onepkg


def unpack(
    onepkg: str = typer.Argument(..., help='Path to the .onepkg file'),
    out_dir: Optional[str] = typer.Argument(
        None, help='Directory to unpack into (default: a fresh temp dir, printed on exit)',
    ),
) -> None:
    """Extract a .onepkg's .one section files and list them — useful for
    isolating one section before `inspect`."""
    if not os.path.exists(onepkg):
        typer.echo(f'File not found: {onepkg}', err=True)
        raise typer.Exit(1)
    target = out_dir or tempfile.mkdtemp(prefix='onenote_tool_unpack_')
    one_files = unpack_onepkg(onepkg, target)
    typer.echo(f'Unpacked into: {target}')
    for p in one_files:
        typer.echo(f'  {p}  ({os.path.getsize(p)} bytes)')
