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
import sys
import shutil
import tempfile
import threading
import time
import warnings
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent

# The demo app's runtime cache (src.db.runtime_cache_path) defaults to a file in the
# OS temp directory, shared with any local demo run. A test that runs app.py, in
# process or in a subprocess (which inherits this environment), must neither read
# what a local run fetched nor leave its own writes there: one private file per
# session instead (#368).
os.environ["DEMO_RUNTIME_CACHE"] = str(
    Path(tempfile.mkdtemp(prefix="demo-runtime-cache-")) / "demo_runtime_cache.db")
# And the demo's daily price refresh never runs in the suite: tests keep reading
# fixed data (#368 item 3). A test of the refresh turns it on for itself.
os.environ["DEMO_DAILY_FETCH"] = "0"
# Nor does the Macro page's background FRED warm (audit item 15f). A test that turned
# the fetch timer on for the price refresh used to start it too, and the thread outlived
# that test by 17 more, running their migrations against their own databases (#429).
# The warm's own tests turn it on for themselves.
os.environ["DEMO_FRED_WARM"] = "0"
# Nor does a refresh ask the provider again for closes a book lacks (#501): the demo's
# refresh and a personal page load would each fetch for whatever book a test points at,
# and write into it. Its own tests turn it on for themselves.
os.environ["PRICE_GAP_REASK"] = "0"

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


# LEAK DETECTION, DETERMINISTIC (#340). Every connection the redirect opens is held
# here by a STRONG reference, with the phase it was opened in and the test that
# opened it. Before this, a leaked connection was caught only if garbage collection
# had not yet closed it by session end: a short run reported test_seed's leak and
# the full suite, where GC ran first, reported clean. Python 3.11's sqlite3 emits no
# ResourceWarning for an unclosed connection (that arrived in 3.13), so there is no
# warning to promote to an error. Holding the reference means GC cannot close the
# leak before it is checked. Never gc.collect() before a check: that closes the leak
# and hides it.
_open_redirected: "list[tuple[sqlite3.Connection, str, str]]" = []
_phase = "collection"
_current_test = "<collection>"


