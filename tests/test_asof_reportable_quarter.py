"""Inception-aware quarter reporting — pre-inception suppression.

Personal-mode bug: when the most-recent COMPLETED quarter entirely predates the
portfolio's inception (MIN trade_date), the Performance page rendered an
all-zero "locked" quarterly report, and the every-page as-of banner claimed a
"Latest locked quarterly report" for a quarter the portfolio never existed
during. The suppression rule is a pure function of (inception, today): a
completed quarter is reportable only if ``quarter_end >= inception``.

These exercise that function and the rendered banner copy directly with the
real production shape (inception = a single ISO date, as get_inception_date
returns), so they are deterministic and run offline in demo-mode CI — the
condition is mode-independent in logic. demo.db's older inception never trips
the suppression, so demo behavior is unchanged by construction.
"""
from datetime import date

from src.asof import (
    most_recent_reportable_quarter,
    reportable_quarter_phrase,
    latest_report_link,
    as_of_report_line,
    NO_COMPLETED_QUARTER,
)

# Fixed reference: today = 2026-06-10 (Q2 2026); the most-recent COMPLETED
# quarter is Q1 2026 (Dec-31-2025 → Mar-31-2026).
_TODAY = date(2026, 6, 10)
_Q1 = (date(2025, 12, 31), date(2026, 3, 31), "Q1 2026")


# ── Suppression branch (the bug) ──────────────────────────────────────────────

def test_fully_pre_inception_quarter_is_suppressed():
    """Inception AFTER the most-recent-completed-quarter-end → no reportable quarter.

    Production shape: inception is the real MIN(trade_date) = 2026-06-09, which is
    after Q1 2026 closed (2026-03-31); the whole quarter predates the portfolio.
    """
    assert most_recent_reportable_quarter(date(2026, 6, 9), _TODAY) is None
    # ISO-string inception (exactly what get_inception_date() returns) is accepted.
    assert most_recent_reportable_quarter("2026-06-09", _TODAY) is None


def test_day_after_quarter_end_is_suppressed():
    """Boundary: inception one day after the quarter closed → still suppressed."""
    assert most_recent_reportable_quarter(date(2026, 4, 1), _TODAY) is None


# ── Over-suppression guard (must NOT swallow legitimate quarters) ─────────────

def test_partial_first_quarter_still_reports():
    """Inception MID-quarter (quarter_start < inception <= quarter_end) is a
    legitimate partial first quarter and MUST still report — the fix must not
    swallow it. (Partial-quarter *labeling* is out of scope; it just reports.)"""
    assert most_recent_reportable_quarter(date(2026, 2, 15), _TODAY) == _Q1


def test_inception_on_quarter_end_reports():
    """Boundary inclusive: inception == quarter_end (existed on the last day) reports."""
    assert most_recent_reportable_quarter(date(2026, 3, 31), _TODAY) == _Q1


def test_inception_well_before_quarter_reports():
    """Normal case (demo.db shape): inception long before the quarter → reports."""
    assert most_recent_reportable_quarter(date(2025, 5, 1), _TODAY) == _Q1


# ── Rendered banner copy (shown on every page via as_of_banner) ───────────────

def test_as_of_report_line_renders_empty_state_copy():
    """When no completed quarter has elapsed since inception, the rendered banner
    line is the pinned empty-state literal — not a quarter the portfolio never saw."""
    line = as_of_report_line(today=_TODAY, inception=date(2026, 6, 9))
    assert line == NO_COMPLETED_QUARTER == "No completed quarter yet."


def test_as_of_report_line_renders_quarter_when_reportable():
    """When a quarter is reportable, the rendered banner line names it (demo shape)."""
    line = as_of_report_line(today=_TODAY, inception=date(2025, 5, 1))
    assert line == "Latest locked quarterly report: Q1 2026 (March 31, 2026)."


# ── PDF help-tooltip phrase (Performance page bf_period radio) ─────────────────

def test_reportable_quarter_phrase_names_quarter_when_reportable():
    """Tooltip phrase is the dynamic quarter label, never a hardcoded literal (demo shape)."""
    assert reportable_quarter_phrase(date(2025, 5, 1), _TODAY) == "(Q1 2026)"


def test_reportable_quarter_phrase_empty_state_when_pre_inception():
    """Tooltip phrase names no stale quarter when none is reportable (personal-mode shape)."""
    assert reportable_quarter_phrase(date(2026, 6, 9), _TODAY) == "(no completed quarter yet)"


# ── "Latest report" download link (Generate-Quarterly-Report expander) ────────

def test_latest_report_link_suppressed_when_pre_inception():
    """Personal-mode pre-inception: no reportable quarter → no 'Latest report' link,
    even though a stale PDF sits on disk. Pins the suppression branch (the bug)."""
    on_disk = ["Orefice_Portfolio_2026Q1.pdf", "Orefice_Portfolio_20250501_to_20260501.pdf"]
    assert latest_report_link(on_disk, date(2026, 6, 9), _TODAY) is None


def test_latest_report_link_shows_newest_when_reportable():
    """When a quarter is reportable (demo shape), the newest report on disk surfaces."""
    on_disk = ["Orefice_Portfolio_2026Q1.pdf", "Orefice_Portfolio_20250501_to_20260501.pdf"]
    assert latest_report_link(on_disk, date(2025, 5, 1), _TODAY) == "Orefice_Portfolio_2026Q1.pdf"


def test_latest_report_link_none_when_no_reports_on_disk():
    """Reportable quarter but empty output dir → still no link (nothing to surface)."""
    assert latest_report_link([], date(2025, 5, 1), _TODAY) is None
