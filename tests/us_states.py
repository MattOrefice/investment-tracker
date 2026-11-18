"""Every US state's name and postal code, for checks that the committed tree names no
home state (#706, #710). Listing all fifty singles out none of them.

`states_named` finds names in any text. `codes_named` finds postal codes in a Python
file's comments and string literals only, where prose lives: code is full of
two-letter capitals. Ten codes are left out of that check because the repo uses them
for something else (an SQL keyword, a pandas frequency, an abbreviation); the list
says what, as found in the tree on November 11, 2026.
"""
from __future__ import annotations

import io
import re
import tokenize

US_STATES: tuple[str, ...] = (
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado", "Connecticut",
    "Delaware", "Florida", "Georgia", "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa",
    "Kansas", "Kentucky", "Louisiana", "Maine", "Maryland", "Massachusetts", "Michigan",
    "Minnesota", "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada",
    "New Hampshire", "New Jersey", "New Mexico", "New York", "North Carolina",
    "North Dakota", "Ohio", "Oklahoma", "Oregon", "Pennsylvania", "Rhode Island",
    "South Carolina", "South Dakota", "Tennessee", "Texas", "Utah", "Vermont", "Virginia",
    "Washington", "West Virginia", "Wisconsin", "Wyoming",
)

STATE_CODES: tuple[str, ...] = tuple(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV "
    "NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split())

# Codes the repo writes for something else, with what it is.
CODES_USED_OTHERWISE: dict[str, str] = {
    "IN": "the SQL keyword, and the word in capitalised comments",
    "OR": "the SQL keyword, and the word in capitalised comments",
    "MS": 'pandas\' month-start frequency, resample("MS")',
    "ME": 'pandas\' month-end frequency, freq="ME"',
    "OK": "the word, in tools' messages",
    "ID": "a FRED series ID",
    "AR": "autoregressive, as in AR(1)",
    "MO": "the month suffix of a FRED tenor, DGS3MO",
    "SD": "standard deviation",
    "SC": "small cap",
}

_NAME_RE = re.compile(r"\b(" + "|".join(re.escape(s) for s in US_STATES) + r")\b")
# A code is a whole word. A regex's own word boundary (\bXX\b) counts as one, so a
# pattern that names a code is found too.
_CODE_RE = re.compile(
    r"(?:(?<![A-Za-z0-9_/.-])|(?<=\\b))("
    + "|".join(c for c in STATE_CODES if c not in CODES_USED_OTHERWISE)
    + r")(?![A-Za-z0-9_])")

# ── The contexts in which a state's name is not a place anyone lives ─────────────
# tests/test_no_home_state.py allows these and nothing else. They are written here,
# beside the list, so the census itself names no state.
#
# The market's clock. The repo keeps every rendered date and time in Eastern time,
# and says so as the exchange's city: its date, time, close, day and sessions, and
# the exchange's name. As found in the tree on November 17, 2026.
MARKET_CLOCK_CITY = "New York"
MARKET_CLOCK_AFTER = re.compile(
    r"^New York(?:'s(?= date| today| quarter| only| since|[,.)\"]| \(|\s*$)"
    r"| (?:time|close|day|session|Stock Exchange|holds a session)\b| correctly\b|\))")
MARKET_CLOCK_BEFORE = re.compile(
    r"(?:midnight|today|date|tomorrow|ended|after the) in $|\d\d:\d\d $")
# A typeface in app.py's font stack.
TYPEFACE_STATE = "Georgia"
TYPEFACE_STACK = "Georgia, 'Times New Roman'"


def allowed_context(text: str, start: int, name: str) -> bool:
    """Whether the state name ``name`` at ``text[start:]`` is one of the contexts above."""
    if name == MARKET_CLOCK_CITY:
        return bool(MARKET_CLOCK_AFTER.match(text[start:])
                    or MARKET_CLOCK_BEFORE.search(text[max(0, start - 20):start]))
    if name == TYPEFACE_STATE:
        return text.startswith(TYPEFACE_STACK, start)
    return False


def names_at(text: str) -> "list[tuple[int, str]]":
    """Every state name in ``text`` with its offset."""
    return [(m.start(), m.group(1)) for m in _NAME_RE.finditer(text)]


def states_named(text: str) -> list[str]:
    """Every state name in ``text``, in order, repeats included."""
    return _NAME_RE.findall(text)


def codes_in_text(text: str) -> list[str]:
    """Every state postal code in plain text (rendered prose, Markdown), less the codes
    the repo uses for something else."""
    return _CODE_RE.findall(text)


def codes_named(py_source: str) -> list[str]:
    """Every state postal code in a Python source's comments and string literals."""
    found: list[str] = []
    prose = {tokenize.COMMENT, tokenize.STRING, getattr(tokenize, "FSTRING_MIDDLE", -1)}
    for tok in tokenize.generate_tokens(io.StringIO(py_source).readline):
        if tok.type in prose:
            found += _CODE_RE.findall(tok.string)
    return found
