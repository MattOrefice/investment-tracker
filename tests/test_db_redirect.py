"""The tracked-DB write redirect is ON, and its edges are where they are claimed.

See tests/conftest.py for the mechanism, the census that motivated it, and the
three channels it does NOT cover. This file is the proof that it is actually
installed — a redirect that silently stopped working would otherwise look
identical to one that works, because a byte-copy is content-identical and every
content assertion in the suite passes either way.

THE ONE TEST THAT MUST BE ABLE TO FAIL is test_the_redirect_is_active. It is the
positive control: with TRACKER_TEST_NO_DB_REDIRECT set it FAILS, which is what
makes a green run mean something. Do not teach it to skip when the redirect is
off — a control that passes in both conditions is a failed instrument, not a
null result.
"""
import os
import shutil
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
KILL_SWITCH = "TRACKER_TEST_NO_DB_REDIRECT"
# Split so the scan below cannot match this module's own reference to the name.
_REAL_DB_MARKER_NAME = "real_" + "tracked_db"


def _tracked_dbs():
    return sorted(DATA.glob("*.db"))


def _opened_path(conn) -> Path:
    """The file SQLite actually opened, asked of SQLite rather than inferred."""
    rows = conn.execute("PRAGMA database_list").fetchall()
    main = [r for r in rows if r[1] == "main"][0]
    return Path(main[2]).resolve()


@pytest.fixture
def a_tracked_db():
    dbs = _tracked_dbs()
    if not dbs:
        pytest.skip("no tracked database present")
    return dbs[0]


# ── the positive control ──────────────────────────────────────────────────────

def test_the_redirect_is_active(a_tracked_db):
    """A write-mode open of a tracked book must land somewhere else.

    Fails when the kill switch is set. That is intended and is the whole value of
    this test: it converts "protection is off" from an invisible local condition
    into a red suite.
    """
    conn = sqlite3.connect(str(a_tracked_db))
    try:
        opened = _opened_path(conn)
    finally:
        conn.close()
    assert opened != a_tracked_db.resolve(), (
        f"write-mode open of {a_tracked_db.name} reached the REAL file. "
        f"If {KILL_SWITCH} is set, unset it; otherwise the redirect in "
        f"tests/conftest.py is not installed."
    )
    assert DATA not in opened.parents, (
        f"redirected inside data/ ({opened}) — a copy under the tracked "
        f"directory is still one `git add -A` from being committed")


def test_every_tracked_db_is_protected_not_just_the_first():
    """A redirect keyed on one path would satisfy the control above and leave the
    other book exposed. 1,996 of the 2,228 measured writable opens were
    tracker.db and 232 were demo.db, so both matter."""
    dbs = _tracked_dbs()
    if not dbs:
        pytest.skip("no tracked database present")
    for db in dbs:
        conn = sqlite3.connect(str(db))
        try:
            assert _opened_path(conn) != db.resolve(), f"{db.name} unprotected"
        finally:
            conn.close()


# ── the edges ─────────────────────────────────────────────────────────────────

def test_read_only_opens_are_not_redirected(a_tracked_db):
    """A mode=ro URI wants the real book and cannot harm it, so it must pass
    through untouched. Redirecting one is the failure mode of this change: a test
    silently reading a copy. Only 4 such opens exist in a full run."""
    conn = sqlite3.connect(f"file:{a_tracked_db.as_posix()}?mode=ro", uri=True)
    try:
        assert _opened_path(conn) == a_tracked_db.resolve(), (
            "a read-only open was redirected — reads of the real book must reach "
            "the real book")
    finally:
        conn.close()


def test_untracked_paths_are_left_alone(tmp_path):
    """The redirect must be keyed on the tracked set, not on the .db suffix, or
    every fixture DB in the suite would be copied."""
    scratch = tmp_path / "scratch.db"
    conn = sqlite3.connect(str(scratch))
    try:
        assert _opened_path(conn) == scratch.resolve()
    finally:
        conn.close()


