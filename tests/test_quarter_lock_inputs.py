"""A quarter lock keeps every input its sections read, not only prices (#382).

The lock froze prices; the factor rows, momentum, the HYG credit proxy, CAPE, the ETF
metadata and the dividend record were read live. Refreshing French's data restated
Q2's factor alpha from -33 to -27 bps/yr, and the executive summary cited August's
CAPE for a quarter that ended in June. Pinned here, on the frozen book:

  * every section built under the lock is byte-identical after every one of those
    inputs is revised on disk, and the same revision moves them without the lock
    (so the revision is real and reaches the sections);
  * CAPE is the quarter's last monthly reading, with a restatement note;
  * sections lock separately: with French's data short of the quarter, the factor
    and benchmark sections are pending with the data's end date, and lock later
    without moving anything already locked.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from tests.conftest import pin_today, point_at_frozen_book, unpin_leftovers

Q2 = ("2026-03-31", "2026-06-30")


@pytest.fixture
def book(tmp_path, monkeypatch):
    from src import reports
    monkeypatch.setattr(reports, "_render_chart_to_png", lambda *a, **k: None)
    pin_today(monkeypatch)
    path = point_at_frozen_book(monkeypatch, tmp_path)
    yield path
    unpin_leftovers()


def _strip(o):
    if isinstance(o, dict):
        return {k: _strip(v) for k, v in o.items() if not k.endswith("b64")}
    if isinstance(o, (list, tuple)):
        return [_strip(v) for v in o]
    return o


def _sections(snap):
    from src import reports
    from src.cache import snapshot_price_context
    from src.holdings import get_portfolio_account_id
    from src.positioning import get_style_box_data
    acct = get_portfolio_account_id()
    with snapshot_price_context(snap):
        return {
            # What the positioning section's style-box chart plots: the metadata
            # reaches the section only through it, and chart images are not compared.
            "pos_style": repr(get_style_box_data(Q2[1])),
            "exec": repr(_strip(reports._build_executive_summary(*Q2))),
            "hold": repr(_strip(reports._build_holdings_section(Q2[1]))),
            "perf": repr(_strip(reports._build_performance_section(*Q2))),
            "attr": repr(_strip(reports._build_attribution_section(*Q2))),
            "pos": repr(_strip(reports._build_positioning_section(Q2[1]))),
            "factor": repr(_strip(reports._build_factor_section(Q2[1]))),
            "bench": repr(_strip(reports._build_benchmark_section(*Q2))),
            "thesis": repr(_strip(reports._build_thesis_section(*Q2, account_id=acct))),
        }


def _revise_every_input(tmp_path, monkeypatch, book):
    """A provider revision of every non-price input, on disk and in the DB."""
    from src import factors, shiller, style_box
    rev = tmp_path / "revised"
    rev.mkdir()
    # Revised history AND rows after the quarter: the lock must see neither.
    post = pd.bdate_range("2026-07-01", "2026-09-30")
    for region, cfg in factors._FACTOR_CONFIG.items():
        df = pd.read_csv(cfg["cache"], index_col=0, parse_dates=True)
        df = df[df.index < "2026-07-01"]
        df.loc[df.index >= "2025-01-01"] *= 1.5
        df = pd.concat([df, pd.DataFrame(0.002, index=post, columns=df.columns)])
        df.index.name = "date"
        out = rev / cfg["cache"].name
        df.to_csv(out)
        monkeypatch.setitem(factors._FACTOR_CONFIG[region], "cache", out)
    umd = pd.read_csv(factors._UMD_CACHE, index_col=0, parse_dates=True)
    umd = umd[umd.index < "2026-07-01"]
    umd.loc[umd.index >= "2025-01-01"] *= 1.5
    umd = pd.concat([umd, pd.DataFrame(0.002, index=post, columns=umd.columns)])
    umd.index.name = "date"
    umd.to_csv(rev / "umd.csv")
    monkeypatch.setattr(factors, "_UMD_CACHE", rev / "umd.csv")
    hyg = pd.read_parquet(factors._HYG_CACHE)
    hyg["adj_close"] = hyg["adj_close"] * (1 + 0.001 * pd.Series(range(len(hyg)), index=hyg.index))
    hyg.to_parquet(rev / "hyg.parquet")
    monkeypatch.setattr(factors, "_HYG_CACHE", rev / "hyg.parquet")
    cape = pd.read_csv(shiller._CACHE_CSV)
    cape["cape"] = cape["cape"] * 0.5
    cape.to_csv(rev / "cape.csv", index=False)
    monkeypatch.setattr(shiller, "_CACHE_CSV", rev / "cape.csv")
    meta = json.loads(Path(style_box._META_PATH).read_text())
    # One ETF's figures only: the style box measures each ETF against SPY, so a
    # revision applied to every ETF alike would cancel and test nothing.
    sphq = meta["SPHQ"] if "SPHQ" in meta else meta.get("etfs", {}).get("SPHQ")
    for k, x in list(sphq.items()):
        if isinstance(x, (int, float)) and not isinstance(x, bool):
            sphq[k] = x * 1.7
    (rev / "meta.json").write_text(json.dumps(meta))
    monkeypatch.setattr(style_box, "_META_PATH", rev / "meta.json")
    con = sqlite3.connect(book)
    with con:
        con.execute("DELETE FROM dividends WHERE ticker = 'VOO'")
    con.close()


def test_every_locked_section_is_unmoved_by_a_revision_of_every_input(book, tmp_path, monkeypatch):
    """#382's acceptance, in CI. Killed by any reader that ignores the lock."""
    from src.cache import capture_quarter_snapshot, get_quarter_snapshot
    capture_quarter_snapshot("2026Q2")
    snap = get_quarter_snapshot("2026Q2")[0]
    before = _sections(snap)
    _revise_every_input(tmp_path, monkeypatch, book)
    after = _sections(get_quarter_snapshot("2026Q2")[0])
    moved = [k for k in before if before[k] != after[k]]
    assert not moved, f"locked sections moved under a provider revision: {moved}"

    # The control: the same revision, read without the input lock, moves the
    # sections that read those inputs. Without this the test could pass on a
    # revision that never reached a section.
    unlocked = _sections(snap._replace(inputs=None, inputs_pending=None, inputs_rule=None))
    for k in ("exec", "pos_style", "factor", "bench"):
        assert unlocked[k] != before[k], f"the revision does not reach {k}; nothing is tested"


