"""No page formats a month name with a zero-padded day (#488, decided by the owner on
October 16, 2026).

The Macro page's trailing-P/E caption read "data as of Oct 02, 2026" beside a warning that
read "October 2, 2026": strftime("%b %d, %Y") pads days 1 to 9. Every date in words on a
page goes through asof.format_long_date now, and the Performance chart's hovers read
"%{x|%B %-d, %Y}". This reads every string literal under pages/, f-string parts and format
specs included, for a month directive (%b or %B) beside a zero-padded day (%d). The parsers
in src/trailing_pe.py and src/shiller.py read a provider's padded dates and are not pages.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_MONTH = re.compile(r"%[bB]")
_PADDED_DAY = re.compile(r"%d")


def padded_month_day(literal: str) -> bool:
    """A format with a month name and a zero-padded day. "%-d" is not "%d"."""
    return bool(_MONTH.search(literal) and _PADDED_DAY.search(literal))


def _literals(path: Path) -> "list[tuple[int, str]]":
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [(n.lineno, n.value) for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def test_the_detector_tells_a_padded_day_from_an_unpadded_one():
    for padded in ("%b %d, %Y", "%B %d, %Y", "<br>%{x|%b %d, %Y}<br>", "%d %b %Y"):
        assert padded_month_day(padded), padded
    for fine in ("%B %-d, %Y", "%{x|%B %-d, %Y}", "%B %Y", "%b %Y", "%Y-%m-%d"):
        assert not padded_month_day(fine), fine


def test_no_page_formats_a_month_name_with_a_zero_padded_day():
    pages = sorted((ROOT / "pages").rglob("*.py"))
    assert len(pages) >= 10, "premise: the pages are found"
    found = [f"{p.relative_to(ROOT)}:{line}: {s!r}" for p in pages
             for line, s in _literals(p) if padded_month_day(s)]
    assert not found, found


def test_the_performance_hovers_write_the_full_month_and_an_unpadded_day():
    src = (ROOT / "pages" / "2_Performance.py").read_text(encoding="utf-8")
    assert src.count("%{x|%B %-d, %Y}") == 3
