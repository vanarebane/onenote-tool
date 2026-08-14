"""Unpack a `.onepkg` and convert every section it contains.

The orchestration layer above `parser.parse_one_file`: CAB extraction,
per-section iteration, progress reporting, and the resumable
(`stop_at_first_unresolved`) contract a multi-request caller (e.g. a web
backend prompting for passwords one at a time) needs.
"""
from __future__ import annotations

import logging
import os
import shutil
import struct
import subprocess
import tempfile
from typing import Optional

from ._patches import require_pyonenote
from .models import ConversionResult, PageResult, SectionResult
from .parser import parse_one_file
from .rendering import _placeholder_blocks

log = logging.getLogger(__name__)


def _fix_mojibake(name: str) -> str:
    """Undo a real, verified CAB-extraction bug: a non-ASCII section/group
    name (Estonian ö/ü, etc.) can come back from `expand.exe`/`cabextract`
    double-encoded — its real UTF-8 bytes decoded as Latin-1 first, then
    re-encoded as UTF-8 — the classic "Ã¶" for "ö" mojibake shape.
    Confirmed on a real notebook: "Hommikusöögiks" extracted as
    "HommikusÃ¶Ã¶giks". `expand.exe`/`cabextract` are external tools this
    package doesn't control, so fix the DISPLAY name after the fact rather
    than the file on disk.

    Round-trip through Latin-1 and back is the correction (encode as
    Latin-1 to recover the original raw bytes, decode as UTF-8 to get the
    real text) and doubles as its own safety check: it only succeeds for a
    string that's ACTUALLY this exact mojibake shape. A name with no
    non-ASCII characters round-trips back to itself unchanged (harmless
    no-op). A name with genuine, correctly-encoded Unicode (e.g. "Meie
    Maja™") fails at the very first step — Latin-1 can't represent U+2122
    at all — so it's left alone rather than mangled further; same for any
    other already-correct non-ASCII name outside Latin-1's range.
    """
    try:
        return name.encode('latin-1').decode('utf-8')
    except (UnicodeEncodeError, UnicodeDecodeError):
        return name


def _find_expand_exe() -> Optional[str]:
    candidate = os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'System32', 'expand.exe')
    if os.path.exists(candidate):
        return candidate
    # Deliberately `expand.exe` only, never bare `expand` — GNU coreutils ships
    # its OWN, completely unrelated `expand` (tabs -> spaces) at /usr/bin/expand
    # on every Linux box, and `shutil.which('expand')` found and ran THAT one
    # instead of failing over to cabextract (confirmed on a real deploy: it ran,
    # rejected the CAB-extraction flags, and exited 1). Cygwin/WSL setups that
    # expose Windows' PATH still resolve the real one via the `.exe` name.
    return shutil.which('expand.exe')


def _expand_onepkg(onepkg_path: str, out_dir: str) -> None:
    """Unpack a .onepkg CAB archive into out_dir.

    Prefers Windows' built-in `expand.exe` when it's present (also covers
    Cygwin/WSL setups that expose it on PATH), and falls back to the
    cross-platform `cabextract` CLI (wraps libmspack; `apt`/`brew`/`dnf`
    package `cabextract`) so Linux/macOS work without any Windows tooling.
    Both extract the CAB preserving its internal directory structure, which
    `unpack_onepkg` relies on for OneNote section-group folders.
    """
    expand_exe = _find_expand_exe()
    if expand_exe:
        subprocess.run([expand_exe, '-F:*', onepkg_path, out_dir], check=True, capture_output=True)
        return

    cabextract = shutil.which('cabextract')
    if cabextract:
        subprocess.run([cabextract, '-q', '-d', out_dir, onepkg_path], check=True, capture_output=True)
        return

    raise RuntimeError(
        '.onepkg unpacking needs either Windows expand.exe or the cabextract CLI '
        '(neither found on PATH). Install cabextract — e.g. "apt install cabextract", '
        '"brew install cabextract", "dnf install cabextract" — and retry.'
    )