def test_the_lock_holds_every_input_through_the_quarter_end(book, tmp_path, monkeypatch):
    """#371's rule for every input: data through the quarter's last day. The files
    here run past the quarter, so a lock that kept later rows would be caught."""
    from src.cache import capture_quarter_snapshot, get_quarter_snapshot
    from src.input_lock import ALL_INPUTS, CAPE, DIVIDENDS, ETF_METADATA, FF5_US
    _factor_files_through(tmp_path, monkeypatch, "2026-09-30")
    _cape_through(tmp_path, monkeypatch, "2026-09-01")
    from src.factors import load_factors
    assert load_factors("us").index.max() == pd.Timestamp("2026-09-30"), "premise: later rows exist"
    capture_quarter_snapshot("2026Q2")
    snap = get_quarter_snapshot("2026Q2")[0]
    assert set(snap.inputs) == set(ALL_INPUTS) and snap.inputs_pending == {}
    end = pd.Timestamp("2026-06-30")
    for name, value in snap.inputs.items():
        if name == ETF_METADATA:
            assert isinstance(value, dict) and value
        elif name == DIVIDENDS:
            assert value and all(max(s.index, default=date(2020, 1, 1)) <= end.date()
                                 for s in value.values())
        else:
            assert value.index.max() <= end, name
    assert snap.inputs[CAPE].index.max() == pd.Timestamp("2026-06-01"), (
        "CAPE's quarter-end observation is the quarter's last monthly reading")
    assert snap.inputs[FF5_US].index.max() == pd.Timestamp("2026-06-30")


