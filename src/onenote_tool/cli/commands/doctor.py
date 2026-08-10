"""`onenote-tool doctor` — environment diagnostic.

The first thing to run when something isn't working: confirms pyOneNote
imported/patched successfully and reports key dependency versions.
"""
from __future__ import annotations

import importlib.metadata
import sys

import typer

import onenote_tool


def doctor() -> None:
    """Print environment/dependency diagnostics."""
    typer.echo(f'onenote-tool version: {onenote_tool.__version__}')
    typer.echo(f'Python: {sys.version.split()[0]} ({sys.platform})')
    typer.echo(f'pyOneNote available: {onenote_tool.PYONENOTE_AVAILABLE}')
    if not onenote_tool.PYONENOTE_AVAILABLE:
        typer.echo(f'  import error: {onenote_tool.PYONENOTE_IMPORT_ERROR}')

    for pkg in ('pyOneNote', 'Pillow', 'cryptography'):
        try:
            typer.echo(f'{pkg}: {importlib.metadata.version(pkg)}')
        except importlib.metadata.PackageNotFoundError:
            typer.echo(f'{pkg}: (not installed)')

    if sys.platform != 'win32':
        typer.echo('\nNote: .onepkg unpacking currently requires Windows (expand.exe).')
        typer.echo('See docs/knowledge/known-limitations.md.')
