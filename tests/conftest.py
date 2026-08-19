"""Shared test fixtures — chiefly the DB-pinning fixtures.

WHY THIS EXISTS
---------------
The app resolves its mode from the repo-root .env (TRACKER_MODE=personal) so a
normal local run correctly targets the personal book — that is correct and must
not change. But it makes .env the ambient default for the WHOLE test suite too,
and mode is frozen at import: src.config._resolved_mode and src.db.DB_PATH are
both computed once when those modules load, so setting TRACKER_MODE (or the inert
DB_PATH) in os.environ AFTER import does nothing. A test that "wants demo" via
os.environ.setdefault was silently reading the personal book — the invisible
ambient state behind five contradictory baselines.

`use_demo_db` / `use_personal_db` OVERRIDE that frozen state explicitly and
restore it after the test, so a test's DB is a property of the test, not of
whatever the shell exported. TRACKER_MODE is the real knob; DB_PATH the env var
is inert and is deliberately not used here.
"""
import os
import sqlite3
import shutil
import tempfile
import warnings
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent

# ══════════════════════════════════════════════════════════════════════════════
# TRACKED-DB WRITE REDIRECT (GitHub #227)
# ══════════════════════════════════════════════════════════════════════════════
#
# A suite run WRITES the committed books. Measured 2026-08-19 by wrapping
# sqlite3.connect over a full run: 2,228 write-mode opens of a tracked DB against
# 4 read-only ones, and 1,118 mutating statements.
#
# THE PART THAT WAS INVISIBLE. Only 714 of those statements touch prices or
# dividends. The other 404 are idempotent upserts and conditional creates —
# INSERT securities (65), INSERT fund_compositions (20), INSERT accounts (6),
# UPDATE accounts, ALTER/DROP from the #232 migration, and 293
# CREATE ... IF NOT EXISTS. They leave content hashes UNMOVED, so
# tools/fingerprint_db.py reports IDENTICAL while they happen. Nine consecutive
# sessions read that as "nothing happened"; it only ever meant "nothing
# DETECTABLE happened".
#
# The sharpest instance: importing app.py runs the full personal bootstrap
# against the real data/tracker.db and reseeds securities, fund_compositions and
# accounts (app.py:27 -> src/bootstrap.py:216-245). A collection side effect
# reseeding the live book.
#
# WHAT THIS COVERS: write-mode opens of data/*.db that go through
# sqlite3.connect. That is the whole write channel for this codebase's own code
# AND for tests — get_connection alone would miss 16 direct sqlite3.connect calls
# in tests and every tools/migrate_* path.
#
# WHAT THIS DOES NOT COVER — stated because the existence of a redirect otherwise
# reads as coverage:
#
#   1. shutil.copy OF THE FILE. tests/test_attribution.py:1176,1302 reach the
#      real DB by copying the file, not by connecting, so nothing here sees them.
#      Their guards-up failure (#173) is the LIVE CANARY for exactly this class
#      and is expected to keep failing. Do not "fix" it here.
#   2. SUBPROCESS WRITES. tests/test_imports.py spawns interpreters; an
#      in-process patch cannot reach them. The census is a lower bound.
#   3. THE CSV CHANNEL. src/factors.py:628 writes tracked data/cache/ff_*.csv
#      (#167). Not a database, not visible from here.
#
# AND THE CAVEAT THAT OUTLIVES ALL THREE: this proves the tracked FILE is
# untouched. It does NOT stop the 404 non-price statements — they still execute,
# against the copy. Nothing here has eliminated a single write; it has relocated
# them.
#
# WHY tools/fingerprint_db.py STAYS. Three guards, three disjoint blind spots:
#   - this redirect       sees sqlite writes; blind to CSV and to file copies
#   - the builtins.open trap in tests/test_market_data_immutability.py sees CSV
#     and parquet writes; blind to sqlite, which opens files in C and never goes
#     through builtins.open — and its tracked set explicitly filters out *.db
#   - fingerprint_db      sees demo.db CONTENT after the fact; blind to the other
#     13 tracked files under data/, and to any write that is idempotent
# None subsumes another. Keep running the fingerprint.

