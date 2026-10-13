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
legs: each loader is called with the clock it reads, ``date.today``, warped +40
days. Five of the six read no clock any more: their staleness windows went with
the in-loader refresh, so for them the warp arms nothing and the leg checks that a
plain call writes no tracked file. One window is left, the BE/ME breakpoints'
30-day one, whose cache is not a tracked file. The warp sends that loader to its
fetch, and the leg asserts it did (#497: the warp used to be installed in three
modules, armed nothing in any leg, and nothing said so).
"""
from __future__ import annotations

import builtins
import io
import os
import pathlib
import shutil
import subprocess
import sys
import warnings
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
    """Record + refuse write-mode opens of tracked data files.

    FOUR SURFACES, NOT ONE. This wrapped only ``builtins.open`` until #271, and
    measurement showed three common idioms walked straight past it:

        io.open(path, "a")            -> io.open       MISSED
        Path(path).write_text(...)    -> io.open       MISSED
        os.replace(src, path)         -> os.replace    MISSED
        open / to_csv / to_parquet    -> builtins.open caught

    ``builtins.open`` and ``io.open`` are the SAME function object but SEPARATE
    NAME BINDINGS, so patching one leaves the other untouched — and ``pathlib``
    reaches the latter. That made the most idiomatic file write in Python
    invisible here.

    A prediction that was wrong in the useful direction, kept as a warning against
    reasoning about this from plausibility: ``to_parquet`` was expected to be the
    gap, on the theory that pyarrow opens files in C. It does not — it routes
    through ``builtins.open`` and was always caught. The NATIVE path went through
    the trap; the plain-Python idioms did not.

    THIS LIST IS STILL AN ENUMERATION and will go stale the same way. It buys
    attribution — the culprit line, at the moment of the write, refused — not
    coverage. Coverage comes from the content backstop in tests/conftest.py,
    which watches bytes rather than call surfaces. Do not read this widening as
    having closed the class.
    """
    tracked = _tracked_data_files()
    assert tracked, "git ls-files returned no tracked data files — trap would be vacuous"
    attempts: list[tuple[str, str]] = []
    real_open = builtins.open
    real_io_open = io.open
    real_replace = os.replace
    real_rename = os.rename

    def _hit(resolved, how):
        attempts.append((resolved, how))
        raise AssertionError(f"write-mode {how} of tracked data file {resolved}")

    def _resolve(target):
        try:
            return os.path.normcase(str(pathlib.Path(target).resolve()))
        except (TypeError, ValueError, OSError):
            return None                      # fd or exotic target

    def _make_open(real, label):
        def guarded(file, mode="r", *args, **kwargs):
            resolved = _resolve(file)
            if resolved in tracked and any(m in str(mode) for m in _WRITE_MODES):
                _hit(resolved, f"{label}({mode!r})")
            return real(file, mode, *args, **kwargs)
        return guarded

    def _make_move(real, label):
        def guarded(src, dst, *args, **kwargs):
            resolved = _resolve(dst)
            if resolved in tracked:
                _hit(resolved, f"{label}(dst)")
            return real(src, dst, *args, **kwargs)
        return guarded

    monkeypatch.setattr(builtins, "open", _make_open(real_open, "builtins.open"))
    monkeypatch.setattr(io, "open", _make_open(real_io_open, "io.open"))
    monkeypatch.setattr(os, "replace", _make_move(real_replace, "os.replace"))
    monkeypatch.setattr(os, "rename", _make_move(real_rename, "os.rename"))
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


# ── Deterministic per-loader legs (the clock each loader reads, warped) ────────

class _WarpedDate(date):
    """date.today() shifted +40 days: past the one staleness window a loader still
    has, the BE/ME breakpoints' 30 days (factors._BEME_REFRESH_DAYS)."""

    @classmethod
    def today(cls):
        return date.today() + timedelta(days=40)


_LOADER_LEGS = {
    "ff_factors_us": ("src.factors", lambda m: m.load_factors("us")),
    "ff_factors_developed_exus": ("src.factors", lambda m: m.load_factors("developed_exus")),
    "ff_umd_us": ("src.factors", lambda m: m.load_umd_factor()),
    "ff_beme_breakpoints": ("src.factors", lambda m: m.load_beme_breakpoints()),
    "shiller_cape": ("src.shiller", lambda m: m.get_cape_series()),
    "trailing_pe": ("src.trailing_pe", lambda m: m.get_trailing_pe()),
}
# The fetch a staleness branch in each module would call. A leg stubs them all, so it
# never reaches the network and a call to one is what "went down a staleness branch"
# means here.
_FETCHERS = {
    "src.factors": ("_fetch_factors", "_fetch_umd", "_fetch_beme_breakpoints"),
    "src.shiller": ("fetch_cape_dataframe",),
    "src.trailing_pe": ("fetch_trailing_pe_dataframe",),
}
# The legs whose loader still has a staleness window for the warp to arm. The other
# five read no clock: counted over each leg, the only clock read in these modules is
# load_beme_breakpoints' (#497).
_HAS_STALENESS_WINDOW = {"ff_beme_breakpoints"}


