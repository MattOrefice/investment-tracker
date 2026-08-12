"""Tracked market-data files must never be written by runtime code.

THE GUARANTEE. Committed data files (the Ken French factor caches, the Shiller
CAPE and trailing-P/E series, the seed CSVs, the pinned HYG parquet) are inputs:
loaders read them, an explicit tool (tools/refresh_market_data.py) refreshes
them, and nothing else may write them. Before this guard, the loaders carried
auto-refresh paths that rewrote tracked files whenever a staleness window fired
— which made `git status` untrustworthy as a verification gate (a test run
could dirty the tree) and made CI's factor inputs depend on Dartmouth rather
than on the repository.

THE TRAP. ``builtins.open`` is wrapped in-process: any write-mode open whose
resolved path is a tracked data file is RECORDED and refused. Recording is the
assertion signal — several runtime paths swallow exceptions (per-sleeve
regression guards, the shiller multpl→Yale fallback chain), so a raise alone
could be silently absorbed while the attempt still proves the defect. The
tracked set is derived from ``git ls-files data`` (minus the SQLite DBs, whose
demo-mode price-cache writes are a separate, documented channel), so a future
tracked data file enrolls automatically.

THE LEGS. Real consumers first — AppTest renders of Factor Profile, Macro, and
SAA plus an in-memory PDF build, with network available, so a refresh is
genuinely attempted wherever the code still tries. Then deterministic per-loader
legs: each loader is called with ``date.today`` warped +40 days, arming every
mtime/lag staleness window regardless of wall-clock, so the mtime-gated paths
(shiller, trailing P/E) cannot dodge the proof by having young files.
"""
from __future__ import annotations

import builtins
import os
import pathlib
import shutil
import subprocess
import sys
from datetime import date, timedelta

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))


def _tracked_data_files() -> set[str]:
    """Absolute, case-normalised paths of every tracked file under data/ except
    the SQLite databases. Derived from git so future files enroll automatically."""
    out = subprocess.run(
        ["git", "ls-files", "data"],
        cwd=str(_ROOT), capture_output=True, text=True, timeout=30,
    ).stdout.splitlines()
    return {
        os.path.normcase(str((_ROOT / p).resolve()))
        for p in out
        if p.strip() and not p.endswith(".db")
    }


_WRITE_MODES = ("w", "a", "x", "+")


@pytest.fixture
def write_trap(monkeypatch):
    """Wrap builtins.open: record + refuse write-mode opens of tracked data files."""
    tracked = _tracked_data_files()
    assert tracked, "git ls-files returned no tracked data files — trap would be vacuous"
    attempts: list[tuple[str, str]] = []
    real_open = builtins.open

    def guarded_open(file, mode="r", *args, **kwargs):
        try:
            resolved = os.path.normcase(str(pathlib.Path(file).resolve()))
        except (TypeError, ValueError, OSError):
            return real_open(file, mode, *args, **kwargs)   # fd or exotic target
        if resolved in tracked and any(m in mode for m in _WRITE_MODES):
            attempts.append((resolved, mode))
            raise AssertionError(
                f"write-mode open({mode!r}) of tracked data file {resolved}"
            )
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)
    yield attempts


@pytest.fixture(scope="module")
def demo_db_copy(tmp_path_factory):
    """Pin the module's renders to a throwaway copy of demo.db so the guard test
    itself never writes the committed databases (price fetches land on the copy)."""
    import src.config as cfg
    import src.db as db

    tmp = tmp_path_factory.mktemp("guard_db") / "demo.db"
    shutil.copyfile(_ROOT / "data" / "demo.db", tmp)
    os.chmod(tmp, 0o666)
    saved = (cfg._resolved_mode, cfg.IS_DEMO, db.DB_PATH)
    cfg._resolved_mode, cfg.IS_DEMO, db.DB_PATH = "demo", True, tmp
    yield tmp
    cfg._resolved_mode, cfg.IS_DEMO, db.DB_PATH = saved


def _assert_no_attempts(attempts, context):
    assert not attempts, (
        f"{context} attempted to WRITE tracked data file(s) — runtime code must "
        f"never write committed inputs (refresh belongs to "
        f"tools/refresh_market_data.py): {attempts[:4]}"
    )


# ── Real-consumer legs ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("page", [
    "pages/4_Factor_Profile.py",
    "pages/3_Macro.py",
    "pages/1_SAA.py",
])
def test_page_render_writes_no_tracked_data(page, demo_db_copy, write_trap):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(page, default_timeout=300)
    at.run()
    _assert_no_attempts(write_trap, f"rendering {page}")


def test_pdf_build_writes_no_tracked_data(demo_db_copy, write_trap):
    from src.reports import generate_quarterly_report_bytes

    try:
        out = generate_quarterly_report_bytes("2025-05-01", "2026-06-30", is_demo=True)
        assert isinstance(out, bytes) and out, "PDF build returned no bytes"
    except Exception:
        # A build failure is a different defect; the guarantee under test is
        # only that no tracked file was written along the way.
        pass
    _assert_no_attempts(write_trap, "building the quarterly PDF")


# ── Deterministic per-loader legs (staleness windows armed via time warp) ──────

class _WarpedDate(date):
    """date.today() shifted +40 days — arms every 7/30/35-day staleness window."""

    @classmethod
    def today(cls):
        return date.today() + timedelta(days=40)


_LOADER_LEGS = {
    "ff_factors_us": ("src.factors", lambda m: m.load_factors("us")),
    "ff_factors_developed_exus": ("src.factors", lambda m: m.load_factors("developed_exus")),
    "ff_umd_us": ("src.factors", lambda m: m.load_umd_factor()),
    "shiller_cape": ("src.shiller", lambda m: m.get_cape_series()),
    "trailing_pe": ("src.trailing_pe", lambda m: m.get_trailing_pe()),
}


@pytest.mark.parametrize("key", sorted(_LOADER_LEGS))
def test_loader_writes_no_tracked_data_even_when_stale(key, write_trap, monkeypatch):
    import importlib

    mod_name, call = _LOADER_LEGS[key]
    mod = importlib.import_module(mod_name)
    monkeypatch.setattr(mod, "date", _WarpedDate)
    try:
        call(mod)
    except Exception:
        # Fallback chains may absorb or re-raise; the recorded attempts decide.
        pass
    _assert_no_attempts(write_trap, f"loader leg {key!r} (today warped +40d)")


def test_trap_set_covers_the_refresh_family():
    """Sanity: the git-derived trap set contains the five tool-refreshed files
    and the pinned parquet — if these ever leave tracking, the guard above
    guards nothing for them and this fails loudly."""
    tracked = _tracked_data_files()
    expected = [
        _ROOT / "data" / "cache" / "ff_factors_us.csv",
        _ROOT / "data" / "cache" / "ff_factors_developed_exus.csv",
        _ROOT / "data" / "cache" / "ff_umd_us.csv",
        _ROOT / "data" / "cache" / "prices_hyg.parquet",
        _ROOT / "data" / "shiller_cape.csv",
        _ROOT / "data" / "trailing_pe.csv",
    ]
    missing = [str(p) for p in expected if os.path.normcase(str(p.resolve())) not in tracked]
    assert not missing, f"expected tracked data files absent from git: {missing}"