def _is_open(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("SELECT 1")
        return True
    except sqlite3.ProgrammingError:
        return False


def _redirecting_connect(target, *args, **kwargs):
    path, is_write = _classify(target, kwargs)
    if path is None or path not in _TRACKED_DBS or not is_write or _allow_real_db:
        return _real_connect(target, *args, **kwargs)
    conn = _real_connect(str(_copy_for(path)), *args, **kwargs)
    _open_redirected.append((conn, _phase, _current_test))
    return conn


def _take_leaks(select) -> "list[str]":
    """Close and return every still-open registered connection that `select` picks,
    and drop every closed one, so the registry stays small over a full run."""
    leaks, keep = [], []
    for conn, phase, test in _open_redirected:
        if not _is_open(conn):
            continue
        if select(phase, test):
            leaks.append(f"{test} ({phase})")
            conn.close()
        else:
            keep.append((conn, phase, test))
    _open_redirected[:] = keep
    return leaks


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
def _no_live_marks_carried_between_tests(monkeypatch):
    """Each test starts with an empty record of the open-session bars served.

    src.prices keeps that record per process (_LIVE_MARKS), as a Streamlit server
    needs: the banner reads it to say a current value includes today's unsettled
    price (#160, audit item 4i). In a test process it carried a bar one test served
    into every later banner. 19 banner tests went red in the full suite and none
    alone, so the reset is autouse: a test that forgets it cannot leak."""
    import src.prices as prices
    monkeypatch.setattr(prices, "_LIVE_MARKS", {})


@pytest.fixture(autouse=True)
def _no_gap_fetch_failures_carried_between_tests(monkeypatch):
    """Each test starts with no failed gap fetch on record (#637).

    src.prices keeps that record per process, as the banner and the half-hour retry
    need. In a test process one test's blocked fetch would skip a later test's fetch of
    the same window and reach its banner, so the reset is autouse, as the live marks'
    is above."""
    import src.prices as prices
    monkeypatch.setattr(prices, "_GAP_FAILURES", {})


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


# ══════════════════════════════════════════════════════════════════════════════
# NO THREAD OUTLIVES THE TEST THAT STARTED IT (#429)
# ══════════════════════════════════════════════════════════════════════════════
#
# A thread left running reads module globals that later tests repoint. The FRED warm
# one test started was still alive 17 tests later; between series it opened
# get_connection(), which reads db.DB_PATH at call time, so it ran _auto_migrate on
# THAT test's fresh database while the test ran its own. One full run failed with
# "duplicate column name".
#
# So a thread alive after a test's teardown that was not alive before its setup fails
# that test, with no grace period: a thread that happens to finish quickly has still
# outlived its test, and whether it finishes quickly depends on the machine (without a
# FRED key the warm fails fast and ends within half a second; with one, on #429's
# local run, it was still alive 17 tests later). The thread is then joined, only so the failure cannot run into the next
# test. Measured 2026-09-27 over the CI-style suite (2629 tests): 4 tests left a thread
# alive at teardown, 3 of them the PDF's chart render below and 1 the warm.
#
# ONE EXCEPTION to the no-grace-period rule (decided by the owner on October 17, 2026,
# #635): a threading.Timer already cancelled when it is reported, its finished event
# set, is joined and fails only if it is still alive after the join. A cancelled Timer
# can never start its function, so it is not the hazard this check exists for. What is
# left of it is a wait that has already returned, and it ends at once on any machine.
# Streamlit's st.spinner, which st.cache_data shows by default, starts one and cancels
# it on exit. A page render under AppTest left one alive in the instant before it ended,
# and failed CI on a test whose body passed (#635). Whether it is cancelled is read when
# it is reported, before the join, so a Timer never cancelled fails as any thread does,
# even one that fires and ends within the join. A Timer cancelled while its function
# runs is joined like the rest and fails if the join does not end it. thread_target
# names a Timer by the function it schedules.
#
# Allowed to outlive, each for a stated reason. Anything else fails by default, so the
# next background thread is caught without anyone having to list it first.
THREADS_ALLOWED_TO_OUTLIVE = {
    # The PDF's chart render gives up on kaleido after 25 s and abandons the thread by
    # design (a hung kaleido on Windows). It holds the figure and its own result list,
    # nothing else: no database, no module state.
    "src.reports._render_chart_to_png.<locals>._render":
        "abandoned after its 25 s join when kaleido hangs; touches no shared state",
    # kaleido 0.2.1's stderr reader (kaleido.scopes.base.BaseScope._collect_standard_error)
    # was allowed here too. kaleido 1.5.0 (#567) starts no thread that outlives an
    # export: the four tests that render a chart left none, so it is not listed.
}
_THREAD_JOIN_SECONDS = 5.0
_threads_before: "set[threading.Thread]" = set()


def thread_target(t: threading.Thread) -> str:
    """What a thread runs: its target, or for a Timer the function it schedules. Read
    it before a join: a finished thread no longer holds its target."""
    if isinstance(t, threading.Timer):
        fn = getattr(t, "function", None)
    else:
        fn = getattr(t, "_target", None)
    if fn is None:
        return f"{type(t).__module__}.{type(t).__qualname__}"
    return f"{getattr(fn, '__module__', '?')}.{getattr(fn, '__qualname__', repr(fn))}"


def threads_outliving(before: "set[threading.Thread]") -> "list[threading.Thread]":
    """Threads alive now that were not in ``before`` and are not allowed to outlive."""
    return [t for t in threading.enumerate()
            if t not in before and t.is_alive()
            and thread_target(t) not in THREADS_ALLOWED_TO_OUTLIVE]


def join_for_cleanup(threads: "list[threading.Thread]",
                     seconds: float = _THREAD_JOIN_SECONDS) -> None:
    deadline = time.monotonic() + seconds
    for t in threads:
        t.join(max(0.0, deadline - time.monotonic()))


def cancelled_timer(t: threading.Thread) -> bool:
    """A threading.Timer whose finished event is set: it can never start its function."""
    finished = getattr(t, "finished", None)
    return isinstance(t, threading.Timer) and finished is not None and finished.is_set()


def fails_after_join(alive: "list[threading.Thread]") -> "list[threading.Thread]":
    """Of the threads threads_outliving reported, the ones that fail the test. Each is
    joined first, so a failure cannot run into the next test. Every one fails, however
    soon it ends, except a Timer cancelled when reported that has ended by the end of its
    join (#635)."""
    cancelled = [t for t in alive if cancelled_timer(t)]
    join_for_cleanup(cancelled + [t for t in alive if t not in cancelled])
    return [t for t in alive if t not in cancelled or t.is_alive()]


def pytest_runtest_setup(item):
    global _allow_real_db, _phase, _current_test, _threads_before
    _allow_real_db = item.get_closest_marker(_REAL_DB_MARKER) is not None
    _phase, _current_test = "setup", item.nodeid
    _threads_before = set(threading.enumerate())


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    global _phase
    _phase = "call"
    yield
    _phase = "teardown"


_LEAK_HEADLINE = "connection to a tracked-book copy left open (#340)"


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown(item, nextitem):
    global _allow_real_db, _phase
    yield
    _allow_real_db = False
    _phase = "between"
    # A connection this test's BODY opened must be closed by the end of its own
    # teardown. Setup-phase opens are exempt here (a module- or session-scoped
    # fixture can legitimately hold one across tests); those are checked at
    # session end instead.
    leaks = _take_leaks(lambda phase, test: phase == "call" and test == item.nodeid)
    if leaks:
        raise AssertionError(
            f"{_LEAK_HEADLINE}: {item.nodeid} opened {len(leaks)} connection(s) to a "
            "redirected tracked-DB copy and never closed them. `with sqlite3.connect(...)"
            " as c:` commits but does NOT close; use contextlib.closing or get_connection.")
    alive = threads_outliving(_threads_before)
    named = {t: (t.name, thread_target(t)) for t in alive}
    failing = fails_after_join(alive)
    if failing:
        raise AssertionError(
            f"{item.nodeid} left {len(failing)} thread(s) running past its teardown: "
            f"{[named[t] for t in failing]}. A thread that outlives its test reads "
            "globals later tests repoint (db.DB_PATH) and acts on their databases "
            "(#429). Join it before the test ends, or keep the test from starting it.")


def remove_tree_reporting(path: Path) -> "list[str]":
    """Remove `path`, returning one line per entry that COULD NOT be removed.

    Replaces `rmtree(ignore_errors=True)`, which is how the redirect's copies went
    silently: on Windows a sqlite connection still open at session end blocks the
    delete, the error was swallowed, and copies of the personal book piled up in
    TEMP with nothing reporting it (#307). A failure here is a LEAK — a connection
    to a tracked-book copy outlived the tests — so it is returned for the caller to
    print, never hidden.
    """
    failures: "list[str]" = []

    def _record(func, p, exc_info):
        err = exc_info[1]
        failures.append(f"{p}: {type(err).__name__}: {err}")

    if path.exists():
        shutil.rmtree(path, onerror=_record)
    return failures


def _cleanup_redirect_dir() -> "list[str]":
    global _redirect_dir
    if _redirect_dir is None:
        return []
    failures = remove_tree_reporting(_redirect_dir)
    if not failures:
        _redirect_dir = None
    return failures


_CLEANUP_HEADLINE = "tracked-DB redirect cleanup FAILED — a connection leaked (#307)"
_CLEANUP_EXPLAIN = (
    "These per-session copies hold TRACKED DATA (possibly the personal book) and "
    "could not be removed, so nothing else will remove them. On Windows this means "
    "a sqlite connection to the copy was still open when the session ended."
)


def pytest_collection_finish(session):
    """Run each collected module's one-time capture AFTER collection (so importing a
    test module reads no DB) and BEFORE the first test (so no test's fetch can move
    what is captured). #166: tests/test_attribution.py's holdings frontier."""
    seen = set()
    for item in session.items:
        mod = getattr(item, "module", None)
        capture = getattr(mod, "_capture_holdings_frontier", None)
        if capture is not None and id(mod) not in seen:
            seen.add(id(mod))
            capture()


_session_leaks: "list[str]" = []


def pytest_sessionfinish(session, exitstatus):
    """Session-end leak check for connections opened OUTSIDE a test body (collection,
    fixture setup): still open now means nothing will close them. Fails the run."""
    _session_leaks.extend(_take_leaks(lambda phase, test: True))
    if _session_leaks and session.exitstatus == 0:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Clean up HERE rather than only in pytest_unconfigure, because this is the
    last point where a failure can still be printed where the reader looks."""
    if _session_leaks:
        terminalreporter.write_sep("!", _LEAK_HEADLINE + " — at session end", red=True)
        for line in _session_leaks:
            terminalreporter.write_line(f"  opened by {line}", red=True)
    failures = _cleanup_redirect_dir()
    if failures:
        terminalreporter.write_sep("!", _CLEANUP_HEADLINE, red=True)
        terminalreporter.write_line(_CLEANUP_EXPLAIN, red=True)
        for line in failures:
            terminalreporter.write_line(f"  {line}", red=True)


def pytest_unconfigure(config):
    global _installed
    if _installed:
        sqlite3.connect = _real_connect
        _installed = False
    # Backstop for a run whose terminal summary never ran: still never silent.
    failures = _cleanup_redirect_dir()
    if failures:
        print(f"\n{_CLEANUP_HEADLINE}\n{_CLEANUP_EXPLAIN}\n  " + "\n  ".join(failures),
              file=sys.__stderr__)


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


# ── What the suite spends its time on (#544) ──────────────────────────────────
# The live pages wait for FRED at most macro.PAGE_WAIT_SECONDS, 15 s. The tests that hold
# FRED until a page gives up waited out the whole 15 s each, 67 s of the CI step's 786 s
# (#544's measurement). They run at this wait instead, and read every bound and every
# message from it, so they prove the same rule at the length the live page does not use.
FRED_TEST_WAIT = 0.5


@pytest.fixture
def short_fred_wait(monkeypatch):
    """macro.PAGE_WAIT_SECONDS at FRED_TEST_WAIT for this test (#544)."""
    import src.macro
    monkeypatch.setattr(src.macro, "PAGE_WAIT_SECONDS", FRED_TEST_WAIT)
    return FRED_TEST_WAIT


def block_network(mp: "pytest.MonkeyPatch") -> None:
    """No network for the rest of this test: every DNS lookup, price fetch and FRED fetch
    raises at once (#544). A real PDF build otherwise reached the network for something
    other than FRED: blocking DNS cut the two PDF-build tests from 14.8 and 9.6 s to
    5.6 and 3.9 s on GitHub's runner, where stubbing FRED alone changed little."""
    import socket
    import src.macro
    import src.prices

    def _offline(*_a, **_k):
        raise OSError("network blocked: this test reads only the book it is given (#544)")

    mp.setattr(socket, "getaddrinfo", _offline)
    mp.setattr(src.prices._SESSION, "get", _offline)
    mp.setattr(src.macro, "fetch_fred_series", _offline)


# The one file in the checkout the suite could write: factors.load_beme_breakpoints
# refreshes data/cache/ff_beme_breakpoints.csv (gitignored) when it is absent or older
# than 30 days. Parallel runs would share it (#544). Tests write a temporary copy instead.
BEME_CACHE_IN_CHECKOUT = _ROOT / "data" / "cache" / "ff_beme_breakpoints.csv"


def _stat_or_none(path: Path):
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_size, st.st_mtime_ns)


@pytest.fixture(scope="session", autouse=True)
def _beme_cache_outside_the_checkout(tmp_path_factory):
    """Point factors._BEME_CACHE at a temporary copy for the whole session, with the
    checkout file's modification time so its staleness is the same, and fail the
    session if the checkout's file changed anyway."""
    import src.factors as factors
    assert Path(factors._BEME_CACHE).resolve() == BEME_CACHE_IN_CHECKOUT.resolve(), (
        "premise: the loader's cache is the checkout file this redirects")
    copy = tmp_path_factory.mktemp("beme_cache") / BEME_CACHE_IN_CHECKOUT.name
    if BEME_CACHE_IN_CHECKOUT.exists():
        shutil.copy2(BEME_CACHE_IN_CHECKOUT, copy)
        os.chmod(copy, 0o644)
    before = _stat_or_none(BEME_CACHE_IN_CHECKOUT)
    mp = pytest.MonkeyPatch()
    mp.setattr(factors, "_BEME_CACHE", copy)
    try:
        yield copy
    finally:
        mp.undo()
    assert _stat_or_none(BEME_CACHE_IN_CHECKOUT) == before, (
        f"the suite wrote {BEME_CACHE_IN_CHECKOUT}: tests write a temporary copy (#544)")


# ── The FROZEN TEST BOOK (#302, test half) ─────────────────────────────────────
# tests/fixtures/frozen_book.db, built by tools/build_frozen_book.py from the PUBLIC
# demo.db (never tracker.db), in #304's format, trimmed, with nothing date-keyed.
# Render tests that used to copy the owner's real book use this instead, so they
# run in CI and do not depend on the real book or on how stale its prices are.
# ALWAYS through a copy, with the network blocked: a live fetch would append
# today's prices and make the book depend on the date, and an in-place open would
# mutate a committed fixture.
FROZEN_BOOK = _ROOT / "tests" / "fixtures" / "frozen_book.db"


def point_at_frozen_book(mp: "pytest.MonkeyPatch", tmp_dir: Path) -> Path:
    """Copy the frozen book into ``tmp_dir``, point src.db at the copy, and block
    the network, FRED included. Returns the copy's path."""
    import socket
    import src.db
    import src.macro
    import src.prices
    copy = tmp_dir / "frozen_book.db"
    shutil.copyfile(FROZEN_BOOK, copy)
    os.chmod(copy, 0o644)

    def _offline(*_a, **_k):
        raise OSError("network blocked: the frozen book is offline by design (#302)")

    mp.setattr(socket, "getaddrinfo", _offline)
    mp.setattr(src.prices._SESSION, "get", _offline)
    # FRED's client retries a blocked socket for 13 s per series; offline, it fails at
    # once, and the risk-free rate reads the book's stored bill series (audit item 15a).
    mp.setattr(src.macro, "fetch_fred_series", _offline)
    mp.setattr(src.db, "DB_PATH", copy)
    mp.setattr(src.db, "_migrated_paths", set())
    src.prices._reset_trailing_memo()
    return copy


def metadata_dated_inside_q2(mp: "pytest.MonkeyPatch", tmp_dir: Path) -> Path:
    """Serve data/etf_metadata.json with every entry's as_of set to April 15, 2026, for a
    test that takes a FRESH Q2 2026 lock: a file such a lock takes. The committed file
    cannot stand in, since its durations are dated after Q2 and leave a fresh Q2 lock's
    metadata pending (#389). The dates are synthetic; the figures are the committed ones.
    (It served the pre-#455 equity fact sheets until the style box was withdrawn, #468.)"""
    import json
    from src import etf_metadata
    meta = json.loads(Path(etf_metadata.META_PATH).read_text())
    entries = [v for v in meta.values() if isinstance(v, dict)]
    assert entries and all("duration_years" in v for v in entries), (
        "premise: the committed file carries durations only")
    for v in entries:
        v["as_of"] = "2026-04-15"
    out = tmp_dir / "etf_metadata_inside_q2.json"
    out.write_text(json.dumps(meta))
    mp.setattr(etf_metadata, "META_PATH", out)
    return out


# The record tools/restate_demo_gap_record.py appended to each of the five locks #469
# rewrote (#478), as the text it adds: the lock's last key.
GAP_RECORD_APPENDED = (', "gap_corrections": {"restated_on": "2026-09-28", '
                       '"removed": ["VNQ+DBC"]}}')


def before_the_policy_was_held(text: str) -> str:
    """A lock's stored payload as it stood before the book's SAA policy was recorded in it
    (#525): the same text with inputs["policy"] and inputs_through["policy"] removed. A
    lock that holds none is returned as it is. tests/test_locks_hold_the_policy.py pins
    that this is main's lock at 46b5300, byte for byte, so a test that pins what a lock
    held before the policy undoes it with this first."""
    import json
    blob = json.loads(text)
    if "policy" not in (blob.get("inputs") or {}):
        return text
    del blob["inputs"]["policy"]
    del blob["inputs_through"]["policy"]
    return json.dumps(blob)


def record_the_policy(book) -> None:
    """Run tools/hold_policy_in_demo_locks.py on ``book``, a test's copy, over every lock
    it holds: the last step that made the committed locks (#525). A test that rebuilds a
    committed lock by replaying the tools that made it ends with this."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "hold_policy_in_demo_locks", _ROOT / "tools" / "hold_policy_in_demo_locks.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    con = sqlite3.connect(f"file:{Path(book).as_posix()}?mode=ro", uri=True)
    try:
        quarters = [q for (q,) in con.execute("SELECT quarter_id FROM quarter_snapshots")]
    finally:
        con.close()
    assert tool.main(book, quarters=quarters) == 0


def before_the_gap_record(text: str) -> str:
    """A lock's stored payload as it stood before the gap record was appended (#478): the
    same text less the record. A lock that carries none is returned as it is.
    tests/test_gap_rewrite_is_recorded.py pins that this is main's lock at cc9c31d, byte
    for byte, so a test that pins what a lock held before the record undoes it with this
    first. The SAA policy was recorded after it (#525), so that is undone first."""
    text = before_the_policy_was_held(text)
    if text.endswith(GAP_RECORD_APPENDED):
        return text[:-len(GAP_RECORD_APPENDED)] + "}"
    return text


_Q2_BEFORE_491: "dict[str, str]" = {}


def q2_2026_before_its_closes_were_filled(text: str) -> str:
    """The Q2 2026 lock's stored payload as it stood before #491's restatement, rebuilt
    from the committed one (``text``): its two price frames recomputed WITHOUT the 32
    filled closes, by the restatement tool's own steps (tools/restate_demo_filled_closes
    .frames, on data/demo.db's closes and the dividends the lock holds), and the record
    removed. tests/test_filled_closes.py pins that this is main's lock at 1e3e5c9, byte
    for byte, so a test that pins what the lock held before #491 undoes the restatement
    with this first. Both books' locks are the same text, so one result serves both.
    The gap record (#478) was appended after #491, so it is undone first."""
    if text not in _Q2_BEFORE_491:
        import importlib.util
        import json
        spec = importlib.util.spec_from_file_location(
            "restate_demo_filled_closes", _ROOT / "tools" / "restate_demo_filled_closes.py")
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        blob = json.loads(before_the_gap_record(text))
        filled = {tuple(c) for c in blob.pop(tool.RECORD)["filled"]}
        tickers, end = list(blob["adj_close"]["columns"]), "2026-06-30"
        closes = {k: c for k, c in tool._closes(_ROOT / "data" / "demo.db").items()
                  if k[0] in tickers and tool.INCEPTION <= k[1] <= end and k not in filled}
        held = blob["inputs"]["dividends"]["data"]
        dividends = {(t, d): a for t in tickers
                     for d, a in zip(held[t]["index"], held[t]["data"])}
        blob.update(tool.frames(closes, dividends, tickers, end))
        _Q2_BEFORE_491[text] = json.dumps(blob)
    return _Q2_BEFORE_491[text]


# The day these renders run "as of": the day after the frozen book's frontier
# (2026-07-20). A frozen book alone does not make a render date-independent: the
# pages read date.today() 79 times (YTD, quarter reportability, benchmark coverage
# "as of today"), so against a fixed book the SAME render would change with the
# calendar. Pinning today to the book's own next day is what makes "nothing depends
# on the date" true. test_frozen_book.py checks the pin against the book.
FROZEN_TODAY = __import__("datetime").date(2026, 7, 21)

import datetime as _dt  # noqa: E402

_REAL_DATE = _dt.date


class _PinnedDateMeta(type):
    def __instancecheck__(cls, obj):
        # A real date (from pandas, sqlite, arithmetic) must still pass
        # isinstance(x, date) wherever `date` is now the pinned class.
        return isinstance(obj, _REAL_DATE)


class _PinnedDate(_REAL_DATE, metaclass=_PinnedDateMeta):
    """datetime.date with today() pinned. Everything else is inherited."""
    _pin = FROZEN_TODAY

    @classmethod
    def today(cls):
        return _REAL_DATE(cls._pin.year, cls._pin.month, cls._pin.day)


_REAL_TODAY_ET = None


def _pinned_today_et(now=None):
    """asof.today_et under a pin: the pinned day, unless a caller passes its own
    moment, which is converted as the real function converts it."""
    if now is not None:
        return _REAL_TODAY_ET(now)
    return _REAL_DATE(_PinnedDate._pin.year, _PinnedDate._pin.month, _PinnedDate._pin.day)


def pin_today(mp: "pytest.MonkeyPatch", day=FROZEN_TODAY, *, pin_et: bool = True) -> None:
    """Make date.today() return ``day`` for code run under ``mp``: the datetime
    module (so a page script's `from datetime import date` at exec gets it) and
    every loaded src/tests/pages module that bound the real `date` at import.

    And New York's date: every rendered "today" reads asof.today_et() (#443), so a
    pin that left it on the real clock would put the calendar back into every
    frozen-book render. ``pin_et=False`` leaves it on the clock, for a test that
    sets the clock itself and asks what a UTC machine renders.

    datetime.now() is NOT pinned: its three uses (the unsettled-bar check, reached
    only by a fetch; snapshot capture time; Macro's "Last updated" caption) are not
    reached by an offline frozen-book render."""
    global _REAL_TODAY_ET
    _PinnedDate._pin = day
    mp.setattr(_dt, "date", _PinnedDate)
    for name, mod in list(sys.modules.items()):
        if (name.startswith(("src", "tests", "pages")) or name.startswith("test_"))                 and getattr(mod, "date", None) is _REAL_DATE:
            mp.setattr(mod, "date", _PinnedDate)
    if not pin_et:
        return
    import src.asof
    if _REAL_TODAY_ET is None:
        _REAL_TODAY_ET = src.asof.today_et
    for name, mod in list(sys.modules.items()):
        if (name.startswith(("src", "tests", "pages")) or name.startswith("test_")) \
                and getattr(mod, "today_et", None) is _REAL_TODAY_ET:
            mp.setattr(mod, "today_et", _pinned_today_et)


def unpin_leftovers() -> "list[str]":
    """Restore the real `date` in any module that bound the PINNED class. A module
    first imported while a pin was active took `from datetime import date` from the
    patched datetime module; the monkeypatch only restores what it patched, so
    without this that module would read the pinned day for the rest of the run."""
    fixed = []
    for name, mod in list(sys.modules.items()):
        if getattr(mod, "date", None) is _PinnedDate:
            setattr(mod, "date", _REAL_DATE)
            fixed.append(name)
        if getattr(mod, "today_et", None) is _pinned_today_et and _REAL_TODAY_ET is not None:
            setattr(mod, "today_et", _REAL_TODAY_ET)
            fixed.append(name)
    return fixed


def utc_machine(mp: "pytest.MonkeyPatch", instant: "_dt.datetime") -> None:
    """A UTC machine at ``instant`` (an aware datetime), for code run under ``mp``:
    date.today() is the UTC date, and the clock asof.today_et() reads says ``instant``.
    New York's date is left to be derived, not pinned.

    GitHub's runner is such a machine. From 00:00 UTC until midnight in New York its
    date is a day ahead of New York's, and a test that stages a date from date.today()
    for code that counts from today_et() is then a day out (#493). Call
    unpin_leftovers() afterwards, as after any pin_today."""
    import src.asof

    class _Clock(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)

    pin_today(mp, instant.astimezone(_dt.timezone.utc).date(), pin_et=False)
    mp.setattr(src.asof, "datetime", _Clock)


# One New York day, read on a UTC machine in the morning and in the evening. At 9 PM
# Eastern the machine's date is already the next day.
NEW_YORK_DAY = _REAL_DATE(2026, 10, 12)
_UTC_MACHINE_AT = {
    "11 AM ET": _dt.datetime(2026, 10, 12, 15, 0, tzinfo=_dt.timezone.utc),
    "9 PM ET": _dt.datetime(2026, 10, 13, 1, 0, tzinfo=_dt.timezone.utc),
}


@pytest.fixture(params=list(_UTC_MACHINE_AT))
def new_york_day(request):
    """New York's date, NEW_YORK_DAY, on a UTC machine pinned at 11 AM and again at
    9 PM Eastern that day. A test that counts days against the app's "today" stages
    its dates from this, never from date.today(): in the evening case the machine's
    date is a day ahead, which the fixture checks before the test runs (#493)."""
    import src.asof
    with pytest.MonkeyPatch.context() as mp:
        utc_machine(mp, _UTC_MACHINE_AT[request.param])
        ahead = (_dt.date.today() - NEW_YORK_DAY).days
        assert src.asof.today_et() == NEW_YORK_DAY, "premise: New York's date is derived"
        assert ahead == (1 if request.param == "9 PM ET" else 0), (
            "premise: the machine's date is a day ahead of New York's only in the evening")
        yield NEW_YORK_DAY
    unpin_leftovers()


@pytest.fixture(scope="module")
def frozen_book_module(tmp_path_factory):
    """Module-scoped: a copy of the frozen book, offline, with today pinned to the
    book's next day. For modules whose rendered-page fixture is module-scoped."""
    with pytest.MonkeyPatch.context() as mp:
        pin_today(mp)
        yield point_at_frozen_book(mp, tmp_path_factory.mktemp("frozen_book"))
    unpin_leftovers()


@pytest.fixture(scope="module")
def frozen_clock_module():
    """Only the pinned clock, for modules that copy the frozen book per test."""
    with pytest.MonkeyPatch.context() as mp:
        pin_today(mp)
        yield FROZEN_TODAY
    unpin_leftovers()
