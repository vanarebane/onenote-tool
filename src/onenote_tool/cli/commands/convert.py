"""`onenote-tool convert` — convert a whole .onepkg notebook."""
from __future__ import annotations

import os
from typing import List, Optional

import typer

from onenote_tool.convert import convert_onepkg


def _parse_section_password(value: str) -> tuple:
    if '=' not in value:
        raise typer.BadParameter('expected NAME=PASSWORD')
    name, _, password = value.partition('=')
    return name, password


def convert(
    onepkg: str = typer.Argument(..., help='Path to the .onepkg file'),
    dump_dir: Optional[str] = typer.Option(
        None, '--dump-dir', help='Write each converted page as a flat HTML file here, for eyeballing output',
    ),
    work_dir: Optional[str] = typer.Option(
        None, '--work-dir', help='Directory to unpack the CAB into (default: a temp dir)',
    ),
    section_password: List[str] = typer.Option(
        [], '--section-password', metavar='NAME=PASSWORD',
        help='Password for a protected section, e.g. --section-password "My Section"=hunter2 '
             '(repeatable for multiple protected sections)',
    ),
) -> None:
    """Convert a whole .onepkg notebook and print a per-element report."""
    if not os.path.exists(onepkg):
        typer.echo(f'File not found: {onepkg}', err=True)
        raise typer.Exit(1)

    passwords = dict(_parse_section_password(v) for v in section_password) or None
    result = convert_onepkg(onepkg, work_dir=work_dir, section_passwords=passwords)

    if dump_dir:
        os.makedirs(dump_dir, exist_ok=True)
    _print_report(result, dump_dir)

    protected = [s for s in result.sections if s.password_status]
    if protected:
        typer.echo('\nPassword-protected sections:')
        for s in protected:
            typer.echo(f'    {s.name!r}: {s.password_status}')


def _print_report(result, dump_dir: Optional[str] = None) -> None:
    typer.echo('=' * 68)
    typer.echo(f'Notebook: {result.notebook_name}')
    typer.echo(f'Sections: {len(result.sections)}   Pages: {result.page_count}')
    typer.echo('=' * 68)

    for section in result.sections:
        typer.echo(f'\n[section] {section.name}  ({len(section.pages)} pages)')
        for page in section.pages:
            typer.echo(f'    - {page.title[:70]}   ({page.block_count} blocks)')
            if dump_dir:
                _dump_page(dump_dir, section.name, page)

    typer.echo('\n' + '-' * 68)
    typer.echo('Converted elements:')
    if result.report.converted:
        for kind, count in sorted(result.report.converted.items()):
            typer.echo(f'    {kind:32} {count}')
    else:
        typer.echo('    (none)')

    typer.echo('\nFailed / unsupported elements:')
    if result.report.failed:
        for kind, count in sorted(result.report.failed.items()):
            typer.echo(f'    {kind:32} {count}')
    else:
        typer.echo('    (none)')
    typer.echo('-' * 68)


def _safe(name: str) -> str:
    return ''.join(c if c.isalnum() or c in ' -_' else '_' for c in name).strip() or 'untitled'


def _dump_page(dump_dir: str, section_name: str, page) -> None:
    """Dev-only convenience: joins a page's neutral `blocks` into one flat
    HTML file, purely for eyeballing output. This is NOT what a real caller
    should do with `page.blocks` — see Documentation.md's "blocks format"
    section for the actual contract (positioning/layout is the caller's own
    concern, deliberately not this library's)."""
    section_dir = os.path.join(dump_dir, _safe(section_name))
    os.makedirs(section_dir, exist_ok=True)
    path = os.path.join(section_dir, _safe(page.title)[:60] + '.html')
    body = '\n'.join(b.get('html', '') for b in page.blocks)
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(body)
