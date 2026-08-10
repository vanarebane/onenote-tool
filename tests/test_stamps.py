from datetime import datetime, time

from onenote_tool.stamps import _is_stamp, classify_stamp


def test_english_date_with_time():
    kind, value = classify_stamp('19. juuli 2014 10:36')
    assert kind == 'datetime'
    assert value == datetime(2014, 7, 19, 10, 36)


def test_estonian_weekday_prefix():
    kind, value = classify_stamp('esmaspäev, 11. juuni 2018 11:03')
    assert kind == 'datetime'
    assert value == datetime(2018, 6, 11, 11, 3)


def test_month_name_day_comma_year():
    kind, value = classify_stamp('April 08, 2025')
    assert kind == 'datetime'
    assert value == datetime(2025, 4, 8, 0, 0)


def test_iso_date():
    kind, value = classify_stamp('2025-04-08')
    assert kind == 'datetime'
    assert value == datetime(2025, 4, 8)


def test_bare_time():
    kind, value = classify_stamp('08:01')
    assert kind == 'time'
    assert value == time(8, 1)


def test_real_content_is_not_a_stamp():
    kind, value = classify_stamp('This is a real paragraph of text.')
    assert kind is None
    assert value is None


def test_empty_text_is_not_a_stamp():
    assert classify_stamp('') == (None, None)
    assert classify_stamp(None) == (None, None)


def test_is_stamp_helper():
    assert _is_stamp('19. juuli 2014 10:36') is True
    assert _is_stamp('Meeting notes') is False
