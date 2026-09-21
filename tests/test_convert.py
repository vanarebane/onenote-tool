from onenote_tool.convert import _fix_comma_corruption, _fix_extracted_name, _fix_mojibake


def test_fix_mojibake_round_trips_double_encoded_name():
    assert _fix_mojibake('HommikusÃ¶Ã¶giks') == 'Hommikusöögiks'


def test_fix_mojibake_leaves_genuine_unicode_alone():
    assert _fix_mojibake('Meie Maja™') == 'Meie Maja™'


def test_fix_mojibake_leaves_ascii_alone():
    assert _fix_mojibake('Plain Name') == 'Plain Name'


def test_fix_comma_corruption_replaces_caret_j():
    # Real bug, confirmed twice independently on real notebooks (hex-verified:
    # the source .onepkg's own onetoc2 bytes already read '^J', not a comma —
    # not an extraction-tool artifact): a literal comma in a section/group
    # name comes back as the literal two-character sequence "^J".
    assert _fix_comma_corruption('Skills^J Tools') == 'Skills, Tools'
    assert _fix_comma_corruption('Coma^J Test') == 'Coma, Test'


def test_fix_comma_corruption_leaves_other_punctuation_alone():
    # Verified against real test data covering every other ordinary
    # punctuation mark — none of them corrupt, only comma does.
    for name in ('Semi; Colon', 'Period. Test', 'Exclaim! Test',
                 "Apostrophe ' Test", 'Hyphen - Test', 'Equal = test', 'Bracket (test)'):
        assert _fix_comma_corruption(name) == name


def test_fix_extracted_name_composes_both_fixes():
    assert _fix_extracted_name('HommikusÃ¶Ã¶giks') == 'Hommikusöögiks'
    assert _fix_extracted_name('Skills^J Tools') == 'Skills, Tools'
    assert _fix_extracted_name('Plain Name') == 'Plain Name'
