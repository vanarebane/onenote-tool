"""`onenote-tool inspect` — parse a single .one file directly.

Skips CAB unpacking entirely — the fast iteration loop for testing one
section's recovery without re-unpacking a whole notebook each time.
"""
from __future__ import annotations

import os
from typing import Optional

import typer

from onenote_tool.models import ElementReport
from onenote_tool.parser import parse_one_file


def inspect_one(
    one_file: str = typer.Argument(..., help='Path to a single .one section file'),
    password: Optional[str] = typer.Option(None, '--password', help='Password, if the section is protected'),
) -> None:
    """Parse ONE .one file directly and print its recovered pages/report."""
    if not os.path.exists(one_file):
        typer.echo(f'File not found: {one_file}', err=True)
        raise typer.Exit(1)

    report = ElementReport()
    passwords = [password] if password else None
    pages, password_status = parse_one_file(one_file, report, passwords=passwords)

    typer.echo(f'Password status: {password_status or "(not protected)"}')
    typer.echo(f'Pages: {len(pages)}')
    for page in pages:
        kinds = ', '.join(b.get('kind', '?') for b in page.blocks)
        typer.echo(
            f'  - {page.title[:70]!r}   blocks={page.block_count} [{kinds}]  '
            f'images={len(page.images)} files={len(page.files)}'
        )

    typer.echo('\nConverted:')
    for kind, count in sorted(report.converted.items()):
        typer.echo(f'    {kind:32} {count}')
    typer.echo('Failed/unsupported:')
    for kind, count in sorted(report.failed.items()):
        typer.echo(f'    {kind:32} {count}')
