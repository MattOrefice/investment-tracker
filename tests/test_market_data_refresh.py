"""Frontier helpers, staleness surface, read-only loaders, and the refresh
tool's three-state reporting.

Expectations are literals and injected values — never the modules' own
constants read back — so no test here restates its implementation.
"""
from __future__ import annotations

import pathlib
import sys
from datetime import date, timedelta

import pandas as pd
import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from src.asof import staleness_note  # noqa: E402


# ── staleness_note ─────────────────────────────────────────────────────────────

def test_staleness_note_fresh_returns_none():
    assert staleness_note("X", date.today() - timedelta(days=10), 45) is None


def test_staleness_note_at_threshold_is_still_fresh():
    assert staleness_note("X", date.today() - timedelta(days=45), 45) is None


def test_staleness_note_fires_with_day_count_and_date():
    frontier = date.today() - timedelta(days=133)
    note = staleness_note("Ken French factor", frontier, 70)
    assert note is not None
    assert frontier.isoformat() in note
    assert "133 days behind" in note
    assert "tools/refresh_market_data.py" in note


def test_staleness_note_missing_frontier_is_loud_not_none():
    """Absence must never present as freshness."""
    note = staleness_note("Trailing P/E", None, 45)
    assert note is not None and "missing or unreadable" in note


# ── frontier helpers read the DATA, not mtime ─────────────────────────────────

def test_factor_frontier_reads_last_data_row(tmp_path, monkeypatch):
    import src.factors as factors

    csv = tmp_path / "ff.csv"
    pd.DataFrame(
        {"Mkt-RF": [0.01, 0.02], "RF": [0.0, 0.0]},
        index=pd.to_datetime(["2026-01-05", "2026-02-06"]),
    ).to_csv(csv)
    monkeypatch.setitem(factors._FACTOR_CONFIG, "us", {"cache": csv, "url": "unused"})
    assert factors.factor_frontier("us") == date(2026, 2, 6)


def test_cape_frontier_reads_last_data_row(tmp_path, monkeypatch):
    import src.shiller as shiller

    csv = tmp_path / "cape.csv"
    pd.DataFrame(
        {"date": ["2026-04-01", "2026-05-01"], "cape": [39.1, 40.9]}
    ).to_csv(csv, index=False)
    monkeypatch.setattr(shiller, "_CACHE_CSV", csv)
    assert shiller.cape_frontier() == date(2026, 5, 1)


def test_trailing_pe_frontier_uses_month_column(tmp_path, monkeypatch):
    import src.trailing_pe as tpe

    csv = tmp_path / "pe.csv"
    pd.DataFrame({
        "date": ["2026-06-01", "2026-07-01"],
        "pe": [28.1, 28.9],
        "obs_date": ["2026-06-15", "2026-07-21"],
    }).to_csv(csv, index=False)
    monkeypatch.setattr(tpe, "_CACHE_CSV", csv)
    # Month-start basis (matches cape_frontier), NOT the fresher obs_date.
    assert tpe.trailing_pe_frontier() == date(2026, 7, 1)


# ── loaders are read-only and fail loud on a missing committed file ───────────

@pytest.mark.parametrize("setup", [
    ("factors", "us"),
    ("shiller", None),
    ("trailing_pe", None),
])
def test_loader_raises_pointing_at_the_tool_when_file_missing(setup, tmp_path, monkeypatch):
    kind, region = setup
    missing = tmp_path / "absent.csv"
    if kind == "factors":
        import src.factors as m
        monkeypatch.setitem(m._FACTOR_CONFIG, "us", {"cache": missing, "url": "unused"})
        call = lambda: m.load_factors(region)
    elif kind == "shiller":
        import src.shiller as m
        monkeypatch.setattr(m, "_CACHE_CSV", missing)
        call = m.get_cape_series
    else:
        import src.trailing_pe as m
        monkeypatch.setattr(m, "_CACHE_CSV", missing)
        call = m.get_trailing_pe
    with pytest.raises(FileNotFoundError, match="refresh_market_data"):
        call()


# ── the tool's three distinct outcomes ────────────────────────────────────────

def _fake_target(tmp_path, fetch, name="t.csv"):
    path = tmp_path / name
    frame = pd.DataFrame({"date": ["2026-08-01"], "v": [1.0]})

    def write(df):
        df.to_csv(path, index=False)

    def frontier():
        return date(2026, 8, 1) if path.exists() else None

    return path, (path, fetch or (lambda: frame), write, frontier), frame


def test_tool_reports_refreshed_on_success(tmp_path, monkeypatch, capsys):
    import tools.refresh_market_data as tool

    path, target, _ = _fake_target(tmp_path, None)
    monkeypatch.setattr(tool, "_TARGETS", {"fake": target})
    failures = tool.refresh(["fake"])
    out = capsys.readouterr().out
    assert failures == 0
    assert "REFRESHED" in out and path.exists()


def test_tool_reports_fetch_failed_distinctly(tmp_path, monkeypatch, capsys):
    import tools.refresh_market_data as tool

    def boom():
        raise ConnectionError("dartmouth down")

    path, target, _ = _fake_target(tmp_path, boom)
    monkeypatch.setattr(tool, "_TARGETS", {"fake": target})
    failures = tool.refresh(["fake"])
    out = capsys.readouterr().out
    assert failures == 1
    assert "FETCH FAILED" in out and "file untouched" in out
    assert "WRITE FAILED" not in out
    assert not path.exists()


def test_tool_reports_write_failed_without_masking_the_successful_fetch(
    tmp_path, monkeypatch, capsys
):
    """The defect the old in-loader refresh had: a blocked write reported
    'refresh failed ... using cached data' and silently discarded the fetched
    frame. The tool must say the fetch SUCCEEDED, what was fetched, and that
    the write — a local problem — is what failed."""
    import tools.refresh_market_data as tool

    path, (p, fetch, _write, frontier), frame = _fake_target(tmp_path, None)

    def blocked_write(df):
        raise PermissionError("read-only filesystem")

    monkeypatch.setattr(tool, "_TARGETS", {"fake": (p, fetch, blocked_write, frontier)})
    failures = tool.refresh(["fake"])
    out = capsys.readouterr().out
    assert failures == 1
    assert "WRITE FAILED" in out
    assert "fetched 1 rows through 2026-08-01 successfully" in out
    assert "could not write" in out
    assert "FETCH FAILED" not in out
