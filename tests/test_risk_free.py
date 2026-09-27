"""The risk-free rate: the average 3-month Treasury bill rate over a window (audit
item 15a). Unit level: the average, its edges, and where the series comes from."""
from __future__ import annotations

import json
import sqlite3
from datetime import date

import numpy as np
import pandas as pd
import pytest

import src.db as db
import src.macro as macro
from src import risk_free
from src.performance import compute_risk_metrics, window_bounds


def _bills(values, start="2025-01-01", basis=risk_free.FRED):
    idx = pd.bdate_range(start, periods=len(values))
    return risk_free.BillSeries(pd.Series(values, index=idx, dtype=float), basis)


def test_the_rate_is_the_average_over_the_window():
    b = _bills([0.04, 0.05, 0.06, 0.07, 0.08])            # Jan 1-7, 2025, business days
    r = risk_free.rate_over("2025-01-02", "2025-01-06", b)
    assert r.annual == pytest.approx((0.05 + 0.06 + 0.07) / 3)
    assert (r.start, r.through, r.series_end) == (date(2025, 1, 2), date(2025, 1, 6),
                                                  date(2025, 1, 7))
    assert risk_free.describe(r) == (
        "6.00%, the average 3-month Treasury bill rate from January 2, 2025 to "
        "January 6, 2025")


def test_a_window_past_the_series_takes_its_last_observation():
    b = _bills([0.04, 0.05], basis=risk_free.STORED)
    r = risk_free.rate_over("2025-03-01", "2025-03-31", b)
    assert r.annual == pytest.approx(0.05) and r.start == r.through == date(2025, 1, 2)
    assert risk_free.describe(r) == (
        "5.00%, the 3-month Treasury bill rate on January 2, 2025. FRED was unavailable, "
        "so the rate comes from the stored series, which ends January 2, 2025")


def test_a_window_before_the_series_raises():
    with pytest.raises(risk_free.RiskFreeUnavailable):
        risk_free.rate_over("2024-01-01", "2024-06-30", _bills([0.04, 0.05]))


def _demo_copy(tmp_path, monkeypatch):
    """A copy of the demo book with no stored bill series, pointed at by src.db."""
    import os
    import shutil
    from pathlib import Path
    path = tmp_path / "rf.db"
    shutil.copyfile(Path(__file__).resolve().parent.parent / "data" / "demo.db", path)
    os.chmod(path, 0o644)
    con = sqlite3.connect(path)
    con.execute("DELETE FROM macro_cache WHERE series_id = 'DGS3MO'")
    con.commit()
    con.close()
    monkeypatch.setattr(db, "DB_PATH", path)
    monkeypatch.setattr(db, "_migrated_paths", set())
    return path


@pytest.fixture
def cache_db(tmp_path, monkeypatch):
    path = _demo_copy(tmp_path, monkeypatch)
    con = sqlite3.connect(path)
    for fetched, dates, values in (
            ("2026-05-01", ["2026-04-29", "2026-04-30"], [4.10, 4.20]),
            ("2026-05-27", ["2026-05-22", "2026-05-26"], [4.30, None])):
        con.execute("INSERT INTO macro_cache VALUES ('DGS3MO', ?, ?)",
                    (fetched, json.dumps({"dates": dates, "values": values})))
    con.commit()
    con.close()
    return path


def test_fred_comes_first_in_decimals(cache_db, monkeypatch):
    s = pd.Series([4.21, np.nan, 4.25], index=pd.to_datetime(["2026-09-22", "2026-09-23",
                                                               "2026-09-24"]))
    monkeypatch.setattr(macro, "get_series", lambda sid, start: s)
    b = risk_free.bill_series()
    assert b.basis == risk_free.FRED
    assert list(b.series.round(6)) == [0.0421, 0.0425], "percent to decimal, gaps dropped"


def test_without_fred_the_newest_stored_series_is_read_whatever_day_it_was_fetched(
        cache_db, monkeypatch):
    def _down(sid, start):
        raise macro.FREDFetchError(sid, OSError("offline"))
    monkeypatch.setattr(macro, "get_series", _down)
    b = risk_free.bill_series()
    assert b.basis == risk_free.STORED
    assert b.end == date(2026, 5, 22), "the newest row, with its missing value dropped"
    assert b.series.iloc[-1] == pytest.approx(0.043)


def test_with_neither_the_rate_is_unavailable(tmp_path, monkeypatch):
    _demo_copy(tmp_path, monkeypatch)
    monkeypatch.setattr(macro, "get_series",
                        lambda sid, start: (_ for _ in ()).throw(RuntimeError("no key")))
    with pytest.raises(risk_free.RiskFreeUnavailable):
        risk_free.bill_series()


def test_the_request_shares_the_macro_pages_cache_row():
    import ast
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "pages" / "3_Macro.py").read_text(
        encoding="utf-8")
    calls = [n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call)
             and getattr(n.func, "id", None) == "_try_fred"
             and n.args and getattr(n.args[0], "value", None) == "DGS3MO"]
    assert len(calls) == 1 and calls[0].args[1].value == risk_free.FETCH_START


@pytest.mark.parametrize("window", ["SI", "1Y", "YTD", "3M", "1M"])
def test_window_bounds_are_the_dates_the_metrics_cover(window):
    idx = pd.date_range("2025-05-01", "2026-09-24", freq="D")
    lo, hi = window_bounds(window, idx)
    assert hi == idx[-1]
    if window == "SI":
        assert lo == idx[0]
    else:
        from src.performance import _window_cutoff
        assert lo == _window_cutoff(window, idx[-1])


def test_sortino_measures_shortfall_below_the_rate():
    """Sortino's target is the rate: a series that never loses has no downside at a
    zero rate, and falls short on every day of a rate above its return."""
    idx = pd.bdate_range("2025-01-01", periods=60)
    rng = np.random.default_rng(0)
    pv = pd.Series(100 * np.cumprod(1 + 0.0002 + rng.uniform(0, 1e-4, len(idx))), index=idx)
    bl = pd.Series(100 * np.cumprod(1 + rng.normal(0, 0.01, len(idx))), index=idx)
    at_zero = compute_risk_metrics(pv, bl, rf_annual=0.0)
    above = compute_risk_metrics(pv, bl, rf_annual=0.20)
    assert np.isnan(at_zero["sortino"])
    assert above["sortino"] < 0 and above["sharpe"] < 0


def test_the_asset_evaluation_sample_ends_where_both_series_do(monkeypatch):
    """Bitcoin trades every day and the sleeves on weekdays; the sample's rate runs to
    the last day BOTH reach, not the later one. Needs a bill series that covers both ends,
    which the demo's stored series (ending May 2026) does not, so it is checked here."""
    import src.asset_evaluation as ae
    b = _bills([0.01] * 10 + [0.09] * 5, start="2026-01-01")
    monkeypatch.setattr(risk_free, "bill_series", lambda: b)
    slv = pd.DataFrame({"x": 1.0}, index=pd.bdate_range("2026-01-01", periods=10))
    btc = pd.Series(1.0, index=pd.date_range("2026-01-01", b.series.index[-1]))
    r = ae.sample_risk_free(btc, slv)
    assert r.through == slv.index[-1].date()
    assert r.annual == pytest.approx(0.01)
