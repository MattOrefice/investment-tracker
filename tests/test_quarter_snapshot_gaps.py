"""#204 — the quarter snapshot captures benchmark CONSTITUENTS, and a series it cannot
lock is stored as a DISCLOSED gap rather than dropped by `except Exception: pass`.

A ticker absent from the snapshot is read live by snapshot_price_context, so a report
under a "Prices locked" cover would be partly unlocked with nothing saying so.
"""
import sqlite3

import pandas as pd
import pytest

import src.cache as cache
import src.db as db


@pytest.fixture
def snap_db(tmp_path, monkeypatch):
    path = tmp_path / "snap.db"
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE securities (ticker TEXT PRIMARY KEY);
        CREATE TABLE asset_classes (name TEXT, benchmark_ticker TEXT);
        INSERT INTO securities VALUES ('VOO'), ('BADX'), ('SPAXX');
        INSERT INTO asset_classes VALUES ('US Large Core', 'VOO'),
            ('Real Assets', 'VNQ (60%) + DBC (40%)'), ('Broken', 'ZZZ (70%) + YYY (10%)');
    """)
    c.commit(); c.close()
    monkeypatch.setattr(db, "DB_PATH", path)
    monkeypatch.setattr(db, "_migrated_paths", {str(path)})
    return path


def _prices(ticker, start, end=None):
    if ticker == "BADX":
        raise ValueError("delisted")
    idx = pd.to_datetime(["2026-03-30", "2026-03-31"]).date
    return pd.DataFrame({"close": [1.0, 1.1], "adj_close": [1.0, 1.1]}, index=idx)


def test_composite_benchmarks_expand_to_their_constituents(snap_db):
    tickers, gaps = cache._get_all_snapshot_tickers()
    assert {"VNQ", "DBC", "VOO", "BADX"} <= set(tickers)
    assert not any("+" in t for t in tickers), "a composite spec was treated as a ticker"
    assert "SPAXX" not in tickers
    # An unparseable spec is a disclosed gap, never a silent drop.
    assert [g[0] for g in gaps] == ["ZZZ (70%) + YYY (10%)"]


def test_one_bad_ticker_is_a_stored_gap_and_does_not_fail_the_quarter(snap_db, monkeypatch):
    monkeypatch.setattr(cache._prices_module, "get_prices", _prices)
    monkeypatch.setattr(cache, "is_quarter_complete", lambda q: True)
    snap, _at = cache.capture_quarter_snapshot("2026Q1")
    assert "VOO" in snap.adj_close.columns and "BADX" not in snap.adj_close.columns
    gap_tickers = {g[0] for g in snap.gaps}
    assert "BADX" in gap_tickers and "ZZZ (70%) + YYY (10%)" in gap_tickers
    # Persisted, and read back by the reader.
    back, _ = cache.get_quarter_snapshot("2026Q1")
    assert set(back.gaps) == set(snap.gaps)


def test_the_cover_discloses_the_unlocked_series():
    """The REAL cover lines, sliced from the shipped template (the full template needs
    the whole report context), rendered through the report's own environment."""
    from src.reports import TEMPLATES_DIR, _make_report_env
    src = (TEMPLATES_DIR / "quarterly_report.html").read_text(encoding="utf-8")
    start = src.index('<p class="cover-date">')
    end = src.index("{% endif %}", src.index("{% if snapshot_captured_at and snapshot_gaps %}"))
    tmpl = _make_report_env().from_string(src[start:end + len("{% endif %}")])
    html = tmpl.render(snapshot_captured_at="April 1, 2026",
                       snapshot_gaps=[("BADX", "ValueError: delisted")])
    assert "Prices locked April 1, 2026" in html
    assert "Except 1 series that could not be fetched when prices were locked (BADX)" in html
    assert "read current prices, not locked ones" in html
    clean = tmpl.render(snapshot_captured_at="April 1, 2026", snapshot_gaps=[])
    assert "could not be fetched" not in clean