def _run_leg(key, monkeypatch, tmp_path, *, warped: bool) -> "tuple[list[str], pathlib.Path | None]":
    """Call one loader with every fetch in its module stubbed, and return (the fetches
    it made, its staged cache or None). The BE/ME loader gets a cache written now, so
    it is fresh by the real clock and stale only under the warp; its stubbed fetch
    answers, so the refresh goes through to its write."""
    import importlib

    import pandas as pd

    mod_name, call = _LOADER_LEGS[key]
    mod = importlib.import_module(mod_name)
    fetched: list[str] = []

    def _stub(name):
        def fetch(*_a, **_k):
            fetched.append(name)
            if name == "_fetch_beme_breakpoints":
                return pd.DataFrame({"p50": [0.75]}, index=pd.to_datetime(["2026-06-30"]))
            raise OSError("a loader leg never reaches the network")
        return fetch

    for name in _FETCHERS[mod_name]:
        assert callable(getattr(mod, name)), f"premise: {mod_name}.{name} is the fetch"
        monkeypatch.setattr(mod, name, _stub(name))
    staged = None
    if key == "ff_beme_breakpoints":
        staged = tmp_path / f"ff_beme_breakpoints_{'warped' if warped else 'real'}.csv"
        pd.DataFrame({"p50": [0.5]}, index=pd.to_datetime(["2025-06-30"])).to_csv(staged)
        monkeypatch.setattr(mod, "_BEME_CACHE", staged)
    if warped:
        monkeypatch.setattr(mod, "date", _WarpedDate)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            call(mod)
        except Exception:
            # Fallback chains may absorb or re-raise; the recorded attempts decide.
            pass
    return fetched, staged


@pytest.mark.parametrize("key", sorted(_LOADER_LEGS))
def test_loader_writes_no_tracked_data_even_when_stale(key, write_trap, monkeypatch, tmp_path):
    """Each loader with the clock it reads 40 days on. A loader with a staleness window
    must be sent to its fetch by the warp, or the leg arms nothing; one without must
    fetch nothing. Either way it writes no tracked file."""
    fetched, _staged = _run_leg(key, monkeypatch, tmp_path, warped=True)
    if key in _HAS_STALENESS_WINDOW:
        assert fetched, f"the warp did not send {key!r} down its staleness branch"
    else:
        assert fetched == [], (
            f"{key!r} fetched under the warp ({fetched}): its loader has a staleness "
            f"window again. Add it to _HAS_STALENESS_WINDOW, so the leg is known armed.")
    _assert_no_attempts(write_trap, f"loader leg {key!r} (today warped +40d)")


def test_the_warp_is_what_sends_a_loader_down_its_staleness_branch(write_trap, monkeypatch,
                                                                   tmp_path):
    """At least one leg is armed, and by the warp: on the real clock the same loader
    reads its fresh cache and fetches nothing; warped, it fetches and rewrites that
    cache. The file it writes is not a tracked one."""
    import src.factors as factors

    assert _HAS_STALENESS_WINDOW == {"ff_beme_breakpoints"}
    real_cache = factors._BEME_CACHE
    assert os.path.normcase(str(real_cache.resolve())) not in _tracked_data_files(), (
        "the one file a stale loader still writes has become a tracked file")

    fetched, staged = _run_leg("ff_beme_breakpoints", monkeypatch, tmp_path, warped=False)
    assert fetched == [] and "0.5" in staged.read_text(), "fresh by the real clock: read, not fetched"

    fetched, staged = _run_leg("ff_beme_breakpoints", monkeypatch, tmp_path, warped=True)
    assert fetched == ["_fetch_beme_breakpoints"]
    assert "0.75" in staged.read_text(), "the refresh went through to its write"
    _assert_no_attempts(write_trap, "the BE/ME loader, stale")


def test_trap_set_covers_the_refresh_family():
    """Sanity: the git-derived trap set contains the five tool-refreshed files —
    if these ever leave tracking, the guard above guards nothing for them and this
    fails loudly. The pinned HYG parquet was listed here until #386 retired it:
    HYG is read from the price layer now, and no tracked file carries it."""
    tracked = _tracked_data_files()
    expected = [
        _ROOT / "data" / "cache" / "ff_factors_us.csv",
        _ROOT / "data" / "cache" / "ff_factors_developed_exus.csv",
        _ROOT / "data" / "cache" / "ff_umd_us.csv",
        _ROOT / "data" / "shiller_cape.csv",
        _ROOT / "data" / "trailing_pe.csv",
    ]
    missing = [str(p) for p in expected if os.path.normcase(str(p.resolve())) not in tracked]
    assert not missing, f"expected tracked data files absent from git: {missing}"
