"""Shared as-of date utilities — banner text for every Streamlit page."""
from datetime import date


def _most_recent_completed_quarter(today: date | None = None) -> tuple[date, date, str]:
    """Return (quarter_start, quarter_end, label) for the most recently completed quarter.

    Quarter start = prior quarter-end date (conventional: Q1 return uses Dec-31 base price).
    Automatically advances when a new quarter closes.
    """
    d = today or date.today()
    quarters = [
        (date(d.year - 1, 12, 31), date(d.year, 3, 31),  f"Q1 {d.year}"),
        (date(d.year, 3, 31),       date(d.year, 6, 30),  f"Q2 {d.year}"),
        (date(d.year, 6, 30),       date(d.year, 9, 30),  f"Q3 {d.year}"),
        (date(d.year, 9, 30),       date(d.year, 12, 31), f"Q4 {d.year}"),
    ]
    for q_start, q_end, label in reversed(quarters):
        if q_end < d:
            return q_start, q_end, label
    return date(d.year - 1, 9, 30), date(d.year - 1, 12, 31), f"Q4 {d.year - 1}"


# Empty-state copy when no completed quarter has elapsed since inception.
# Pinned literal — referenced by the Performance page (Section 1a snapshot and
# the PDF-export expander) and the every-page as-of banner. Update all callers
# and tests together if this string changes.
NO_COMPLETED_QUARTER = "No completed quarter yet."


def most_recent_reportable_quarter(
    inception: "date | str", today: date | None = None
) -> "tuple[date, date, str] | None":
    """Most recent COMPLETED quarter the portfolio actually existed for.

    Returns (quarter_start, quarter_end, label) for the most recently completed
    quarter whose end is on or after ``inception`` — i.e. the portfolio existed
    for at least part of it (a partial first quarter, where
    ``quarter_start < inception <= quarter_end``, still qualifies and reports).

    Returns ``None`` when the entire most-recent completed quarter predates
    inception (``quarter_end < inception``): there is no completed quarter to
    report yet, so callers should render the empty state rather than an
    all-zero "locked" report for a span that precedes the portfolio.

    ``inception`` accepts a date or an ISO date string.
    """
    if isinstance(inception, str):
        inception = date.fromisoformat(inception)
    q_start, q_end, label = _most_recent_completed_quarter(today)
    if q_end < inception:
        return None
    return q_start, q_end, label


def reportable_quarter_phrase(inception: "date | str", today: date | None = None) -> str:
    """Parenthetical quarter label for help/tooltip copy.

    '(Q1 2026)' when a completed quarter is reportable, else
    '(no completed quarter yet)'. Sourced from most_recent_reportable_quarter
    so help text never names a stale or pre-inception quarter.
    """
    q = most_recent_reportable_quarter(inception, today)
    return f"({q[2]})" if q else "(no completed quarter yet)"


def as_of_live_line() -> str:
    """Return the live-data line only. e.g. 'Live data as of May 10, 2026.'"""
    today = date.today()
    today_str = f"{today.strftime('%B')} {today.day}, {today.year}"
    return f"Live data as of {today_str}."


def as_of_report_line(today: date | None = None, inception: "date | str | None" = None) -> str:
    """Return the locked-report line. e.g. 'Latest locked quarterly report: Q1 2026 (March 31, 2026).'

    When the most-recent completed quarter entirely predates inception (no
    completed quarter has elapsed since the portfolio began), returns the
    empty-state copy instead of naming a quarter the portfolio never existed
    during. ``inception`` defaults to the canonical MIN(trade_date).
    """
    today = today or date.today()
    if inception is None:
        from src.holdings import get_inception_date
        inception = get_inception_date()
    q = most_recent_reportable_quarter(inception, today)
    if q is None:
        return NO_COMPLETED_QUARTER
    _, q_end, q_label = q
    q_end_str = f"{q_end.strftime('%B')} {q_end.day}, {q_end.year}"
    return f"Latest locked quarterly report: {q_label} ({q_end_str})."


def as_of_banner() -> str:
    """Return muted one-line as-of banner for display under each page title.

    Format: "Live data as of May 4, 2026. Latest locked quarterly report: Q1 2026 (March 31, 2026)."
    Both dates are computed dynamically — no hardcoded strings.
    """
    return f"{as_of_live_line()} {as_of_report_line()}"
