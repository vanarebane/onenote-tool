from onenote_tool.parser import _page_style, _run_is_equation


class _FakePrid:
    def __init__(self, value):
        self.value = value


class _FakeBody:
    """Stands in for pyOneNote's real PropertySet body — see the same fake
    in test_rendering.py for why (`_page_style` reads raw, unnamed
    PropertyIDs off `prop['body']`, same escape hatch as the merge-span/
    table-column-widths properties there)."""

    def __init__(self, pairs):
        self.rgPrids = [_FakePrid(k) for k, _ in pairs]
        self.rgData = [v for _, v in pairs]


def test_page_style_reads_color_and_rule_lines():
    prop = {'body': _FakeBody([
        (0x14001D2A, b'\xf0\xf8\xfa\x00'),
        (0x0C00352D, b'\x01'),
    ])}
    assert _page_style(prop) == ('#f0f8fa', 'narrow_lines')


def test_page_style_all_eight_rule_line_patterns():
    # Verified against 13 real pages, one per built-in pattern — the raw
    # enum matched each page's own title exactly.
    names = ['narrow_lines', 'college_ruled', 'standard_rules', 'wide_ruled',
             'small_grid', 'medium_grid', 'large_grid', 'very_large_grid']
    for i, name in enumerate(names, start=1):
        prop = {'body': _FakeBody([(0x0C00352D, bytes([i]))])}
        assert _page_style(prop) == (None, name)


def test_page_style_absent_properties_is_none_none():
    assert _page_style({'body': _FakeBody([])}) == (None, None)
    assert _page_style(None) == (None, None)
    assert _page_style({}) == (None, None)


def test_run_is_equation_true_when_math_formatting_flag_set():
    guid = '11111111-1111-1111-1111-111111111111'
    val = {'TextRunFormatting': [f'<ExtendedGUID> ({guid}, 2)']}
    by_id = {f'{guid},2': {'val': {'MathFormatting': 'True', 'Font': 'Cambria Math\x00'}}}
    assert _run_is_equation(val, by_id) is True


def test_run_is_equation_false_for_ordinary_formatting():
    guid = '22222222-2222-2222-2222-222222222222'
    val = {'TextRunFormatting': [f'<ExtendedGUID> ({guid}, 2)']}
    by_id = {f'{guid},2': {'val': {'Bold': 'True'}}}
    assert _run_is_equation(val, by_id) is False


def test_run_is_equation_false_with_no_formatting_ref():
    assert _run_is_equation({}, {}) is False
