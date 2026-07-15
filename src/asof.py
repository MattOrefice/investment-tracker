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


def latest_report_link(existing_reports, inception: "date | str", today: date | None = None):
    """The report to surface as 'Latest report', or None to suppress the link.

    ``existing_reports`` is a newest-first sequence of report paths/names. Returns
    the newest one only when a completed quarter is currently reportable
    (``most_recent_reportable_quarter`` is not None); when none is reportable —
    the pre-inception case — returns None so a stale pre-inception report is not
    surfaced above the "No completed quarter yet." empty state. Inception-gated
    through the same helper the report and tooltip use (single source of truth);
    report *generation* is already inception-gated, so a reportable-quarter state
    only ever has reportable or custom reports on disk to link.
    """
    if most_recent_reportable_quarter(inception, today) is None:
        return None
    return existing_reports[0] if existing_reports else None


def reportable_quarter_phrase(inception: "date | str", today: date | None = None) -> str:
    """Parenthetical quarter label for help/tooltip copy.

    '(Q1 2026)' when a completed quarter is reportable, else
    '(no completed quarter yet)'. Sourced from most_recent_reportable_quarter
    so help text never names a stale or pre-inception quarter.
    """
    q = most_recent_reportable_quarter(inception, today)
    return f"({q[2]})" if q else "(no completed quarter yet)"


def format_long_date(d: "date | str") -> str:
    """Long-form date for banner and label copy. e.g. 'June 9, 2026'.

    Composed from strftime('%B') plus the integer day rather than a %-d/%#d
    directive, which is platform-specific. Accepts a date or an ISO string.
    """
    if isinstance(d, str):
        d = date.fromisoformat(d)
    return f"{d.strftime('%B')} {d.day}, {d.year}"


def as_of_live_line(today: date | None = None) -> str:
    """Return the live-data line only. e.g. 'Live data as of May 10, 2026.'

    ``today`` defaults to the current date; it is injectable so a composed banner
    can be pinned in a test without patching the clock.
    """
    return f"Live data as of {format_long_date(today or date.today())}."


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
    return f"Latest locked quarterly report: {q_label} ({format_long_date(q_end)})."


def inception_line(inception: "date | str", days: int) -> str:
    """Return the inception clause. e.g. 'Portfolio inception June 9, 2026 (35 days).'

    ``days`` is supplied by the caller rather than derived from ``date.today()``
    here: the pages that show a portfolio age measure it to the settled display
    anchor (the last complete trading day), not to today, so a locally recomputed
    count would disagree with the figure alongside it by a day or more.
    """
    return f"Portfolio inception {format_long_date(inception)} ({days} day{'s' if days != 1 else ''})."


def as_of_banner() -> str:
    """Return muted one-line as-of banner for display under each page title.

    Format: "Live data as of May 4, 2026. Latest locked quarterly report: Q1 2026 (March 31, 2026)."
    Both dates are computed dynamically — no hardcoded strings.
    """
    return f"{as_of_live_line()} {as_of_report_line()}"


def as_of_banner_with_inception(
    inception: "date | str", days: int, today: date | None = None
) -> str:
    """as_of_banner() extended with the portfolio's inception date and age.

    Format: "Live data as of July 14, 2026. Portfolio inception June 9, 2026
    (35 days). Latest locked quarterly report: Q2 2026 (June 30, 2026)."

    A separate entry point rather than a widened as_of_banner(): the plain banner
    is rendered by every page, most of which have no inception context to pass and
    no reason to name it. Every component is computed — no literal dates.
    """
    return (
        f"{as_of_live_line(today)} "
        f"{inception_line(inception, days)} "
        f"{as_of_report_line(today, inception)}"
    )
