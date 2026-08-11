"""onenote-tool: parse and convert Microsoft OneNote .onepkg/.one files.

Quick start::

    from onenote_tool import convert_onepkg

    result = convert_onepkg("MyNotebook.onepkg")
    for section in result.sections:
        for page in section.pages:
            print(page.title, page.block_count)

See Documentation.md (in the repo root) for the full API/CLI reference, the
`blocks` format contract, password handling, and the progress-flow contract.
See docs/knowledge/ for how the file format and encryption were reverse
engineered, and what's been tried.
"""
from __future__ import annotations

from ._patches import PYONENOTE_AVAILABLE, PYONENOTE_IMPORT_ERROR, PyOneNoteUnavailable
from .convert import convert_onepkg, process_sections, unpack_onepkg
from .models import ConversionResult, ElementReport, PageFile, PageImage, PageRegion, PageResult, SectionResult
from .parser import parse_one_file

__version__ = '0.1.3'

__all__ = [
    '__version__',
    'PYONENOTE_AVAILABLE',
    'PYONENOTE_IMPORT_ERROR',
    'PyOneNoteUnavailable',
    'convert_onepkg',
    'process_sections',
    'unpack_onepkg',
    'parse_one_file',
    'ConversionResult',
    'ElementReport',
    'PageFile',
    'PageImage',
    'PageRegion',
    'PageResult',
    'SectionResult',
]
