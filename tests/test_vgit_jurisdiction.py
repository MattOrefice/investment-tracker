"""VGIT's rationale says something true about the exemption, and names no state. #283, #710.

THE SENTENCE HAD TWO ERRORS POINTING OPPOSITE WAYS (#283). It claimed Treasury interest
is exempt from state and local taxes — **correct**, and contradicted by the register
until #278 — while calling the jurisdiction "a high-income-tax jurisdiction like DC",
which was **wrong** and rendered to a reader as-is. A reader who spotted "DC" and
concluded the sentence was stale would have discarded the half that was right. #283
replaced the jurisdiction with the owner's home state of the day and dropped the
"high-income-tax" characterisation, which was false of it.

#710 TOOK THE STATE OUT. No committed code, comment, doc or test names a home state
(#706), and seeded prose is held to the rule too, so the sentence now says what its
source says and nothing more: Treasury interest is exempt from state and local income
tax under federal law, citing 31 U.S.C. § 3124(a) with the edition it was read in
(tools/migrate_vgit_state_exemption.py). The half that was right is kept; the state,
and the comparison the source does not make, are gone.

WHY THIS FILE TESTS A DATABASE. src/seed_securities.py is INSERT-ONLY (`if existing
is None`, no UPDATE path), so editing the seed changes nothing for a database that
already holds the row. The rendered text lives in data/demo.db (tracked, and it
deploys publicly — pages/8_Research.py's only `if IS_DEMO:` renders a banner, not a
gate), in tests/fixtures/frozen_book.db, and in data/tracker.db (gitignored). Source-
only assertions would pass on a change no reader could see.
"""
import pathlib
import re
import sqlite3

import pytest

import src.seed_securities as seed
from tests.us_states import codes_in_text, states_named
from tools.migrate_vgit_state_exemption import NEW_SENTENCE

ROOT = pathlib.Path(seed.__file__).resolve().parent.parent
DEMO_DB = ROOT / "data" / "demo.db"
FROZEN_BOOK = ROOT / "tests" / "fixtures" / "frozen_book.db"
BOOKS = {"demo.db": DEMO_DB, "frozen book": FROZEN_BOOK}

OLD_FRAGMENT = "high-income-tax jurisdiction like DC"
EXEMPTION = "Treasury interest is exempt from state and local income tax under federal law"
CITATION = "(31 U.S.C. § 3124(a); U.S. Code, 2024 edition, read November 17, 2026)"


def _seed_text() -> str:
    return next(h["holding_rationale"] for h in seed.HOLDINGS if h["ticker"] == "VGIT")


def _book_text(book: pathlib.Path) -> str:
    con = sqlite3.connect(f"file:{book.resolve().as_posix()}?mode=ro", uri=True)
    try:
        row = con.execute(
            "SELECT holding_rationale FROM securities WHERE ticker = 'VGIT'"
        ).fetchone()
        assert row and row[0], f"no VGIT rationale in {book.name}"
        return row[0]
    finally:
        con.close()


def _text(where: str) -> str:
    return _seed_text() if where == "seed" else _book_text(BOOKS[where])


WHERE = ["seed", *BOOKS]


# ── the claims, each asserted separately ──────────────────────────────────────

@pytest.mark.parametrize("where", WHERE)
def test_the_exemption_claim_survives(where):
    """The half that was RIGHT. #278 made the register agree with it, so weakening
    or deleting it here would re-open the contradiction from the other side."""
    assert EXEMPTION in _text(where)


@pytest.mark.parametrize("where", WHERE)
def test_the_stale_jurisdiction_is_gone(where):
    t = _text(where)
    assert OLD_FRAGMENT not in t
    assert "DC" not in t, "a DC reference survives"


@pytest.mark.parametrize("where", WHERE)
def test_the_sentence_names_no_state_and_cites_its_source(where):
    """#710. No state is named or coded, every state checked alike, and the claim
    carries its source and the edition read."""
    t = _text(where)
    assert states_named(t) == [] and codes_in_text(t) == []
    assert NEW_SENTENCE == f"{EXEMPTION} {CITATION}."
    assert NEW_SENTENCE in t
    assert "flat rate" not in t and "high-income-tax" not in t


