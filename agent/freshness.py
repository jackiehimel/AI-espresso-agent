"""Shared date-inference helpers for story freshness checks.

Dependency-free so both espresso_agent.py and espresso_loop.py can import it
without circular-import concerns.
"""

from __future__ import annotations

import datetime as dt
import re

_URL_DATE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"/(20\d{2})/(0[1-9]|1[0-2])/([0-2]\d|3[01])(?:/|$)"),
    re.compile(r"/(20\d{2})-(0[1-9]|1[0-2])-([0-2]\d|3[01])(?:/|$)"),
    re.compile(r"/(0[1-9]|1[0-2])-([0-2]\d|3[01])-(\d{2})(?:/|$)"),
)

_TEXT_DATE_RE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+"
    r"([0-2]?\d|3[01]),\s*(20\d{2})\b",
    re.IGNORECASE,
)

_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


def infer_date_from_url(url: str) -> dt.date | None:
    for idx, pattern in enumerate(_URL_DATE_PATTERNS):
        match = pattern.search(url or "")
        if not match:
            continue
        try:
            if idx < 2:
                year, month, day = map(int, match.groups())
            else:
                month, day, yy = match.groups()
                year = 2000 + int(yy)
                month = int(month)
                day = int(day)
            return dt.date(year, month, day)
        except ValueError:
            continue
    return None


def infer_date_from_text(text: str) -> dt.date | None:
    match = _TEXT_DATE_RE.search(text or "")
    if not match:
        return None
    month_name, day_text, year_text = match.groups()
    month = _MONTHS.get(month_name.lower())
    if not month:
        return None
    try:
        return dt.date(int(year_text), month, int(day_text))
    except ValueError:
        return None