def test_the_executive_summary_cites_the_quarter_end_cape_and_says_it_was_restated(book):
    from src.cache import (INPUTS_RULE_SINCE, capture_quarter_snapshot, get_quarter_snapshot,
                           inputs_restatement_note)
    capture_quarter_snapshot("2026Q2")
    snap = get_quarter_snapshot("2026Q2")[0]
    exec_ = _sections(snap)["exec"]
    assert "(June 2026, the quarter's last monthly reading)" in exec_
    assert "CAPE data through" not in exec_
    note = inputs_restatement_note("2026Q2", snap)
    assert note.startswith("Restated September 26, 2026: this quarter now locks every input")
    assert "June 2026" in note
    assert inputs_restatement_note("2026Q3", snap._replace(quarter_end="2026-09-30")) is None, (
        "a quarter that closes after the rule was never reported the old way")
    assert INPUTS_RULE_SINCE == date(2026, 9, 26)


# ── sections lock separately ──────────────────────────────────────────────────

def _extend_prices_through(book, day: str):
    """Carry every ticker's last close forward, weekdays, through ``day``."""
    con = sqlite3.connect(book)
    last = {t: (d, c) for t, d, c in con.execute(
        "SELECT p.ticker, p.price_date, p.close FROM prices p JOIN (SELECT ticker, "
        "MAX(price_date) mx FROM prices GROUP BY ticker) m ON p.ticker = m.ticker "
        "AND p.price_date = m.mx")}
    rows = []
    for t, (d, c) in last.items():
        cur = date.fromisoformat(d) + timedelta(days=1)
        while cur <= date.fromisoformat(day):
            if cur.weekday() < 5:
                rows.append((t, cur.isoformat(), c))
            cur += timedelta(days=1)
    with con:
        con.executemany("INSERT OR REPLACE INTO prices (ticker, price_date, close, adj_close) "
                        "VALUES (?, ?, ?, NULL)", rows)
    con.close()


def _factor_files_through(tmp_path, monkeypatch, day: str):
    from src import factors
    for region, cfg in factors._FACTOR_CONFIG.items():
        df = pd.read_csv(cfg["cache"], index_col=0, parse_dates=True)
        df = df[df.index <= day]
        extra = pd.bdate_range(df.index.max() + pd.Timedelta(days=1), day)
        df = pd.concat([df, pd.DataFrame(0.0001, index=extra, columns=df.columns)])
        df.index.name = "date"
        out = tmp_path / f"{region}_{day}.csv"
        df.to_csv(out)
        monkeypatch.setitem(factors._FACTOR_CONFIG[region], "cache", out)
    umd = pd.read_csv(factors._UMD_CACHE, index_col=0, parse_dates=True).squeeze("columns")
    umd = umd[umd.index <= day]
    extra = pd.bdate_range(umd.index.max() + pd.Timedelta(days=1), day)
    umd = pd.concat([umd, pd.Series(0.0001, index=extra, name=umd.name)])
    umd.index.name = "date"
    umd.to_frame().to_csv(tmp_path / f"umd_{day}.csv")
    monkeypatch.setattr(factors, "_UMD_CACHE", tmp_path / f"umd_{day}.csv")


def _cape_through(tmp_path, monkeypatch, month: str):
    from src import shiller
    df = pd.read_csv(shiller._CACHE_CSV)
    df = df[df["date"] < month]
    df = pd.concat([df, pd.DataFrame({"date": [month], "cape": [40.0]})])
    df.to_csv(tmp_path / "cape_q3.csv", index=False)
    monkeypatch.setattr(shiller, "_CACHE_CSV", tmp_path / "cape_q3.csv")


