"""
date_parser.py

Parses the "Human readable dates updated final" column into:
  - expression: "YYYY Month D" / "YYYY Month" / "YYYY" (single) or two of those
                joined by " - " (range)
  - begin: YYYY-MM-DD
  - end:   YYYY-MM-DD
  - date_type: "single" or "inclusive" (ArchivesSpace date_type enum values)

Design notes / assumptions (see README for the full list):
  - Excel datetime cells with day == 1 are treated as MONTH precision (the
    day is assumed to be a placeholder, not a real reported day). A
    datetime cell with day != 1 is treated as exact DAY precision.
  - Season words (Winter/Spring/Summer/Fall/Autumn) collapse to YEAR
    precision only, per instruction -- we do not guess a month for them.
  - When a range has a month/day on only one side, the missing year is
    filled in from the side that has it (e.g. "Apr - Aug 1999" ->
    April 1999 - August 1999).
  - Anything that can't be confidently parsed returns None; the caller
    is responsible for logging the row and skipping the date subrecord.
"""

import calendar
import datetime
import re
from dataclasses import dataclass
from typing import Optional


MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

MONTH_NAMES = {
    1: "January", 2: "February", 3: "March", 4: "April", 5: "May",
    6: "June", 7: "July", 8: "August", 9: "September", 10: "October",
    11: "November", 12: "December",
}

SEASONS = {"winter", "spring", "summer", "fall", "autumn"}

YEAR_RE = re.compile(r"\b(1[5-9]\d{2}|20\d{2})\b")


@dataclass
class ParsedDatePart:
    year: Optional[int] = None
    month: Optional[int] = None
    day: Optional[int] = None
    # True if the ONLY thing we found was a season word plus (maybe) a year
    season_only: bool = False

    def has_any(self) -> bool:
        return self.year is not None or self.month is not None


@dataclass
class ParsedDate:
    expression: str
    begin: str
    end: str
    date_type: str  # "single" or "inclusive"


def _expr_for_part(part: ParsedDatePart) -> str:
    if part.day:
        return f"{part.year} {MONTH_NAMES[part.month]} {part.day}"
    if part.month:
        return f"{part.year} {MONTH_NAMES[part.month]}"
    return f"{part.year}"


def _begin_for_part(part: ParsedDatePart) -> str:
    month = part.month or 1
    day = part.day or 1
    return f"{part.year:04d}-{month:02d}-{day:02d}"


def _end_for_part(part: ParsedDatePart) -> str:
    if part.day:
        month = part.month
        day = part.day
    elif part.month:
        month = part.month
        day = calendar.monthrange(part.year, part.month)[1]
    else:
        month = 12
        day = 31
    return f"{part.year:04d}-{month:02d}-{day:02d}"


def _parse_part(text: str) -> Optional[ParsedDatePart]:
    """Parse a single side of a date (no range dash in it)."""
    text = text.strip().strip(".").strip()
    if not text:
        return None

    lower = text.lower()
    year_match = YEAR_RE.search(text)
    year = int(year_match.group(0)) if year_match else None

    # Season word => year-only precision, per instruction to "just use
    # the year range" rather than guessing a month.
    words = re.findall(r"[a-zA-Z]+", lower)
    if any(w in SEASONS for w in words):
        return ParsedDatePart(year=year, month=None, day=None, season_only=True)

    month = None
    for w in words:
        if w in MONTHS:
            month = MONTHS[w]
            break

    if year is None and month is None:
        return None

    return ParsedDatePart(year=year, month=month, day=None)


def _split_range(text: str):
    """Try to split a string into two date parts around a range dash.
    Handles both '"A" - "B"' and '"A"-"B"' (no surrounding spaces),
    while leaving single (non-range) strings alone.
    """
    text = text.strip()
    # Prefer an explicit " - " (or en-dash) split first -- least ambiguous.
    for sep in (" - ", " – ", "–"):
        if sep in text:
            left, right = text.split(sep, 1)
            if left.strip() and right.strip():
                return left.strip(), right.strip()

    # Fall back to a bare hyphen, e.g. "Apr-Oct 1991" or "1973-1975".
    if "-" in text:
        idx = text.index("-")
        left, right = text[:idx].strip(), text[idx + 1:].strip()
        if left and right:
            return left, right

    return None


def parse_date_string(raw: str) -> Optional[ParsedDate]:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    # Insert a space between a letter and a digit that are jammed
    # together (e.g. "Mar-Oct1993" -> "Mar-Oct 1993") so the year regex
    # (which requires a word boundary) can find the year.
    text = re.sub(r"(?<=[A-Za-z])(?=\d)", " ", text)

    split = _split_range(text)

    if split is None:
        part = _parse_part(text)
        if part is None or part.year is None:
            return None
        return ParsedDate(
            expression=_expr_for_part(part),
            begin=_begin_for_part(part),
            end=_end_for_part(part),
            date_type="single",
        )

    left_text, right_text = split
    left = _parse_part(left_text)
    right = _parse_part(right_text)
    if left is None or right is None:
        return None

    # Fill in a missing year from the sibling side (e.g. "Apr - Aug 1999").
    if left.year is None and right.year is not None:
        left.year = right.year
    if right.year is None and left.year is not None:
        right.year = left.year

    if left.year is None or right.year is None:
        return None

    left_expr = _expr_for_part(left)
    right_expr = _expr_for_part(right)

    if left_expr == right_expr:
        return ParsedDate(
            expression=left_expr,
            begin=_begin_for_part(left),
            end=_end_for_part(left),
            date_type="single",
        )

    return ParsedDate(
        expression=f"{left_expr} - {right_expr}",
        begin=_begin_for_part(left),
        end=_end_for_part(right),
        date_type="inclusive",
    )


def parse_date_cell(value) -> Optional[ParsedDate]:
    """Entry point: accepts whatever openpyxl handed back for the date
    column -- a datetime, a bare year float/int, or a free-text string.
    """
    if value is None:
        return None

    if isinstance(value, datetime.datetime):
        year, month, day = value.year, value.month, value.day
        if day == 1:
            # Treat as month precision -- day is very likely a placeholder.
            part = ParsedDatePart(year=year, month=month, day=None)
        else:
            part = ParsedDatePart(year=year, month=month, day=day)
        return ParsedDate(
            expression=_expr_for_part(part),
            begin=_begin_for_part(part),
            end=_end_for_part(part),
            date_type="single",
        )

    if isinstance(value, (int, float)):
        year = int(value)
        part = ParsedDatePart(year=year)
        return ParsedDate(
            expression=_expr_for_part(part),
            begin=_begin_for_part(part),
            end=_end_for_part(part),
            date_type="single",
        )

    if isinstance(value, str):
        return parse_date_string(value)

    return None
