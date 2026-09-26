"""One blended-benchmark series, rebalanced each calendar quarter (#383).

Every surface bought its own blended basket at the start of whatever window it
measured: the Performance page held one from inception and sliced it, the report
bought a fresh one per window, the regression held one from inception. So the page
and the report disagreed on Q2 (11.83% against 11.71%) and a year never equalled its
four quarters. Now there is one series: reset to SAA target weights at each calendar
quarter's base, held through the quarter, chain-linked. Pinned here:

  * the series' slice for each of the demo's five locked quarters equals that
    report's quarter figure to the last digit (the decision's acceptance);
  * it is one series: a year is its quarters compounded, and a window reads the same
    whatever start the series was requested from;
  * a lock taken before the rule keeps its report's figures (each window's own
    basket) and says so on the cover; a lock taken under it reads the series and
    states the rule;
  * the 60/40 naive benchmark follows the same rule (tests/test_attribution.py);
  * the site states the rule and the date it took effect.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from tests.conftest import pin_today, point_at_frozen_book, unpin_leftovers

ROOT = Path(__file__).resolve().parent.parent
PRIOR = {"2025Q2": "2025-03-31", "2025Q3": "2025-06-30", "2025Q4": "2025-09-30",
         "2026Q1": "2025-12-31", "2026Q2": "2026-03-31"}
Q2 = ("2026-03-31", "2026-06-30")


@pytest.fixture
def demo(tmp_path, monkeypatch):
    """A copy of the committed demo.db, with its five locks, offline."""
    import socket
    import src.db as db
    import src.prices as prices
    from src import reports
    copy = tmp_path / "demo.db"
    shutil.copyfile(ROOT / "data" / "demo.db", copy)
    os.chmod(copy, 0o644)

    def _offline(*_a, **_k):
        raise OSError("offline")

    monkeypatch.setattr(socket, "getaddrinfo", _offline)
    monkeypatch.setattr(prices._SESSION, "get", _offline)
    monkeypatch.setattr(db, "DB_PATH", copy)
    monkeypatch.setattr(db, "_migrated_paths", set())
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    monkeypatch.setattr(reports, "_render_chart_to_png", lambda *a, **k: None)
    pin_today(monkeypatch)
    yield copy
    unpin_leftovers()


@pytest.fixture
def book(tmp_path, monkeypatch):
    from src import reports
    monkeypatch.setattr(reports, "_render_chart_to_png", lambda *a, **k: None)
    pin_today(monkeypatch)
    path = point_at_frozen_book(monkeypatch, tmp_path)
    yield path
    unpin_leftovers()


def _html(monkeypatch, start, end):
    from src import reports
    monkeypatch.setattr(reports, "_render_pdf", lambda html: html.encode("utf-8"))
    return reports.generate_quarterly_report_bytes(start, end, is_demo=True).decode("utf-8")


# ── the acceptance ─────────────────────────────────────────────────────────────

def test_each_locked_quarter_is_the_series_slice_to_the_last_digit(demo):
    from src.benchmarks import BLENDED_RULE, get_custom_blended_series
    from src.cache import get_quarter_snapshot, snapshot_price_context
    from src.holdings import get_inception_date, get_portfolio_account_id
    from src.reports import _build_executive_summary, _fmt_pct
    inception = get_inception_date(account_id=get_portfolio_account_id())
    for q, start in PRIOR.items():
        snap = get_quarter_snapshot(q)[0]
        assert snap.benchmark_rule is None, f"{q} is a lock from before the rule"
        end = snap.quarter_end
        with snapshot_price_context(snap):
            report = _build_executive_summary(start, end)["blended_return_pct"]
        # The same locked prices, read through the one series from inception.
        with snapshot_price_context(snap._replace(benchmark_rule=BLENDED_RULE)):
            series = get_custom_blended_series(min(inception, start), end)
            with_fresh = get_custom_blended_series(start, end)
        got = float(series[pd.Timestamp(end)] / series[pd.Timestamp(start)] - 1)
        exact = float(with_fresh.iloc[-1] / with_fresh.iloc[0] - 1)
        assert _fmt_pct(got) == report, (q, _fmt_pct(got), report)
        assert abs(got - exact) <= 1e-12, (q, got, exact)


# ── one series ─────────────────────────────────────────────────────────────────

def _ret(series, start, end):
    return float(series[pd.Timestamp(end)] / series[pd.Timestamp(start)] - 1)


def test_a_year_is_its_quarters_compounded(book):
    from src.benchmarks import get_custom_blended_series
    ends = ["2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30"]
    year = get_custom_blended_series(ends[0], ends[-1])
    compounded = 1.0
    for a, b in zip(ends, ends[1:]):
        q = get_custom_blended_series(a, b)
        compounded *= float(q.iloc[-1] / q.iloc[0])
    assert abs(_ret(year, ends[0], ends[-1]) - (compounded - 1)) < 1e-12


@pytest.mark.parametrize("window", [("2025-11-14", "2026-05-20"), ("2026-06-20", "2026-07-20")])
def test_a_window_reads_the_same_whatever_start_the_series_was_requested_from(book, window):
    from src.benchmarks import get_custom_blended_series
    s, e = window
    from_inception = get_custom_blended_series("2025-05-01", e)
    own = get_custom_blended_series(s, e)
    assert abs(_ret(from_inception, s, e) - float(own.iloc[-1] / own.iloc[0] - 1)) < 1e-12
    # And it is not one basket held from the window's start: that is the old rule.
    from src.benchmarks import _fresh_basket_series
    held = _fresh_basket_series(s, e)
    assert abs(float(held.iloc[-1] / held.iloc[0] - 1) - _ret(from_inception, s, e)) > 1e-5


def test_the_chain_mechanics(monkeypatch):
    """Segments at calendar quarter-ends; gaps carried once each; a non-trading
    segment start holds the level; a segment that prices nothing is a NaN sentinel."""
    from src import benchmarks as bm
    calls = []

    def build(a, b):
        calls.append((a, b))
        idx = pd.date_range(a, b, freq="D")
        s = pd.Series(1.0 + 0.01 * pd.Series(range(len(idx))).values, index=idx)
        if a == "2025-12-31":
            s.iloc[:2] = float("nan")          # the base and the next day unpriced
            s.iloc[2:] = s.iloc[2:] / s.iloc[2]
        s.attrs["benchmark_gaps"] = [("Sleeve", "X", a)] if a == "2025-09-30" else [
            ("Sleeve", "Y", "2020-01-01")]
        return s

    out = bm._quarterly_reset(build, "2025-11-14", "2026-02-10")
    assert calls == [("2025-09-30", "2025-12-31"), ("2025-12-31", "2026-02-10")]
    assert out.index[0] == pd.Timestamp("2025-11-14") and out.iloc[0] == 1.0
    assert out.attrs["benchmark_gaps"] == [("Sleeve", "X", "2025-09-30"),
                                           ("Sleeve", "Y", "2020-01-01")]
    assert out[pd.Timestamp("2026-01-01")] == out[pd.Timestamp("2025-12-31")], (
        "a non-trading day after the base holds the level")

    def nothing(a, b):
        s = pd.Series(float("nan"), index=pd.date_range(a, b, freq="D"))
        s.attrs["benchmark_gaps"] = [("Sleeve", "Z", a)]
        return s

    dead = bm._quarterly_reset(nothing, "2025-11-14", "2026-02-10")
    assert dead.isna().all() and dead.attrs["benchmark_gaps"]


# ── locked reports: the old ones keep their figures, the new ones read the series ──

def _perf_blended(snap, label="Since Inception"):
    from src import reports
    from src.cache import snapshot_price_context
    with snapshot_price_context(snap):
        rows = reports._build_performance_section(*Q2)["period_rows"]
    return next(r["blended"] for r in rows if r["period"] == label)


def test_a_lock_from_before_the_rule_keeps_its_baskets_and_says_so(book, monkeypatch):
    from src.benchmarks import BLENDED_RULE, _fresh_basket_series, blended_rule_note
    from src.cache import (benchmark_construction_note, capture_quarter_snapshot,
                           get_quarter_snapshot)
    from src.holdings import get_inception_date, get_portfolio_account_id
    from src.reports import _fmt_pct
    snap, _ = capture_quarter_snapshot("2026Q2")
    assert snap.benchmark_rule == BLENDED_RULE, (
        "the frame a first generation uses straight from the capture carries the rule")
    new = get_quarter_snapshot("2026Q2")[0]
    assert new.benchmark_rule == BLENDED_RULE, "a lock taken now records the rule"
    old = new._replace(benchmark_rule=None)

    inception = get_inception_date(account_id=get_portfolio_account_id())
    held = _fresh_basket_series(inception, Q2[1])
    assert _perf_blended(old) == _fmt_pct(float(held.iloc[-1] / held.iloc[0] - 1)), (
        "a pre-rule report measures since inception with one basket held from inception")
    assert _perf_blended(new) != _perf_blended(old), "premise: the rules differ here"

    assert benchmark_construction_note(new) is None
    note = benchmark_construction_note(old)
    assert note.startswith("The Custom Blended figures in this report hold the SAA "
                           "target-weight basket bought at the start of each period shown")

    html_new = _html(monkeypatch, *Q2)
    assert blended_rule_note() in html_new and note not in html_new
    con = sqlite3.connect(book)
    blob = json.loads(con.execute("SELECT snapshot_data FROM quarter_snapshots "
                                  "WHERE quarter_id = '2026Q2'").fetchone()[0])
    del blob["benchmark_rule"]
    with con:
        con.execute("UPDATE quarter_snapshots SET snapshot_data = ? WHERE quarter_id = '2026Q2'",
                    (json.dumps(blob),))
    con.close()
    html_old = _html(monkeypatch, *Q2)
    assert note in html_old and blended_rule_note() not in html_old


def test_the_committed_locks_are_all_from_before_the_rule(monkeypatch):
    """So each keeps its figures and carries the construction line."""
    import src.db as db
    from src.cache import benchmark_construction_note, get_quarter_snapshot
    monkeypatch.setattr(db, "DB_PATH", ROOT / "data" / "demo.db")
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    for q in PRIOR:
        assert benchmark_construction_note(get_quarter_snapshot(q)[0]), q


# ── the site states the rule ───────────────────────────────────────────────────

def test_the_rule_and_its_date_are_stated_where_the_blend_is_described(book):
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    from src.benchmarks import blended_rule_note
    rule = blended_rule_note()
    assert "each calendar quarter" in rule and "September 26, 2026" in rule
    # Each passage that describes a blend states the rule itself: one passage stating
    # it must not stand in for another.
    describes = {
        "2_Performance.py": ("The Custom Blended benchmark", "What the two stages measure"),
        "6_Benchmark_Attribution.py": ("R_benchmark − RF** is the custom", "Benchmark returns:"),
    }
    for page, markers in describes.items():
        st.cache_data.clear()
        at = AppTest.from_file(str(ROOT / "pages" / page), default_timeout=300).run()
        assert not at.exception, [str(e.value) for e in at.exception]
        texts = [str(e.value) for e in list(at.caption) + list(at.markdown)]
        for marker in markers:
            passage = [t for t in texts if marker in t]
            assert passage, (page, marker)
            assert all(rule in t for t in passage), (page, marker)
    st.cache_data.clear()
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "chain-linked (the rule since September 26, 2026)" in readme


@pytest.mark.parametrize("window", ["SI", "YTD"])
def test_the_stages_state_the_two_benchmarks_apart(book, window):
    """SI spans quarters; YTD also starts on a non-trading day (January 1), where the
    held basket skips the year's first trading day (#391). Either way the page states
    the difference between the two benchmarks, never as rebalancing alone."""
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    st.cache_data.clear()
    at = AppTest.from_file(str(ROOT / "pages" / "2_Performance.py"), default_timeout=300).run()
    [r for r in at.radio if r.key == "bf_period"][0].set_value(window).run()
    assert not at.exception, [str(e.value) for e in at.exception]
    caps = [str(c.value) for c in at.caption]
    assert any("against one basket held from the window’s start. Stage 1 reads the SAA "
               "blend rebalanced each calendar quarter," in c for c in caps)
    assert any(c.startswith("Stage 2 is measured against the SAA blend rebalanced each "
                            "calendar quarter; Brinson-Fachler holds one basket") for c in caps)
    assert not any("rebalancing adds" in c or "that rebalancing is" in c for c in caps)
    bf_line = next(c for c in caps if c.startswith("**BF decomposition:**"))
    assert "vs. Stage 2: ✓" in bf_line, "net of the difference, BF still reconciles"
    st.cache_data.clear()
