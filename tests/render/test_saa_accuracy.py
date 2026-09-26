"""SAA page accuracy (2026-09-25 audit, item 6), rendered on the demo book.

The audit found: an endowment row labelled "This Portfolio (SAA)" that plotted a
typed-in 78 / 10 / 10 / 2-cash allocation beside an SAA of 80 / 10 / 10 with no cash;
a sleeve table whose caption described actual weights it did not show, cut off at ten
of its twelve rows; "(CAPE historically extreme (CAPE data through 2026-08-01) —
comparable only to the 1929 and 1999 peaks)", garbled, and wrong about 1929 (its peak
was 32.6); and "International Developed (20%)", a sleeve the demo book split into four.
"""
from __future__ import annotations

import json
import math
import os
import shutil
from pathlib import Path

import pandas as pd
import pytest

import src.db as db
import src.prices as prices

ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.fixture(scope="module")
def saa_page(tmp_path_factory):
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    import src.config as config
    mp = pytest.MonkeyPatch()
    copy = tmp_path_factory.mktemp("saa") / "demo.db"
    shutil.copyfile(ROOT / "data" / "demo.db", copy)
    os.chmod(copy, 0o644)
    mp.setattr(db, "DB_PATH", copy)
    mp.setattr(db, "_migrated_paths", set())
    mp.setattr(db, "_RUNTIME_CACHE", None)
    mp.setattr(prices._SESSION, "get", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    mp.setattr(config, "IS_DEMO", True)
    # AppTest keeps no element-level layout, so the table's height is read at the call.
    calls = []
    real = st.dataframe

    def _record(data, *a, **k):
        calls.append((data, k))
        return real(data, *a, **k)

    mp.setattr(st, "dataframe", _record)
    st.cache_data.clear()
    at = AppTest.from_file(str(ROOT / "pages" / "1_SAA.py"), default_timeout=600).run()
    assert not at.exception, [str(e.value) for e in at.exception]
    yield at, calls
    st.cache_data.clear()
    mp.undo()


def _markdown(at):
    return [str(m.value) for m in at.markdown]


def _sleeve_table(at, calls):
    frames = [(d, k) for d, k in calls if isinstance(d, pd.DataFrame) and "Sleeve" in d.columns]
    assert len(frames) == 1, "premise: one sleeve table"
    return frames[0]


def test_the_endowment_row_is_the_saa_targets(saa_page):
    at, _ = saa_page
    with db.get_connection() as conn:
        parents = {r["name"]: float(r["target_weight"]) * 100 for r in conn.execute(
            "SELECT name, target_weight FROM asset_classes WHERE parent_id IS NULL")}
    spec = json.loads(at.get("plotly_chart")[-1].proto.spec)
    row = {}
    for tr in spec["data"]:
        assert tr["y"][-1] == "This Portfolio (SAA targets)", tr["y"]
        row[tr["name"]] = tr["x"][-1]
    assert row["Public Equity"] == pytest.approx(parents["Equity"])
    assert row["Fixed Income"] == pytest.approx(parents["Income"])
    assert row["Real Assets"] == pytest.approx(parents["Real Assets"])
    assert row["Cash"] == 0.0 and parents["Cash"] == 0.0
    assert round(row["Public Equity"]) == 80, "premise: the demo SAA is 80 / 10 / 10"


def test_the_table_shows_actual_and_drift_for_every_sleeve(saa_page):
    at, calls = saa_page
    df, kwargs = _sleeve_table(at, calls)
    assert {"Target (%)", "Actual (%)", "Drift (pp)"} <= set(df.columns), list(df.columns)
    assert len(df) == 12
    # Drift is computed before rounding, so it may differ from the rounded columns by 0.1.
    assert ((df["Actual (%)"] - df["Target (%)"] - df["Drift (pp)"]).abs() <= 0.1 + 1e-9).all()
    assert not [v for v in df["Drift (pp)"] if v == 0 and math.copysign(1, v) < 0], \
        "a rounded drift renders as -0.0"
    assert kwargs.get("height") is not None and kwargs["height"] >= 35 * (len(df) + 1), kwargs


def test_the_actual_column_matches_the_weights_the_band_verdict_uses(saa_page):
    at, calls = saa_page
    df, _ = _sleeve_table(at, calls)
    from datetime import date
    from src.holdings import sleeve_weights_with_coverage
    sw, _ = sleeve_weights_with_coverage(date.today().isoformat())
    for _, r in df.iterrows():
        expected = float(sw["Actual Weight"].get(r["Sleeve"], 0.0)) * 100
        assert r["Actual (%)"] == pytest.approx(round(expected, 1)), r["Sleeve"]


def _earlier_years_by_loop(series, level):
    """An independent restatement: walk back from the end past the current run."""
    vals = list(series.dropna().items())
    i = len(vals) - 1
    while i >= 0 and vals[i][1] >= level:
        i -= 1
    return sorted({ts.year for ts, v in vals[: i + 1] if v >= level})


def test_the_cape_sentence_is_derived_and_reads_cleanly(saa_page):
    at, _ = saa_page
    from src.shiller import get_cape_series
    from src.prose_helpers import year_ranges
    s = get_cape_series().dropna()
    cv = float(s.iloc[-1])
    para = next(m for m in _markdown(at) if m.startswith("Strategic asset allocation reflects"))
    assert f"CAPE {cv:.1f} as of {s.index[-1].strftime('%B %Y')}" in para
    years = _earlier_years_by_loop(s, cv)
    assert years, "premise: CAPE has been this high before"
    assert f"a level reached before only in {year_ranges(years)}." in para, para
    assert "1929" not in para and "comparable only" not in para
    assert "(CAPE data through" not in para and "((" not in para.replace(" ", "")


def test_the_international_sleeves_are_named_as_the_split_defines_them(saa_page):
    at, _ = saa_page
    from src.sleeve_config import international_sleeves
    names = international_sleeves()
    assert len(names) == 4, "premise: the demo book splits developed international"
    para = next(m for m in _markdown(at) if "unhedged inflation tail" in m)
    assert all(f"{n} " in para for n in names), para
    assert "International Developed (" not in para


def test_a_failed_cape_read_says_so_rather_than_inventing_a_label(tmp_path, monkeypatch):
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    import src.config as config
    import src.shiller as shiller
    copy = tmp_path / "demo.db"
    shutil.copyfile(ROOT / "data" / "demo.db", copy)
    os.chmod(copy, 0o644)
    monkeypatch.setattr(db, "DB_PATH", copy)
    monkeypatch.setattr(db, "_migrated_paths", set())
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    monkeypatch.setattr(prices._SESSION, "get", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    monkeypatch.setattr(config, "IS_DEMO", True)
    monkeypatch.setattr(shiller, "get_cape_series",
                        lambda: (_ for _ in ()).throw(FileNotFoundError("no CAPE file")))
    st.cache_data.clear()
    at = AppTest.from_file(str(ROOT / "pages" / "1_SAA.py"), default_timeout=600).run()
    st.cache_data.clear()
    assert not at.exception
    para = next(m for m in _markdown(at) if m.startswith("Strategic asset allocation reflects"))
    assert "the CAPE reading could not be loaded for this render" in para
    assert "historically" not in para.split("It is balanced")[0]


# ── the helpers, at their edges ──────────────────────────────────────────────

def _monthly(values, start="2020-01-01"):
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq="MS"))


