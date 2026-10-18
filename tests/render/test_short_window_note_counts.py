"""The Performance page's short-window note prints each window's own count (#512).

Under the risk tiles the page says: "Risk ratios at 1M and 3M windows reflect N and M
trading days respectively; ...". N and M were written `n_days or 21` and `n_days or 63`.
A window below the floor has no figures and so no n_days, and the sentence then printed
21 or 63 trading days for a window the page had not measured at that length. Both now
come from performance.trading_days_in_window, the count the floor is held to, whether
or not the window has figures.

Rendered on copies of the frozen book, offline:
  * as the book stands, today pinned to its next day: both windows have figures;
  * with every close carried forward flat through August 14, 2026 and today pinned
    there: the 30 days to that date hold three sessions with a return (July 16, 17 and
    20), so the 1 Month window is below the floor. The note printed 21. It prints 3.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

import pytest
from streamlit.testing.v1 import AppTest

from src.demo_refresh import is_session
from src.performance import MIN_TRADING_DAYS
from tests.conftest import FROZEN_TODAY, pin_today, point_at_frozen_book, unpin_leftovers
from tests.test_cape_read_dates import _extend_prices_through

LAST_REAL_CLOSE = date(2026, 7, 20)          # the frozen book's frontier
FLAT_THROUGH = date(2026, 8, 14)
NOTE = re.compile(r"Risk ratios at 1M and 3M windows reflect (\d+) and (\d+) trading days "
                  r"respectively;")


def _sessions_with_a_return(anchor: date, days: int) -> int:
    """The sessions in the ``days`` days to ``anchor`` on which the book's closes moved:
    those after the window's first day and on or before the last real close."""
    first = anchor - timedelta(days=days)
    return sum(is_session(first + timedelta(days=k))
               for k in range(1, days + 1) if first + timedelta(days=k) <= LAST_REAL_CLOSE)


def _render(tmp_path_factory, name: str, today: date, flat_through: "date | None"):
    import streamlit as st
    with pytest.MonkeyPatch.context() as mp:
        book = point_at_frozen_book(mp, tmp_path_factory.mktemp(name))
        if flat_through is not None:
            _extend_prices_through(book, flat_through.isoformat())
        pin_today(mp, today)
        st.cache_data.clear()
        at = AppTest.from_file("pages/2_Performance.py", default_timeout=300)
        at.run()
        first = {"exception": list(at.exception),
                 "notes": [m.value for m in at.markdown if NOTE.search(str(m.value))]}
        window = next(r for r in at.radio if r.key == "risk_metrics_window")
        window.set_value("1 Month")
        at.run()
        one_month = {"exception": list(at.exception),
                     "captions": [c.value for c in at.caption if "Insufficient data" in c.value],
                     "notes": [m.value for m in at.markdown if NOTE.search(str(m.value))]}
        st.cache_data.clear()
    unpin_leftovers()
    return first, one_month


@pytest.fixture(scope="module")
def as_it_stands(tmp_path_factory):
    return _render(tmp_path_factory, "note-plain", FROZEN_TODAY, None)


@pytest.fixture(scope="module")
def flat_into_august(tmp_path_factory):
    return _render(tmp_path_factory, "note-flat", FLAT_THROUGH, FLAT_THROUGH)


def _counts(notes) -> "tuple[int, int]":
    assert len(notes) == 1, notes
    found = NOTE.search(str(notes[0]))
    return int(found.group(1)), int(found.group(2))


def test_with_figures_in_both_windows_the_note_prints_their_counts(as_it_stands):
    first, one_month = as_it_stands
    assert not first["exception"], first["exception"]
    expected = (_sessions_with_a_return(LAST_REAL_CLOSE, 30),
                _sessions_with_a_return(LAST_REAL_CLOSE, 90))
    assert _counts(first["notes"]) == expected
    assert min(expected) >= MIN_TRADING_DAYS, "premise: both windows have figures"
    assert one_month["captions"] == [], "the 1 Month window shows its tiles"


def test_a_window_below_the_floor_prints_its_own_count_not_twenty_one(flat_into_august):
    first, one_month = flat_into_august
    assert not first["exception"] and not one_month["exception"]
    n_1m, n_3m = _counts(first["notes"])
    assert n_1m == _sessions_with_a_return(FLAT_THROUGH, 30) == 3, (
        "July 16, 17 and 20: the only days in the window on which a close moved")
    assert n_1m < MIN_TRADING_DAYS, "premise: the 1 Month window is below the floor"
    assert n_3m == _sessions_with_a_return(FLAT_THROUGH, 90) >= MIN_TRADING_DAYS
    assert "21 and" not in str(first["notes"][0]), "the fallback figure is back"


def test_the_note_and_the_windows_own_sentence_give_one_count(flat_into_august):
    """The window says why it has no figures, with the floor and its count. The note
    under the tiles gives the same count for it, on the same render."""
    _first, one_month = flat_into_august
    n_1m, _n_3m = _counts(one_month["notes"])
    assert one_month["captions"] == [
        f"Insufficient data for the 1 Month window: it needs at least {MIN_TRADING_DAYS} "
        f"trading days; it has {n_1m}."]


def test_the_page_types_no_count_into_the_sentence():
    """Read from the page's source: the sentence's two figures are the function's
    results and nothing sits beside them to fall back on."""
    import ast
    from pathlib import Path
    page = Path(__file__).resolve().parent.parent.parent / "pages" / "2_Performance.py"
    tree = ast.parse(page.read_text(encoding="utf-8"))
    assigned = {t.id: ast.unparse(n.value) for n in ast.walk(tree) if isinstance(n, ast.Assign)
                for t in n.targets if isinstance(t, ast.Name) and t.id in ("_n_1m", "_n_3m")}
    # _pv_m and _cf_m are pv and cf, or the benchmark in the portfolio's place when the
    # value series is withheld (#643); test_ytd_risk_in_january pins that binding.
    assert assigned == {
        "_n_1m": "trading_days_in_window(_pv_m, _bl_for_metrics, window='1M', cashflows=_cf_m)",
        "_n_3m": "trading_days_in_window(_pv_m, _bl_for_metrics, window='3M', cashflows=_cf_m)"}
    sentence = [ast.unparse(n) for n in ast.walk(tree) if isinstance(n, ast.JoinedStr)
                and "trading days respectively" in ast.unparse(n)]
    assert len(sentence) == 1 and "{_n_1m} and {_n_3m}" in sentence[0], sentence
