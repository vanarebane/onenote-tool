"""Classify a run's text as an auto date/time stamp vs real content.

OneNote auto-adds a date+time stamp under every page title. Those runs are
not real content and must not be taken as a page title or body text; the
caller instead uses the parsed datetime as the page's `created_at`. Stamps
arrive in several locales (English + Estonian, with or without a leading
weekday), e.g. "19. juuli 2014 10:36", "esmaspäev, 11. juuni 2018 11:03",
"08 April 2025 08:01".
"""
from __future__ import annotations

import re
from datetime import datetime, time as dt_time

_MONTHS = {
    'january': 1, 'february': 2, 'march': 3, 'april': 4, 'may': 5, 'june': 6,
    'july': 7, 'august': 8, 'september': 9, 'october': 10, 'november': 11, 'december': 12,
    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'jun': 6, 'jul': 7, 'aug': 8, 'sep': 9,
    'sept': 9, 'oct': 10, 'nov': 11, 'dec': 12,
    'jaanuar': 1, 'veebruar': 2, 'märts': 3, 'aprill': 4, 'mai': 5, 'juuni': 6,
    'juuli': 7, 'august': 8, 'september': 9, 'oktoober': 10, 'november': 11, 'detsember': 12,
}
_WEEKDAYS = {
    'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday',
    'mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun',
    'esmaspäev', 'teisipäev', 'kolmapäev', 'neljapäev', 'reede', 'laupäev', 'pühapäev',
}
_TIME_RE = re.compile(r'(\d{1,2}):(\d{2})(?::\d{2})?\s*$')


def _parse_date_core(core: str):
    """core has weekday/time removed; return (year, month, day) or None."""
    core = core.strip().rstrip(',').strip()
    m = re.match(r'^(\d{1,2})\.?\s+([^\d\s.,]+)\.?\s+(\d{4})$', core)  # DD. month YYYY
    if m:
        mon = _MONTHS.get(m.group(2).lower())
        if mon:
            return int(m.group(3)), mon, int(m.group(1))
    m = re.match(r'^([^\d\s.,]+)\.?\s+(\d{1,2}),?\s+(\d{4})$', core)   # month DD, YYYY
    if m:
        mon = _MONTHS.get(m.group(1).lower())
        if mon:
            return int(m.group(3)), mon, int(m.group(2))
    m = re.match(r'^(\d{4})-(\d{1,2})-(\d{1,2})$', core)              # ISO
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))
    return None


def classify_stamp(text: str):
    """Classify a run's text.

    Returns ('datetime', datetime) for a date (optionally with time),
    ('time', datetime.time) for a bare time, or (None, None) for real content.
    OneNote often stores the page date and time as SEPARATE runs, so the caller
    combines a date-only stamp with a later time-only stamp.
    """
    s = (text or '').strip()
    if not s:
        return (None, None)
    # Strip a leading weekday token ("esmaspäev, " / "Monday, ").
    candidate = s
    wm = re.match(r'^([^\s,]+)\s*,\s*(.*)$', s)
    if wm and wm.group(1).lower() in _WEEKDAYS:
        candidate = wm.group(2).strip()
    # Split off an optional trailing time.
    hh = mm = None
    core = candidate
    tm = _TIME_RE.search(candidate)
    if tm:
        hh, mm = int(tm.group(1)), int(tm.group(2))
        core = candidate[:tm.start()].strip().rstrip(',').strip()
    if core:
        ymd = _parse_date_core(core)
        if ymd:
            try:
                return ('datetime', datetime(ymd[0], ymd[1], ymd[2], hh or 0, mm or 0))
            except ValueError:
                return (None, None)
        return (None, None)  # leftover non-date text -> real content
    if tm:
        try:
            return ('time', dt_time(hh, mm))
        except ValueError:
            return (None, None)
    return (None, None)


def _is_stamp(text: str) -> bool:
    return classify_stamp(text)[0] is not None
