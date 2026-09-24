"""#349 — the Performance page's other portfolio figures are TWRs, not value ratios.

Stage 2 was not the only figure dividing end value by start value on a series that
steps up on every deposit. The quarterly return tile, the cumulative-return chart's
portfolio line, and the reconciliation caption's "absolute" return did the same, and
on the owner's book the caption read 107.6% beside a 3.2% TWR.

The frozen book holds no deposit after inception, where a value ratio and a TWR agree
exactly, so this module plants one (2026-06-01) inside all three windows: the most
recent reportable quarter (Q2 2026, frozen today 2026-07-21) and since inception. Each
figure is checked against tests/first_principles.py, which values the book from the
ledger and never calls src.holdings or src.returns.
"""
from __future__ import annotations

import base64
import json
import re

import numpy as np
import pytest
from streamlit.testing.v1 import AppTest

from tests import first_principles as fp

pytestmark = pytest.mark.usefixtures("frozen_book_module")

DEPOSIT = ("2026-06-01", "VOO", 300.0)


@pytest.fixture(scope="module")
def rendered(frozen_book_module):
    """(page, book): page 2 rendered on the frozen book plus one deposit, and the
    independent reference over the same book. st.cache_data is cleared on both sides:
    the page's loaders are keyed on their arguments, not on the DB behind them."""
    import streamlit as st
    fp.plant_deposit(frozen_book_module, *DEPOSIT)
    st.cache_data.clear()
    at = AppTest.from_file("pages/2_Performance.py", default_timeout=180).run()
    assert not at.exception, f"page raised: {at.exception}"
    yield at, fp.Book(account_id=1)
    st.cache_data.clear()


def _quarter(at, book) -> "tuple[str, str, str, str]":
    """(label, start, end, rendered value) of the quarterly return tile. The window is
    the page's own definition (the quarter runs from the PRIOR quarter-end's close,
    e.g. Q2 = 03-31 to 06-30): a window is not the calculation under test, and
    guessing it differently would test the guess."""
    from datetime import date
    from src.asof import most_recent_reportable_quarter
    q_start, q_end, label = most_recent_reportable_quarter(book.inception, date.today())
    tile = next((m for m in at.metric if m.label == f"{label} return"), None)
    assert tile is not None, f"no '{label} return' tile among {[m.label for m in at.metric]}"
    return label, q_start.isoformat(), q_end.isoformat(), tile.value


def _portfolio_line(at) -> "dict[str, float]":
    """The cumulative-return chart's Portfolio trace, as {ISO date: percent}."""
    for chart in at.get("plotly_chart"):
        spec = json.loads(chart.proto.spec)
        title = (spec.get("layout", {}).get("yaxis", {}).get("title") or {}).get("text")
        if title != "Cumulative Return (%)":
            continue
        trace = next(t for t in spec["data"] if t.get("name") == "Portfolio")
        y = trace["y"]
        if isinstance(y, dict):          # plotly 6 typed array
            y = np.frombuffer(base64.b64decode(y["bdata"]), dtype=y["dtype"]).tolist()
        return {str(x)[:10]: float(v) for x, v in zip(trace["x"], y)}
    raise AssertionError("no chart titled 'Cumulative Return (%)'")


def test_the_deposit_would_move_every_figure(rendered):
    """PREMISE. Without a deposit inside each window a value ratio equals the TWR, and
    the three tests below could not tell the right expression from the wrong one."""
    at, book = rendered
    _, q_start, q_end, _ = _quarter(at, book)
    si_start, si_end = book.window("SI")
    for start, end in ((q_start, q_end), (si_start, si_end)):
        assert DEPOSIT[0] in book.flows(start, end), (start, end)
        assert book.value_ratio(start, end) - book.twr(start, end) > 0.10, (start, end)


def test_quarterly_tile_is_the_quarters_twr(rendered):
    at, book = rendered
    label, start, end, shown = _quarter(at, book)
    m = re.fullmatch(r"(-?\d+\.\d{2})%", str(shown))
    assert m, f"{label} tile is not a percentage: {shown!r}"
    expected = book.twr(start, end) * 100
    assert abs(float(m.group(1)) - expected) <= 0.006, (
        f"{label} tile reads {shown}, but the book's TWR over {start}..{end} is "
        f"{expected:.4f}% (a value ratio would read {book.value_ratio(start, end)*100:.4f}%; #349)."
    )


def test_cumulative_chart_is_the_twr_index(rendered):
    at, book = rendered
    line = _portfolio_line(at)
    inception, end = book.window("SI")
    assert max(line) == end, (max(line), end)
    for day in (DEPOSIT[0], "2026-06-30", end):
        expected = book.twr(inception, day) * 100
        assert line[day] == pytest.approx(expected, abs=1e-6), (
            f"the chart's Portfolio line reads {line[day]:.4f}% on {day}, but the book's "
            f"TWR since inception to that day is {expected:.4f}% (a value ratio would read "
            f"{book.value_ratio(inception, day)*100:.4f}%; #349)."
        )


def test_absolute_return_caption_is_the_twr(rendered):
    """The caption's "absolute" return equals the since-inception TWR by construction:
    Phase 11 set it up that way (docs/phase_11_diagnostic.md §2.1), and the value ratio
    kept the identity only until the first deposit."""
    at, book = rendered
    cap = next(str(c.value) for c in at.caption if "Returns — absolute (" in str(c.value))
    shown = float(re.search(r"absolute \((-?\d+\.\d)%\)", cap).group(1))
    twr = float(re.search(r"cumulative TWR \((-?\d+\.\d)%\)", cap).group(1))
    inception, end = book.window("SI")
    expected = book.twr(inception, end) * 100
    assert abs(shown - expected) <= 0.051 and shown == twr, (
        f"the caption's absolute return reads {shown}% (TWR {twr}%), but the book's TWR "
        f"since inception is {expected:.3f}% (a value ratio would read "
        f"{book.value_ratio(inception, end)*100:.3f}%; #349)."
    )
