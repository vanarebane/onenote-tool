"""Unpack a `.onepkg` and convert every section it contains.

The orchestration layer above `parser.parse_one_file`: CAB extraction,
per-section iteration, progress reporting, and the resumable
(`stop_at_first_unresolved`) contract a multi-request caller (e.g. a web
backend prompting for passwords one at a time) needs.
"""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from typing import Optional

from ._patches import require_pyonenote
from .models import ConversionResult, PageResult, SectionResult
from .parser import parse_one_file
from .rendering import _placeholder_blocks

log = logging.getLogger(__name__)


def _expand_onepkg(onepkg_path: str, out_dir: str) -> None:
    """Unpack a .onepkg CAB archive into out_dir using Windows expand.exe.

    See docs/knowledge/known-limitations.md — this is currently the one
    genuinely Windows-only part of the package; a Linux/macOS caller needs
    `cabextract`/`libmspack` wired in here instead (not yet done).
    """
    expand_exe = os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'System32', 'expand.exe')
    if not os.path.exists(expand_exe):
        raise RuntimeError(
            'expand.exe not found. OneNote .onepkg unpacking currently requires '
            'Windows. On Linux install cabextract/libmspack and adapt _expand_onepkg.'
        )
    subprocess.run([expand_exe, '-F:*', onepkg_path, out_dir], check=True, capture_output=True)


def unpack_onepkg(onepkg_path: str, work_dir: str) -> list:
    """Unpack a .onepkg into `work_dir` (created if missing) and return the
    sorted list of absolute `.one` file paths found inside. Pass a
    caller-owned, PERSISTENT `work_dir` (not auto-cleaned between calls)
    when the caller needs to resume processing across multiple calls — see
    `process_sections`."""
    os.makedirs(work_dir, exist_ok=True)
    _expand_onepkg(onepkg_path, work_dir)
    one_files = []
    for root, _dirs, files in os.walk(work_dir):
        for name in files:
            if name.lower().endswith('.one'):
                one_files.append(os.path.join(root, name))
    one_files.sort()
    return one_files