_KILL_SWITCH = "TRACKER_TEST_NO_DB_REDIRECT"
_REAL_DB_MARKER = "real_tracked_db"

_TRACKED_DBS = {p.resolve() for p in (_ROOT / "data").glob("*.db")}

_real_connect = sqlite3.connect
_redirect_copies: "dict[Path, Path]" = {}
_redirect_dir: "Path | None" = None
_allow_real_db = False
_installed = False


def _redirect_disabled() -> bool:
    return bool(os.environ.get(_KILL_SWITCH))


def _classify(target, kwargs):
    """(resolved path or None, is_write_mode).

    A read-only URI must NEVER be redirected: it wants the real book and cannot
    harm it. Misclassifying one is the failure mode this whole change could
    introduce — a test silently reading a copy — so the read-only forms are
    matched explicitly and everything else is treated as writable.
    """
    s = str(target)
    if kwargs.get("uri") or s.startswith("file:"):
        head, _, query = s.partition("?")
        raw = head[5:] if head.startswith("file:") else head
        if "mode=ro" in query or "immutable=1" in query:
            try:
                return Path(raw).resolve(), False
            except (OSError, ValueError):
                return None, False
        try:
            return Path(raw).resolve(), True
        except (OSError, ValueError):
            return None, False
    try:
        return Path(s).resolve(), True   # a plain path opens read/write
    except (OSError, ValueError):
        return None, False               # ':memory:', an fd, something exotic


def _copy_for(path: Path) -> Path:
    global _redirect_dir
    if path not in _redirect_copies:
        if _redirect_dir is None:
            _redirect_dir = Path(tempfile.mkdtemp(prefix="tracked_db_redirect_"))
        dst = _redirect_dir / path.name
        # copyfile + explicit chmod, never shutil.copy: the source is read-only
        # whenever guards are up, and copy would carry that bit onto the copy and
        # reproduce #173 inside the fix.
        shutil.copyfile(path, dst)
        os.chmod(dst, 0o644)
        _redirect_copies[path] = dst
    return _redirect_copies[path]


def _redirecting_connect(target, *args, **kwargs):
    path, is_write = _classify(target, kwargs)
    if path is None or path not in _TRACKED_DBS or not is_write or _allow_real_db:
        return _real_connect(target, *args, **kwargs)
    return _real_connect(str(_copy_for(path)), *args, **kwargs)


# Installed at MODULE BODY, not in a fixture or hook, and that is deliberate: the
# #232 migration writes fire during COLLECTION (src/db.py:155-156 via a
# module-level import), before any fixture exists. A session fixture would be too
# late for the very writes that make guards-up collection impossible.
if not _installed and not _redirect_disabled():
    sqlite3.connect = _redirecting_connect
    _installed = True

if _redirect_disabled():
    warnings.warn(
        f"{_KILL_SWITCH} is set: tracked-DB write redirect is OFF. This run CAN "
        "write data/demo.db and data/tracker.db. Intended only for the A/B "
        "verification in PR #227; unset it.",
        RuntimeWarning, stacklevel=2,
    )


