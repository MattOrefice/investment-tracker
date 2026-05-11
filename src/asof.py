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


def as_of_live_line() -> str:
    """Return the live-data line only. e.g. 'Live data as of May 10, 2026.'"""
    today = date.today()
    today_str = f"{today.strftime('%B')} {today.day}, {today.year}"
    return f"Live data as of {today_str}."


def as_of_report_line() -> str:
    """Return the locked-report line only. e.g. 'Latest locked quarterly report: Q1 2026 (March 31, 2026).'"""
    today = date.today()
    _, q_end, q_label = _most_recent_completed_quarter(today)
    q_end_str = f"{q_end.strftime('%B')} {q_end.day}, {q_end.year}"
    return f"Latest locked quarterly report: {q_label} ({q_end_str})."


def as_of_banner() -> str:
    """Return muted one-line as-of banner for display under each page title.

    Format: "Live data as of May 4, 2026. Latest locked quarterly report: Q1 2026 (March 31, 2026)."
    Both dates are computed dynamically — no hardcoded strings.
    """
    return f"{as_of_live_line()} {as_of_report_line()}"