def _toc_order(dir_path: str) -> dict:
    """Parse `dir_path`'s own `Open Notebook.onetoc2` — OneNote's own
    table-of-contents file, one per folder level, present alongside the
    `.one` files it lists — into `{entry_name: order_index}` (1-based; a
    section's own name INCLUDES the `.one` extension, a section GROUP's
    doesn't — matching the extracted filenames/directory names exactly, so
    the caller can look either up directly). Empty dict if the file's
    absent or nothing in it matches the expected shape — the caller falls
    back to alphabetical for anything not found here, same as before this
    existed.

    pyOneNote can open a `.onetoc2` (same MS-ONESTORE/FSSHTTPB container as
    a `.one` file) but not read anything OUT of it — every property inside
    decodes as "Unknown": `.onetoc2` uses `ObjectDeclarationWithRefCountFNDX`
    (the older, non-"2" declaration node type) for each entry, which
    pyOneNote has no data-shape class for at all (confirmed: present in its
    own `_FileNodeIDs` name table, but no corresponding parsing class
    anywhere in the library — genuinely never implemented, not a bug this
    project could patch the same way the `.one`-file gaps elsewhere in
    `_patches.py` are).

    Reverse-engineered directly from real notebooks instead (a real user's,
    cross-checked against OneNote's own sidebar screenshot) rather than
    implementing that missing structural parser from the MS-ONESTORE spec
    from scratch — lower-risk than hand-writing a new binary format reader
    against imperfect spec recall, given this project's standing rule
    against landing unverified structural changes. Every entry has the
    same real, verified shape: a 16-byte GUID, then a 4-byte little-endian
    order index, then a 4-byte little-endian byte-length (including the
    trailing UTF-16 null terminator), then the entry's own name as
    null-terminated UTF-16LE. Verified byte-for-byte against two real,
    unrelated notebooks: on one, all 9 top-level individual sections'
    relative order matched a real screenshot of that notebook's own sidebar
    exactly (including a section named "Skills, Tools", whose extracted
    name is itself corrupted — see `known-limitations.md` — matched by its
    corrupted name, since that's what's actually on disk to line up
    against); on the other, all 18 decodable entries had no counter-example
    to that same shape. Recursed one level deeper (a sub-folder's own
    `Open Notebook.onetoc2`) and confirmed the same shape orders that
    folder's own 5 sections identically to the same real screenshot too.

    The byte-length field doubles as this function's own correctness check:
    a candidate is only accepted when it exactly matches the decoded
    string's real encoded size — a real entry always satisfies this, so
    rejecting anything that doesn't is a free, cheap way to avoid a false
    positive from scanning at literally every byte offset (see below) —
    verified zero false positives on both real files checked.
    """
    path = os.path.join(dir_path, 'Open Notebook.onetoc2')
    order = {}
    try:
        with open(path, 'rb') as f:
            data = f.read()
    except OSError:
        return order
    # Deliberately a brute-force, every-offset scan, not a structural walk
    # (this file's own outer FileNodeList *does* parse — it's specifically
    # each entry's un-dispatched inner body that's opaque, see above) — a
    # `.onetoc2` is a few KB at most, so the cost is negligible, and the
    # byte-length cross-check above makes this reliable despite not being
    # position-aware.
    i = 0
    n = len(data)
    while i + 24 <= n:
        idx, strlen = struct.unpack_from('<II', data, i + 16)
        if 4 <= strlen <= 512 and i + 24 + strlen <= n:
            raw = data[i + 24:i + 24 + strlen]
            if raw[-2:] == b'\x00\x00':
                try:
                    name = raw[:-2].decode('utf-16-le')
                except UnicodeDecodeError:
                    name = ''
                if name and name.isprintable():
                    order.setdefault(name, idx)
                    # `expand.exe`/`cabextract` mojibake the on-disk filename for
                    # any non-ASCII entry (see `_fix_mojibake`) but this file's
                    # own name field decodes CORRECTLY — so a name like "Meie
                    # näitus" never matches the on-disk "Meie nÃ¤itus.one" and
                    # silently falls to the 10**9 alphabetical-last fallback
                    # (confirmed on a real notebook). Also index under the same
                    # forward-mojibake'd form so the later on-disk lookup hits.
                    try:
                        mojibake = name.encode('utf-8').decode('latin-1')
                    except (UnicodeEncodeError, UnicodeDecodeError):
                        mojibake = name
                    if mojibake != name:
                        order.setdefault(mojibake, idx)
        i += 1
    return order


def unpack_onepkg(onepkg_path: str, work_dir: str) -> list:
    """Unpack a .onepkg into `work_dir` (created if missing) and return the
    `.one` file paths found inside, in OneNote's own real notebook order
    (see `_toc_order`) — falling back to alphabetical for anything that
    order couldn't be recovered for (a directory with no `Open Notebook
    .onetoc2` of its own, or an entry it doesn't mention). Pass a
    caller-owned, PERSISTENT `work_dir` (not auto-cleaned between calls)
    when the caller needs to resume processing across multiple calls — see
    `process_sections`."""
    os.makedirs(work_dir, exist_ok=True)
    _expand_onepkg(onepkg_path, work_dir)

    def walk(dir_path):
        try:
            entries = os.listdir(dir_path)
        except OSError:
            return []
        order = _toc_order(dir_path)
        files, dirs = [], []
        for name in entries:
            full = os.path.join(dir_path, name)
            if name.lower().endswith('.one') and os.path.isfile(full):
                files.append(name)
            elif os.path.isdir(full):
                dirs.append(name)
        # Sections before section-group folders, matching OneNote's own
        # sidebar (verified against a real screenshot: two folders sorted
        # AFTER every top-level section regardless of their own relative
        # order to those sections) — within each of the two groups, by the
        # real TOC index when known, else alphabetically (the previous,
        # only-available behavior) as a stable tiebreak/fallback.
        files.sort(key=lambda name: (order.get(name, 10 ** 9), name.lower()))
        dirs.sort(key=lambda name: (order.get(name, 10 ** 9), name.lower()))
        out = [os.path.join(dir_path, name) for name in files]
        for name in dirs:
            out.extend(walk(os.path.join(dir_path, name)))
        return out

    return walk(work_dir)


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
        section_name = _fix_mojibake(os.path.splitext(os.path.basename(one_path))[0])
        # Subfolders in the .onepkg are OneNote section groups; the relative
        # directory path from the notebook root is the group hierarchy.
        rel_dir = os.path.relpath(os.path.dirname(one_path), work_dir)
        group_path = [] if rel_dir in ('.', '') else [_fix_mojibake(p) for p in rel_dir.split(os.sep)]
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
                    {'name': _fix_mojibake(os.path.splitext(os.path.basename(p))[0]), 'size': sizes[p]}
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