# ══════════════════════════════════════════════════════════════════════════════
# TRACKED-DATA CONTENT BACKSTOP (GitHub #271)
# ══════════════════════════════════════════════════════════════════════════════
#
# THE ONLY GUARD HERE WHOSE COVERAGE IS NOT A LIST OF CALL SURFACES.
#
# Every other protection patches a surface someone had to think of — the redirect
# above patches sqlite3.connect, tests/test_market_data_immutability.py patches
# builtins.open (and now io.open / os.replace / os.rename). Each covers exactly
# what was enumerated, and enumeration has already failed once: measured
# 2026-08-19, `io.open`, `pathlib.Path.write_text`/`write_bytes` and `os.replace`
# all bypassed the builtins.open trap. Path(...).write_text(...) — the most
# idiomatic file write in Python — walked straight past it.
#
# This one watches CONTENT instead, so it catches a write regardless of the
# surface used, including one nobody enumerated, and including one made by a
# SUBPROCESS that no in-process patch can reach.
#
# TWO TIERS, because the costs differ by 25x (measured over 1876 tests):
#   per-test   stat() size + mtime_ns on all 14 files      3.2 s total  -> names
#              the culprit test, which a session check cannot
#   per-session sha256 of all 14 files, start and end      44 ms total  -> the
#              authoritative check; catches a content change even if stat matched
#
# Hashing per-test would cost 82 s on a 300 s suite. Stat-ing per-session would
# name nothing. Neither tier is redundant.
#
# IT WATCHES ALL 14 `git ls-files data` ENTRIES, INCLUDING demo.db — deliberately
# wider than the write_trap set, which excludes *.db. That puts the database
# channel under a non-enumerating check too, so a subprocess DB write, or a run
# with TRACKER_TEST_NO_DB_REDIRECT set, is still caught here. demo.db is 28.3 MB
# of the 30.7 MB total, which is exactly why it can only be hashed per session.
#
# WHAT IT DOES NOT DO: it does not tell you WHICH LINE wrote, and it cannot refuse
# the write. That is write_trap's job and the reason both exist. The count is now
# three enumerating guards plus one non-enumerating backstop.

_BACKSTOP_OFF = "TRACKER_TEST_NO_DATA_BACKSTOP"


def _tracked_data_paths():
    import subprocess
    try:
        out = subprocess.run(["git", "ls-files", "data"], cwd=str(_ROOT),
                             capture_output=True, text=True, timeout=30).stdout.split()
    except Exception:
        return []
    return [p for p in (_ROOT / q for q in out if q.strip()) if p.exists()]


def _stat_snapshot(paths):
    snap = {}
    for p in paths:
        try:
            st = p.stat()
            snap[p] = (st.st_size, st.st_mtime_ns)
        except OSError:
            snap[p] = None
    return snap


def _hash_snapshot(paths):
    import hashlib
    snap = {}
    for p in paths:
        try:
            snap[p] = hashlib.sha256(p.read_bytes()).hexdigest()
        except OSError:
            snap[p] = None
    return snap


_TRACKED_DATA = _tracked_data_paths()
_stat_baseline = {}


@pytest.fixture(scope="session", autouse=True)
def _tracked_data_content_backstop():
    """Session bookend: sha256 every tracked data file before and after."""
    if os.environ.get(_BACKSTOP_OFF) or not _TRACKED_DATA:
        yield
        return
    before = _hash_snapshot(_TRACKED_DATA)
    yield
    after = _hash_snapshot(_TRACKED_DATA)
    moved = [p for p in _TRACKED_DATA if before.get(p) != after.get(p)]
    assert not moved, (
        "tracked data files CHANGED during this session: "
        f"{[str(p.relative_to(_ROOT)) for p in moved]}. Something wrote a "
        "committed input. The per-test tripwire above should name the test; if it "
        "did not, the write came from a subprocess or from outside a test."
    )


@pytest.fixture(autouse=True)
def _tracked_data_tripwire(request):
    """Per-test: stat-based, so it can name the culprit for ~1% of runtime.

    On a hit the baseline is UPDATED as well as reported, so only the test that
    actually wrote fails — otherwise every subsequent test fails against a moved
    baseline and the real culprit is buried in 1800 identical failures.
    """
    if os.environ.get(_BACKSTOP_OFF) or not _TRACKED_DATA:
        yield
        return
    global _stat_baseline
    if not _stat_baseline:
        _stat_baseline = _stat_snapshot(_TRACKED_DATA)
    yield
    after = _stat_snapshot(_TRACKED_DATA)
    moved = [p for p in _TRACKED_DATA if _stat_baseline.get(p) != after.get(p)]
    if moved:
        _stat_baseline = after
        raise AssertionError(
            f"{request.node.nodeid} wrote tracked data file(s): "
            f"{[str(p.relative_to(_ROOT)) for p in moved]}. Committed inputs are "
            "read-only to runtime code and to tests; refresh is "
            "tools/refresh_market_data.py's job."
        )