@pytest.mark.parametrize("values, level, expected", [
    ([10, 12, 11, 12], 12, [2020]),                 # the current run is only the last month
    ([12, 12, 9, 12, 12], 12, [2020]),              # a run of two at the end is left out
    ([9] * 11 + [13] + [13] * 3, 13, []),           # a run spanning a year end is left out whole
    ([9, 9, 9], 10, []),                            # never this high
    ([13, 9, 9], 10, [2020]),                       # no current run: the latest is below
])
def test_earlier_years_leave_out_the_current_run(values, level, expected):
    from src.shiller import earlier_years_at_or_above
    assert earlier_years_at_or_above(_monthly(values), level) == expected


@pytest.mark.parametrize("years, text", [
    ([1999, 2000], "1999–2000"),
    ([1929], "1929"),
    ([1929, 1999, 2000], "1929 and 1999–2000"),
    ([1929, 1966, 1999, 2000], "1929, 1966, and 1999–2000"),
])
def test_year_ranges(years, text):
    from src.prose_helpers import year_ranges
    assert year_ranges(years) == text


@pytest.mark.parametrize("years, tail", [
    ([1999, 2000], ", a level reached before only in 1999–2000."),
    ([], ", above every earlier reading."),
    ([1929, 1966, 1999, 2021], "the Shiller record."),        # four stretches: not rare
])
def test_the_cape_sentence_at_its_edges(years, tail):
    from src.prose_helpers import cape_valuation_sentence
    s = cape_valuation_sentence(41.48, "September 2026", 99.1, years)
    assert s.endswith(tail), s
    assert s.startswith("Strategic asset allocation reflects US equity valuations that are "
                        "historically extreme: CAPE 41.5 as of September 2026, the 99th percentile")