@pytest.mark.parametrize("where", WHERE)
def test_no_tax_rate_is_stated_in_the_prose(where):
    """The rate is the residence's (personal_profile.load_tax_residence). Putting one
    here mints a second copy of a config value — the mechanism behind four copies of
    one sentence (#228) and two contradicting statements (#284).

    WINDOWED ON THE SENTENCE, not on the prose leading up to it. The first version
    sliced everything before "Cost minimization" and went red on the rationale's
    legitimate expense-ratio figures — a true claim about a different subject. The
    window has to be the artifact, not its neighbourhood.
    """
    t = _text(where)
    m = re.search(re.escape(EXEMPTION) + r".*?\)\.", t, re.S)
    assert m, "the exemption sentence is not present in a recognisable form"
    assert not re.search(r"\d+(\.\d+)?\s*%", m.group(0)), (
        f"a percentage reached the exemption sentence. Sentence: {m.group(0)!r}")


@pytest.mark.parametrize("book", list(BOOKS))
def test_the_seed_and_the_tracked_books_agree(book):
    """THE ASSERTION THAT ONLY A DATA FIX CAN PASS. The seeder is insert-only, so
    these can diverge silently and stay diverged forever. Before #283 they had.
    """
    assert _seed_text() == _book_text(BOOKS[book]), (
        f"src/seed_securities.py and {book} disagree about VGIT's rationale — "
        "the seeder cannot reconcile them (INSERT-only), so a migration is required")


@pytest.mark.parametrize("book", list(BOOKS))
def test_no_other_security_carries_either_stale_sentence(book):
    """Extent, not just mechanism. The migrations target one ticker; this asserts one
    ticker was all there was."""
    con = sqlite3.connect(f"file:{BOOKS[book].resolve().as_posix()}?mode=ro", uri=True)
    try:
        for fragment in (OLD_FRAGMENT, "flat rate"):
            n = con.execute(
                "SELECT COUNT(*) FROM securities WHERE holding_rationale LIKE ?",
                (f"%{fragment}%",)).fetchone()[0]
            assert n == 0, fragment
    finally:
        con.close()


# ── the #283 migration itself ─────────────────────────────────────────────────
# It is kept as it was (#710: a migration's behaviour does not change), so these
# tests run it on fixtures carrying its own pre-migration text.

def _fixture_db(tmp_path, rows):
    """A minimal securities table carrying the given (ticker, rationale) rows."""
    p = tmp_path / "fixture.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE securities (ticker TEXT PRIMARY KEY, "
                "holding_rationale TEXT)")
    con.executemany("INSERT INTO securities VALUES (?, ?)", rows)
    con.commit()
    con.close()
    return p


PRE = ("... Critically, Treasury interest income is exempt from state and local taxes "
       "— a real after-tax advantage in a high-income-tax jurisdiction like DC over "
       "investment-grade corporate bond funds with similar yield. Cost minimization ...")


def _run_demo_migration(monkeypatch, db_path):
    import tools.migrate_demo_vgit_jurisdiction as m
    monkeypatch.setattr(m, "DEMO_DB", db_path)
    return m.main()


def _283_new() -> str:
    import tools.migrate_demo_vgit_jurisdiction as m
    return m._NEW


def test_the_migration_updates_exactly_one_row(monkeypatch, tmp_path):
    db = _fixture_db(tmp_path, [("VGIT", PRE), ("OTHER", PRE)])
    assert _run_demo_migration(monkeypatch, db) == 0
    con = sqlite3.connect(db)
    got = dict(con.execute("SELECT ticker, holding_rationale FROM securities"))
    con.close()
    assert _283_new() in got["VGIT"], "VGIT was not updated"
    assert got["OTHER"] == PRE, (
        "a second row carrying identical text was ALSO updated — the WHERE clause "
        "matches more than the intended row")


def test_a_wide_match_aborts_and_rolls_back(monkeypatch, tmp_path):
    """THE MUTANT THAT MATTERS. A rationale UPDATE whose WHERE matches too widely
    SUCCEEDS SILENTLY — it would surface only in a rendered diff, on a tracked
    binary, after the commit. No other migration in tools/ checks rowcount.

    Simulated by giving the table two rows with the SAME ticker, which is what a
    widened predicate looks like from the UPDATE's side.
    """
    p = tmp_path / "wide.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE securities (ticker TEXT, holding_rationale TEXT)")
    con.executemany("INSERT INTO securities VALUES (?, ?)",
                    [("VGIT", PRE), ("VGIT", PRE)])
    con.commit()
    con.close()

    rc = _run_demo_migration(monkeypatch, p)
    assert rc == 1, "a 2-row match must ABORT, not succeed"
    con = sqlite3.connect(p)
    texts = [r[0] for r in con.execute("SELECT holding_rationale FROM securities")]
    con.close()
    assert all(t == PRE for t in texts), "aborted but did not roll back"