def test_on_october_1_the_factor_sections_wait_for_french_and_then_lock(book, tmp_path, monkeypatch):
    """Q3 on October 1: prices and CAPE cover the quarter, French ends August 31.
    The factor inputs are pending, the two sections say so with the data's end
    date, and once French covers September they lock without moving anything
    already locked."""
    from src import reports
    from src.cache import capture_quarter_snapshot, complete_quarter_inputs, get_quarter_snapshot
    from src.input_lock import CAPE, FF5_DEVELOPED_EXUS, FF5_US, UMD
    pin_today(monkeypatch, date(2026, 10, 1))
    _extend_prices_through(book, "2026-09-30")
    _factor_files_through(tmp_path, monkeypatch, "2026-08-31")
    _cape_through(tmp_path, monkeypatch, "2026-09-01")

    capture_quarter_snapshot("2026Q3")
    snap = get_quarter_snapshot("2026Q3")[0]
    assert {FF5_US, FF5_DEVELOPED_EXUS, UMD} <= set(snap.inputs_pending)
    assert snap.inputs_pending[FF5_US] == "2026-08-31"
    assert CAPE in snap.inputs, "CAPE has its September reading, so it locks now"
    factor_note = reports._pending_note(snap, "factor")
    assert factor_note == ("Pending: this section locks when the factor data covers the "
                           "quarter. The Fama-French factor data on file ends August 31, "
                           "2026; the quarter ended September 30, 2026.")
    assert reports._pending_note(snap, "benchmark") == factor_note
    assert reports._pending_note(snap, "executive_summary") is None

    # A pending input is never read live inside the lock.
    from src.cache import snapshot_price_context
    from src.factors import load_factors
    from src.input_lock import InputPending
    with snapshot_price_context(snap), pytest.raises(InputPending):
        load_factors("us")

    locked_cape = snap.inputs[CAPE].copy()
    locked_prices = snap.adj_close.copy()
    _factor_files_through(tmp_path, monkeypatch, "2026-09-30")
    _cape_through(tmp_path, monkeypatch, "2026-09-01")   # a revised CAPE file...
    from src import shiller
    df = pd.read_csv(shiller._CACHE_CSV)
    df.loc[df["date"] == "2026-09-01", "cape"] = 99.0      # ...that must not reach Q3
    df.to_csv(shiller._CACHE_CSV, index=False)
    done = complete_quarter_inputs("2026Q3")
    assert done.inputs_pending == {} and {FF5_US, FF5_DEVELOPED_EXUS, UMD} <= set(done.inputs)
    assert done.inputs[FF5_US].index.max() == pd.Timestamp("2026-09-30")
    pd.testing.assert_series_equal(done.inputs[CAPE], locked_cape)
    pd.testing.assert_frame_equal(done.adj_close, locked_prices)
    assert reports._pending_note(done, "factor") is None


def test_the_template_renders_a_pending_section(book):
    from src.reports import _make_report_env
    tmpl = _make_report_env().get_template("quarterly_report.html")
    src = Path(tmpl.filename).read_text(encoding="utf-8")
    assert "{% elif factor_pending %}" in src and "{% elif bench_pending %}" in src
    assert "{{ factor_pending }}" in src and "{{ bench_pending }}" in src


# ── the demo ships its locks ──────────────────────────────────────────────────

COMMITTED = ("2025Q2", "2025Q3", "2025Q4", "2026Q1", "2026Q2")


def test_the_demo_ships_its_completed_quarters_locked_with_every_input(monkeypatch):
    """A fresh demo container rebuilds from the repo, so a lock that lived only in
    its runtime cache was re-taken from whatever files were committed then. These
    were locked by tools/lock_demo_quarters.py before the 2026-09-25 refresh."""
    import src.db as db
    from src.cache import INPUTS_RULE, QUARTER_END_RULE, _parse_quarter_end, get_quarter_snapshot
    from src.input_lock import ALL_INPUTS, DIVIDENDS, ETF_METADATA
    root = Path(__file__).resolve().parent.parent
    monkeypatch.setattr(db, "DB_PATH", root / "data" / "demo.db")
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    con = sqlite3.connect(f"file:{(root / 'data' / 'demo.db').as_posix()}?mode=ro", uri=True)
    ids = [r[0] for r in con.execute("SELECT quarter_id FROM quarter_snapshots ORDER BY quarter_id")]
    con.close()
    assert tuple(ids) == COMMITTED
    for qid in COMMITTED:
        snap = get_quarter_snapshot(qid)[0]
        end = pd.Timestamp(_parse_quarter_end(qid))
        assert snap.rule == QUARTER_END_RULE and snap.inputs_rule == INPUTS_RULE, qid
        assert set(snap.inputs) == set(ALL_INPUTS) and snap.inputs_pending == {}, qid
        for name, value in snap.inputs.items():
            if name not in (DIVIDENDS, ETF_METADATA):
                assert value.index.max() <= end, (qid, name)
