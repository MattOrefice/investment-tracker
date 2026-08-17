"""Inception surfacing in the Performance page header banner.

The page showed the portfolio's age ("34 days") but never the date it started
from, leaving the reader to subtract it out of the as-of date. These pin the
copy helpers behind the extended banner.

Deterministic and offline: every helper is a pure function of (inception, days,
today), so these run identically in demo-mode CI and personal mode. The day
count is a caller-supplied argument by design — the Performance page measures
the portfolio's age to the settled display anchor (the last complete trading
day), not to today, so a count recomputed inside asof would disagree with the
figure rendered beside it whenever the price frontier lags the calendar.
"""
from datetime import date

from src.asof import (
    as_of_banner,
    as_of_banner_with_inception,
    format_long_date,
    inception_line,
)

# Fixed reference: today = 2026-07-14 (Q3 2026); most-recent COMPLETED quarter
# is Q2 2026 (Mar-31 → Jun-30). Inception 2026-06-09 is inside Q2, so a quarter
# is reportable and the banner names it rather than the empty state.
_TODAY = date(2026, 7, 14)
_INCEPTION = "2026-06-09"


def test_format_long_date_accepts_iso_string_and_date() -> None:
    """Both call shapes render the same long-form copy. get_inception_date
    returns an ISO string; the quarter helpers return date objects."""
    assert format_long_date("2026-06-09") == "June 9, 2026"
    assert format_long_date(date(2026, 6, 9)) == "June 9, 2026"


def test_format_long_date_day_is_not_zero_padded() -> None:
    """Single-digit days read '9', not '09' — the reason the helper composes the
    day as an integer instead of using %-d/%#d, which is platform-specific."""
    assert format_long_date("2026-06-09") == "June 9, 2026"
    assert format_long_date("2026-06-19") == "June 19, 2026"


def test_inception_line_pluralizes_day_count() -> None:
    """A one-day-old portfolio reads '1 day', not '1 days'."""
    assert inception_line(_INCEPTION, 1) == "Portfolio inception June 9, 2026 (1 day)."
    assert inception_line(_INCEPTION, 34) == "Portfolio inception June 9, 2026 (34 days)."


def test_inception_line_uses_supplied_day_count() -> None:
    """The count is echoed verbatim, never recomputed from today. Pins the
    contract that lets the page pass its settled-anchor age (34 at the reference
    date) rather than the calendar age (35), keeping banner and page consistent."""
    assert "(34 days)" in inception_line(_INCEPTION, 34)


def test_banner_with_inception_extends_plain_banner_in_order() -> None:
    """Live data → inception → locked report, one line. The plain banner's two
    clauses must survive verbatim so the extension is additive, not a rewrite.

    ``frontier`` is passed explicitly so this stays a test about COMPOSITION. The
    freshness clause now depends on how current the committed prices are (#189),
    and without pinning the frontier this unit test would silently read the
    machine's price cache and change wording with it — which is exactly what
    happened when the four-state banner landed.
    """
    line = as_of_banner_with_inception(_INCEPTION, 34, _TODAY, frontier=_TODAY)
    assert line == (
        "Live data as of July 14, 2026. "
        "Portfolio inception June 9, 2026 (34 days). "
        "Latest locked quarterly report: Q2 2026 (June 30, 2026)."
    )


def test_plain_banner_unchanged_by_inception_variant() -> None:
    """as_of_banner() is rendered by every other page and must not gain the
    inception clause — the extension is opt-in via the separate entry point."""
    assert "Portfolio inception" not in as_of_banner(frontier=_TODAY)