def test_the_migration_is_idempotent(monkeypatch, tmp_path):
    db = _fixture_db(tmp_path, [("VGIT", PRE)])
    assert _run_demo_migration(monkeypatch, db) == 0
    con = sqlite3.connect(db)
    once = con.execute("SELECT holding_rationale FROM securities").fetchone()[0]
    con.close()
    assert _run_demo_migration(monkeypatch, db) == 0
    con = sqlite3.connect(db)
    twice = con.execute("SELECT holding_rationale FROM securities").fetchone()[0]
    con.close()
    assert once == twice


def test_the_migration_aborts_on_unexpected_text(monkeypatch, tmp_path):
    """Neither pre- nor post-migration means something else edited the row. Abort
    rather than overwrite prose this script did not author. The #710 sentence is such
    text to this migration: on a book #710 rewrote it finds neither of its own and
    writes nothing."""
    for text in ("something a human rewrote by hand", f"... {NEW_SENTENCE} ..."):
        db = _fixture_db(tmp_path, [("VGIT", text)])
        assert _run_demo_migration(monkeypatch, db) == 1
        con = sqlite3.connect(db)
        assert con.execute(
            "SELECT holding_rationale FROM securities").fetchone()[0] == text
        con.close()
        db.unlink()


def test_the_migration_preserves_the_surrounding_prose_byte_for_byte(monkeypatch, tmp_path):
    """A targeted substring swap, not a whole-string overwrite — so prose the script
    did not author cannot be silently replaced by the seed's version of it."""
    db = _fixture_db(tmp_path, [("VGIT", PRE)])
    _run_demo_migration(monkeypatch, db)
    con = sqlite3.connect(db)
    after = con.execute("SELECT holding_rationale FROM securities").fetchone()[0]
    con.close()
    assert after.startswith("... Critically, Treasury interest income is exempt")
    assert after.endswith("with similar yield. Cost minimization ...")


# ── wiring ────────────────────────────────────────────────────────────────────

def test_the_personal_migration_is_in_the_runners_LIST():
    """tracker.db is gitignored, so the only way this reaches another machine is the
    documented entry point. An unregistered migration is one nobody will run.

    ASSERTS THE LIST, NOT THE FILE TEXT. The first version searched the source for
    the module name and a mutant deleting it from `_MIGRATIONS` SURVIVED — because
    the module docstring names it too, as step 5. A positive source assertion
    satisfied by prose promising the thing: the runner would have documented a
    migration it no longer runs.
    """
    import tools.sync_personal_prose as runner
    assert "migrate_personal_vgit_jurisdiction" in runner._MIGRATIONS


def test_the_runner_documents_every_migration_it_runs():
    """The other half, and the reason the two must be separate assertions: the
    docstring's numbered list and `_MIGRATIONS` are two copies of one fact, and
    either can drift from the other. This pins them together."""
    import tools.sync_personal_prose as runner
    doc = runner.__doc__ or ""
    missing = [m for m in runner._MIGRATIONS if m not in doc]
    assert not missing, f"runs but does not document: {missing}"


@pytest.mark.parametrize("mod", ["migrate_demo_vgit_jurisdiction",
                                 "migrate_personal_vgit_jurisdiction",
                                 "migrate_vgit_state_exemption"])
def test_the_migrations_expose_main_only(mod):
    """bootstrap.run_pending_migrations auto-runs any tools/migrate_*.py exposing
    migrate_db(db_path). These must NOT be auto-run: at bootstrap step 3 the
    holding_rationale column does not exist yet, and a hard abort would take down
    app startup."""
    import importlib
    m = importlib.import_module(f"tools.{mod}")
    assert hasattr(m, "main")
    assert not hasattr(m, "migrate_db"), (
        f"{mod} exposes migrate_db and would be auto-run on bootstrap")


def test_the_runner_states_no_hardcoded_migration_count():
    """The sibling docstrings said "sequences all four". Adding a fifth made four
    stale copies at once — a count in prose is a second copy of a fact the list
    already holds."""
    for f in sorted((ROOT / "tools").glob("migrate_personal_*.py")):
        t = f.read_text(encoding="utf-8")
        assert "sequences all four" not in t, f"{f.name} hardcodes a migration count"
