import struct

from onenote_tool.equations import equation_mathml


class _Prid:
    def __init__(self, value):
        self.value = value


class _Body:
    def __init__(self, pairs):
        self.rgPrids = [_Prid(k) for k, _ in pairs]
        self.rgData = [v for _, v in pairs]


class _Data:
    def __init__(self, data):
        self.Data = data


def _run(obj_type=None, char=None, char2=None):
    pairs = []
    if obj_type is not None:
        pairs.append((0x1400344F, struct.pack('<I', obj_type)))
    if char is not None:
        pairs.append((0x10003453, struct.pack('<H', ord(char))))
    if char2 is not None:
        pairs.append((0x10003454, struct.pack('<H', ord(char2))))
    return _Body(pairs)


def _equation(runs):
    """runs: [(text, run_body)] -> an equation run's raw body."""
    text = ''.join(t for t, _ in runs)
    ends, pos = [], 0
    for t, _ in runs[:-1]:
        pos += len(t.encode('utf-16-le')) // 2
        ends.append(pos)
    return _Body([
        (0x1C001C22, _Data(text.encode('utf-16-le'))),
        (0x1C001E12, _Data(struct.pack(f'<{len(ends)}I', *ends))),
        (0x40003499, [b for _, b in runs]),
    ])


S, SEP, E = '﷐', '﷮', '﷯'


def test_superscript_inside_brackets():
    # (x+a)^n — same shape as the real binomial-theorem equation's left side.
    body = _equation([
        (S, _run(31, '^')), (S, _run(13, '(', ')')), ('x+a' + E, _run(13)), (SEP, _run(31)), ('n' + E, _run(31)),
    ])
    assert equation_mathml(body) == (
        '<math xmlns="http://www.w3.org/1998/Math/MathML"><msup><mrow><mrow><mo>(</mo><mi>x</mi><mo>+</mo>'
        '<mi>a</mi><mo>)</mo></mrow></mrow><mrow><mi>n</mi></mrow></msup></math>'
    )


def test_nary_with_limits_and_stack():
    body = _equation([
        (S, _run(21, '∑')), ('k=0' + SEP, _run(21)), ('n' + SEP, _run(21)),
        (S, _run(27, '¦')), ('n' + SEP, _run(27)), ('k' + E, _run(27)), (E, _run(21)),
    ])
    html = equation_mathml(body)
    assert '<munderover><mo>∑</mo><mrow><mi>k</mi><mo>=</mo><mn>0</mn></mrow><mrow><mi>n</mi></mrow></munderover>' in html
    assert '<mfrac linethickness="0"><mrow><mi>n</mi></mrow><mrow><mi>k</mi></mrow></mfrac>' in html


def test_unverified_object_type_gives_none():
    body = _equation([(S, _run(16)), ('1' + SEP, _run(16)), ('2' + E, _run(16))])  # fraction: not verified
    assert equation_mathml(body) is None


def test_unbalanced_structure_gives_none():
    body = _equation([(S, _run(31)), ('x' + SEP + 'n', _run(31))])
    assert equation_mathml(body) is None


def test_missing_run_data_gives_none():
    assert equation_mathml(_Body([])) is None