def process_sections(
    one_files: list,
    result: ConversionResult,
    work_dir: str,
    section_passwords: Optional[dict] = None,
    sizes: Optional[dict] = None,
    processed_size: int = 0,
    progress_cb=None,
    stop_at_first_unresolved: bool = False,
) -> tuple:
    """Parse `one_files` in order, appending a `SectionResult` to
    `result.sections` for each — in place, so a caller can inspect partial
    progress even if this stops early (see below).

    `section_passwords` maps section name -> its OWN password (an explicit
    override), but EVERY password in it is tried against EVERY protected
    section (not just the one it's named for) — a shared password across
    sections then only needs to be typed once; see `parser.parse_one_file`.

    `progress_cb`, if given, is called `progress_cb(event, payload)` — once
    with `event='start'` and `payload={'sections': [{'name', 'size'}, ...],
    'total_size': int}` (a caller processing the WHOLE notebook in one call
    should send this itself before calling; a resumable caller sends it once
    up front and never again), then once per section actually processed
    here with `event='section'` and `payload={'name', 'size', 'status',
    'processed_size'}` — `status` is `'protected'` (password-protected,
    regardless of whether it was recovered), `'failed'`
    (`section_parse_error`), or `'ok'`. Exceptions from `progress_cb` itself
    are swallowed — a broken progress reporter must never break the import.

    Normally (`stop_at_first_unresolved=False`) every file in `one_files`
    gets processed — a password-protected section with no working password
    gets a `password_status`-tagged placeholder page and processing
    continues.

    When `stop_at_first_unresolved=True` (a resumable multi-request caller),
    processing STOPS the instant a section needs a password
    (`'needs_password'`/`'wrong_password'`) that isn't among
    `section_passwords.values()`: nothing is appended to `result.sections`
    for that section (it isn't resolved yet, so there's nothing final to
    record), and it becomes the FIRST element of the returned
    `remaining_files` so a later call can retry it once the caller has a new
    candidate password. A protected section that DOES have a working
    password, or one whose password decrypts fine but recovers no content
    (`'decrypt_failed'` — retrying a password can't fix that), is still
    fully resolved either way and never blocks.

    Returns `(remaining_files, blocked, processed_size)` — `remaining_files`
    is `[]` once everything got through; `blocked` is `None` unless stopped
    early, in which case it's `{'name', 'status'}` for the one section that
    blocked; `processed_size` is the running total (starting from the
    `processed_size` argument) for a resumable caller to carry forward.
    """
    def _report_progress(event, payload):
        if progress_cb is None:
            return
        try:
            progress_cb(event, payload)
        except Exception:
            log.exception('OneNote import progress_cb raised — ignoring')

    if sizes is None:
        sizes = {p: os.path.getsize(p) for p in one_files}
    passwords_seen = list(dict.fromkeys(p for p in (section_passwords or {}).values() if p))

    for i, one_path in enumerate(one_files):
        section_name = os.path.splitext(os.path.basename(one_path))[0]
        # Subfolders in the .onepkg are OneNote section groups; the relative
        # directory path from the notebook root is the group hierarchy.
        rel_dir = os.path.relpath(os.path.dirname(one_path), work_dir)
        group_path = [] if rel_dir in ('.', '') else rel_dir.split(os.sep)
        explicit = (section_passwords or {}).get(section_name)
        candidates = list(dict.fromkeys(([explicit] if explicit else []) + passwords_seen))

        section = SectionResult(name=section_name, group_path=group_path)
        status = 'ok'
        try:
            section.pages, section.password_status = parse_one_file(one_path, result.report, passwords=candidates)
            if section.password_status:
                if stop_at_first_unresolved and section.password_status in ('needs_password', 'wrong_password'):
                    return one_files[i:], {'name': section_name, 'status': section.password_status}, processed_size
                status = 'protected'
        except Exception as exc:  # best-effort: record the failure, keep going
            log.warning('OneNote section "%s" failed to parse (%s): %s', section_name, type(exc).__name__, exc)
            result.report.fail(f'section_parse_error:{type(exc).__name__}')
            # Leave a visible placeholder page so the section isn't a silent blank.
            section.pages = [PageResult(
                title=section_name,
                blocks=_placeholder_blocks(
                    f'the OneNote section "{section_name}"',
                    f'{type(exc).__name__}: {exc}',
                ),
                block_count=1,
            )]
            status = 'failed'
        result.sections.append(section)
        processed_size += sizes[one_path]
        _report_progress('section', {
            'name': section_name, 'size': sizes[one_path], 'status': status, 'processed_size': processed_size,
        })

    return [], None, processed_size


def convert_onepkg(
    onepkg_path: str,
    work_dir: Optional[str] = None,
    section_passwords: Optional[dict] = None,
    progress_cb=None,
) -> ConversionResult:
    """Unpack and convert a full .onepkg notebook into a ConversionResult in
    one call — see `process_sections` for `section_passwords`/`progress_cb`.

    A resumable, one-section-at-a-time caller (e.g. a web backend that needs
    to pause between HTTP requests to prompt for a password) shouldn't use
    this directly — call `unpack_onepkg` + `process_sections` itself instead,
    so it can stop and resume across requests with `stop_at_first_unresolved`.
    """
    require_pyonenote()

    notebook_name = os.path.splitext(os.path.basename(onepkg_path))[0]
    result = ConversionResult(notebook_name=notebook_name)

    tmp = work_dir or tempfile.mkdtemp(prefix='onenote_tool_')
    one_files = unpack_onepkg(onepkg_path, tmp)
    sizes = {p: os.path.getsize(p) for p in one_files}
    total_size = sum(sizes.values())

    if progress_cb is not None:
        try:
            progress_cb('start', {
                'sections': [
                    {'name': os.path.splitext(os.path.basename(p))[0], 'size': sizes[p]}
                    for p in one_files
                ],
                'total_size': total_size,
            })
        except Exception:
            log.exception('OneNote import progress_cb raised — ignoring')

    process_sections(one_files, result, tmp, section_passwords, sizes, 0, progress_cb)
    # (remaining_files, blocked, processed_size) — nothing to resume from
    # here since this call always runs the whole notebook in one go.
    return result