def test_a_write_through_the_redirect_leaves_the_real_file_untouched(a_tracked_db):
    """End to end, and two-sided: the write must SUCCEED (or the redirect is just
    breakage) and the real file's mtime/size must not move.

    SKIPS when the redirect is off, and this one exception is deliberate. With
    protection disabled this test would genuinely CREATE a table in the committed
    demo.db — the verification run would become a drift source of its own. The
    activeness controls above already fail in that condition, so the signal is not
    lost by skipping here; only the self-inflicted damage is.
    """
    if os.environ.get(KILL_SWITCH):
        pytest.skip("redirect off: this test would write the committed database")
    before = (a_tracked_db.stat().st_size, a_tracked_db.stat().st_mtime_ns)
    conn = sqlite3.connect(str(a_tracked_db))
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS _redirect_probe (x INTEGER)")
        conn.execute("INSERT INTO _redirect_probe (x) VALUES (1)")
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM _redirect_probe").fetchone()[0] >= 1
    finally:
        conn.close()
    after = (a_tracked_db.stat().st_size, a_tracked_db.stat().st_mtime_ns)
    assert before == after, f"{a_tracked_db.name} moved: {before} -> {after}"


# ── the escape hatch ──────────────────────────────────────────────────────────

@pytest.mark.real_tracked_db
def test_the_escape_hatch_reaches_the_real_book(a_tracked_db):
    """The marker must actually work, or a future test that genuinely needs the
    real book would be silently served a copy.

    READ-ONLY on purpose. This is the only marked test in the suite and it must
    stay that way: the census found no test needing real-book access, so the
    marked set should remain visibly empty apart from this self-test.
    """
    conn = sqlite3.connect(str(a_tracked_db))
    try:
        assert _opened_path(conn) == a_tracked_db.resolve()
    finally:
        conn.close()


def test_the_marked_set_stays_empty_apart_from_its_own_self_test():
    """An escape hatch that is easy to reach gets used. Counting the marker's uses
    in the tree keeps 'nobody needs this' a measured claim rather than a belief.
    """
    # Matched as a DECORATOR, not as a substring: a bare substring search also
    # matches the search literal on this very line, so the first version of this
    # test reported 2 uses and failed against itself.
    hits = []
    for path in (ROOT / "tests").rglob("*.py"):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.strip().startswith("@pytest.mark." + _REAL_DB_MARKER_NAME):
                hits.append(f"{path.relative_to(ROOT)}:{i}")
    assert len(hits) == 1, (
        f"the real-DB escape hatch is used {len(hits)} times: {hits}. Expected "
        f"exactly one (this file's own self-test). If a test genuinely needs the "
        f"real book, measure why and update this count deliberately.")


# ── the kill switch ───────────────────────────────────────────────────────────

def test_the_kill_switch_is_never_set_in_ci():
    """Unconditional, so it needs no skip: the assertion is about the CONJUNCTION.
    Locally with the switch set this passes and the header banner plus the failing
    positive control carry the signal instead."""
    assert not (os.environ.get("CI") and os.environ.get(KILL_SWITCH)), (
        f"{KILL_SWITCH} is set in CI — the redirect is disabled on the one runner "
        f"nobody watches interactively")


# ── the declared gaps, pinned so they cannot quietly change ───────────────────

def test_file_copies_are_not_covered_and_this_is_declared(a_tracked_db, tmp_path):
    """#173's trio reaches the real DB by copying the FILE, so the connect-level
    redirect cannot see it. Pinned as an INVERSE assertion: if a future change
    makes copies redirect too, this goes red and points at the docstring that
    currently declares the gap, instead of the claim silently going stale.
    """
    dst = tmp_path / "copied.db"
    shutil.copyfile(a_tracked_db, dst)
    assert dst.stat().st_size == a_tracked_db.stat().st_size, (
        "a file copy no longer reads the real tracked DB — the uncovered-channel "
        "note in tests/conftest.py needs updating")


def test_conftest_declares_every_uncovered_channel():
    """The three gaps are load-bearing prose: without them the redirect's
    existence reads as coverage. Asserted against the file so a tidy-up cannot
    drop them silently."""
    src = (ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
    for phrase in ("shutil.copy", "SUBPROCESS WRITES", "CSV CHANNEL",
                   "they still execute"):
        assert phrase in src, f"conftest.py no longer declares: {phrase!r}"
