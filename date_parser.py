"""
date_parser.py

Parses the "Human readable dates updated final" column into:
  - expression: "YYYY Month D" / "YYYY Month" / "YYYY" (single) or two of
                those joined by " - " (range)
  - begin: an ISO8601 date at whatever precision is known --
           "YYYY", "YYYY-MM", or "YYYY-MM-DD"
  - end:   same precision rules, but only present for actual RANGES.
           A single date (whatever its precision) has no end at all.
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
    date_type: str  # "single" or "inclusive"
    end: Optional[str] = None  # only set for date_type == "inclusive"


def _expr_for_part(part: ParsedDatePart) -> str:
    if part.day:
        return f"{part.year} {MONTH_NAMES[part.month]} {part.day}"
    if part.month:
        return f"{part.year} {MONTH_NAMES[part.month]}"
    return f"{part.year}"


def _iso_for_part(part: ParsedDatePart) -> str:
    """Format at whatever precision is actually known -- no padding out
    to a fake day-of-month or month-of-year."""
    if part.day:
        return f"{part.year:04d}-{part.month:02d}-{part.day:02d}"
    if part.month:
        return f"{part.year:04d}-{part.month:02d}"
    return f"{part.year:04d}"


def _parse_part(text: str) -> Optional[ParsedDatePart]:
    """Parse a single side of a date (no range dash in it)."""
    text = text.strip().strip(".").strip()
    if not text:
        return None

    year_match = YEAR_RE.search(text)
    year = int(year_match.group(0)) if year_match else None

    # Season word => year-only precision, per instruction to "just use
    # the year range" rather than guessing a month.
    words = re.findall(r"[a-zA-Z]+", text.lower())
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


# "1998 March 8-11" or "March 8-11, 1998" -- a day-day range within a
# SINGLE month. Deliberately scoped to this one shape (year and month
# shared by both days) -- a range spanning a month boundary (e.g.
# "March 30 - April 2") is NOT handled here; no real data seen so far
# has needed that, and conflating the two would risk misparsing a
# genuine month-to-month range (already handled by _split_range) as a
# same-month one. Checked BEFORE _split_range, since the generic
# hyphen-splitter would otherwise slice "8-11" into two nonsense
# fragments ("...March 8" and "11") rather than recognizing it as one
# day-day pair.
DAY_RANGE_RE = re.compile(
    r"^(?:(?P<year1>1[5-9]\d{2}|20\d{2})\s+)?"
    r"(?P<month>[A-Za-z]+)\.?\s+"
    r"(?P<day1>\d{1,2})\s*-\s*(?P<day2>\d{1,2})"
    r"(?:,?\s*(?P<year2>1[5-9]\d{2}|20\d{2}))?$"
)


def _parse_day_range(text: str) -> Optional["ParsedDate"]:
    m = DAY_RANGE_RE.match(text.strip())
    if not m:
        return None

    month_word = m.group("month").lower()
    if month_word not in MONTHS:
        return None
    month = MONTHS[month_word]

    year_text = m.group("year1") or m.group("year2")
    if not year_text:
        return None
    year = int(year_text)

    day1, day2 = int(m.group("day1")), int(m.group("day2"))
    if not (1 <= day1 <= 31 and 1 <= day2 <= 31):
        return None
    if day2 < day1:
        # Doesn't look like a real day range (e.g. could be something
        # else entirely) -- let it fall through to the normal parsing
        # path instead of guessing.
        return None

    left = ParsedDatePart(year=year, month=month, day=day1)
    right = ParsedDatePart(year=year, month=month, day=day2)
    return ParsedDate(
        expression=f"{_expr_for_part(left)}-{day2}",
        begin=_iso_for_part(left),
        end=_iso_for_part(right),
        date_type="inclusive",
    )


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

    day_range = _parse_day_range(text)
    if day_range:
        return day_range

    split = _split_range(text)

    if split is None:
        part = _parse_part(text)
        if part is None or part.year is None:
            return None
        return ParsedDate(
            expression=_expr_for_part(part),
            begin=_iso_for_part(part),
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
            begin=_iso_for_part(left),
            date_type="single",
        )

    # Same year on both sides, and both have at least month precision:
    # don't repeat the year -- "1989 October - December" rather than
    # "1989 October - 1989 December". begin/end (below) still get the
    # full, unambiguous ISO value on each side regardless; only the
    # human-readable expression is shortened.
    if left.year == right.year and left.month and right.month:
        right_short = MONTH_NAMES[right.month]
        if right.day:
            right_short += f" {right.day}"
        combined_expr = f"{left_expr} - {right_short}"
    else:
        combined_expr = f"{left_expr} - {right_expr}"

    return ParsedDate(
        expression=combined_expr,
        begin=_iso_for_part(left),
        end=_iso_for_part(right),
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
            begin=_iso_for_part(part),
            date_type="single",
        )

    if isinstance(value, (int, float)):
        year = int(value)
        part = ParsedDatePart(year=year)
        return ParsedDate(
            expression=_expr_for_part(part),
            begin=_iso_for_part(part),
            date_type="single",
        )

    if isinstance(value, str):
        return parse_date_string(value)

    return None