def pytest_report_header(config):
    """A kill-switch whose only signal is a CI assertion is invisible locally,
    and a local green run with protection off is what teaches someone to leave it
    set. So it announces itself in the header of every run."""
    if _redirect_disabled():
        return [
            "!" * 78,
            f"!! {_KILL_SWITCH} IS SET — tracked-DB write redirect is OFF.",
            "!! This run can write data/demo.db and data/tracker.db.",
            "!! Restore protection by unsetting it. See tests/conftest.py (#227).",
            "!" * 78,
        ]
    return [f"tracked-DB write redirect: ON ({len(_TRACKED_DBS)} book(s) protected)"]


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        f"{_REAL_DB_MARKER}: this test needs the REAL tracked database, not the "
        "per-session copy. The 2026-08-19 census found NO test that needs this; "
        "the set should stay visibly empty. Do not add without measuring.",
    )


def pytest_runtest_setup(item):
    global _allow_real_db
    _allow_real_db = item.get_closest_marker(_REAL_DB_MARKER) is not None


def pytest_runtest_teardown(item, nextitem):
    global _allow_real_db
    _allow_real_db = False


def pytest_unconfigure(config):
    global _installed
    if _installed:
        sqlite3.connect = _real_connect
        _installed = False
    if _redirect_dir is not None:
        shutil.rmtree(_redirect_dir, ignore_errors=True)


def _pin_mode(monkeypatch, mode: str) -> None:
    """Repoint every import-frozen mode/DB binding to `mode`, restored on teardown.

    src.db.DB_PATH is what get_connection() actually opens; src.config._resolved_mode
    / IS_DEMO drive is_demo()/is_write_enabled(). All three are frozen at import, so
    all three are overridden here — setting the env var alone would be a no-op.
    """
    import src.config as cfg
    import src.db as db

    db_path = _ROOT / "data" / ("demo.db" if mode == "demo" else "tracker.db")
    monkeypatch.setattr(cfg, "_resolved_mode", mode)
    monkeypatch.setattr(cfg, "IS_DEMO", mode == "demo")
    monkeypatch.setattr(db, "DB_PATH", db_path)


@pytest.fixture
def use_demo_db(monkeypatch):
    """Pin this test to the public demo book, overriding .env=personal."""
    _pin_mode(monkeypatch, "demo")
    yield


@pytest.fixture
def use_personal_db(monkeypatch):
    """Pin this test to the personal tracker book explicitly."""
    _pin_mode(monkeypatch, "personal")
    yield


@pytest.fixture
def no_ambient_db(tmp_path, monkeypatch):
    """Repoint src.db.DB_PATH at an EMPTY sqlite file for this test.

    OPT-IN, never autouse: request it in a test's signature to assert that the
    test reaches NO database. The ~50 honest DB-backed tests that legitimately
    read data are untouched — pinning this autouse would break every one of them.

    Why it exists: production scopes reads with the inline idiom
    ``f(..., account_id=get_portfolio_account_id())``. The resolver argument
    evaluates BEFORE a mock intercepts the wrapped call, so patching only the data
    function (``get_holdings_on_date`` / ``get_portfolio_value_series`` / …) does
    NOT stop the DB hit — and against the ambient DB it silently resolves to
    account 1 and the test passes, masking the coupling. Under this fixture the
    resolver instead opens an empty DB with no ``accounts`` table and RAISES,
    turning that silent coupling into a hard failure. Keep it on any unit test that
    mocks its data layer and must not touch a database, so the next instance of the
    same shape fails loudly instead of passing by accident. Pairs with patching the
    resolver at the module boundary (e.g. ``src.factors.get_portfolio_account_id``).
    """
    import src.db as db
    empty = tmp_path / "no_ambient_db.db"          # created on first connect; no schema
    monkeypatch.setattr(db, "DB_PATH", empty)
    monkeypatch.setattr(db, "_migrated_paths", set(), raising=False)
    return empty
