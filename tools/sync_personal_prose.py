"""Run the personal-book prose migrations in order — the single carry-over entry point.

tracker.db is gitignored, so the authored-prose syncs that update it in place do
NOT travel with the repo. On a machine that already carries a personal tracker.db
(seeded before these prose changes landed), run this ONCE after pulling to bring
its holding_rationale text up to what shipped in the seed:

    python tools/sync_personal_prose.py

It runs the personal prose migrations in CHRONOLOGICAL order — the order the
edits actually landed, which is deliberately NOT filename-sorted order:

    1. migrate_personal_vea_rationale        (drop the stale 19% weight)
    2. migrate_personal_intl_rationales      (fill IDHQ/AVIV/AVDV; de-superlative AVUV/IEMG)
    3. migrate_personal_revisit_clause_bold  (bold + split the five existing revisit clauses)
    4. migrate_personal_missing_falsifiers   (add the five missing falsifiers; PDBC opening rewrite)
    5. migrate_personal_vgit_jurisdiction    (VGIT: the DC reference replaced, #283)
    6. migrate_vgit_state_exemption          (VGIT: the sentence names no state, #710)

Step 6 rewrites the sentence step 5 wrote, so step 5 treats a book that already
carries the #710 sentence as done (#720), and a book at any of VGIT's three texts
(before #283, after it, after #710) ends at the #710 text. Step 6 takes the book on its
command line, never defaulted; the runner passes tracker.db.

Today's per-ticker substring swaps happen to be order-independent, but that is
coincidental, not guaranteed — the real order is encoded here so a future
migration that IS order-dependent stays correct.

Each migration is independently idempotent (already-applied → writes nothing) and
aborts on a text mismatch rather than guessing. This runner PROPAGATES that abort:
if any migration aborts it STOPS immediately and reports which one, why, and what
did/didn't run. It never catches-and-continues — a half-applied prose sync is
exactly the state this exists to prevent. Safe to run repeatedly; on a fully
synced DB every step reports "already-applied" and nothing is written.

Steps 2 and 4 take step 5's rule (#732). Each holds its target text and the SHA-256 of
every text the seed held after it as data, never reading today's seed, and leaves a book
at one of those later texts as it is. They read the seed until #732, so the next edit to
the seed made them abort, and the runner stopped at step 2 on the personal book. From
every text the seed has held for the tickers they touch, the runner now completes end to
end (tests/test_prose_runner_from_history.py, on scratch copies of the personal book),
except from AVUV's first seed text, which stops at step 3 (#747).

WHY this is a manual runner and not an auto-discovered bootstrap migration
--------------------------------------------------------------------------
src/bootstrap.run_pending_migrations auto-runs every tools/migrate_*.py that
exposes migrate_db(db_path); modules exposing only main() are deliberately skipped
(bootstrap.py documents this for the SAA migrations). These are all main()-only
ON PURPOSE — auto-running them on bootstrap is wrong for three concrete reasons:

  - Ordering. run_pending_migrations fires at bootstrap step 3, BEFORE
    seed_securities.seed() (step 4) adds the holding_rationale column and inserts
    the ETF rows. On a fresh DB a prose swap at step 3 hits "no such column" / no
    rows and crashes the boot.
  - Redundant on a fresh DB. seed_securities inserts the CURRENT prose directly,
    so a fresh boot never needs these; they matter ONLY for an existing DB that
    already holds older prose — the carry-over case this runner serves.
  - Abort-as-crash. Each migration aborts hard on a text mismatch (correct for a
    manual run). Folded into bootstrap, a hard abort would take down app startup,
    forcing it to degrade into a caught/logged — or silent — skip. A silent skip
    on a personal DB with drifted prose is worse than a manual step: it turns a
    loud, correct failure into a quiet one. So the abort stays loud and manual.

Targets data/tracker.db only (each migration hardcodes that path); it cannot touch
demo.db regardless of TRACKER_MODE.
"""
import importlib.util
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

_TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(_TOOLS.parent))  # repo root — so each migration's `from src...` resolves

TRACKER_DB = _TOOLS.parent / "data" / "tracker.db"
# Books this runner refuses whatever it is handed: the committed ones.
_NEVER = (_TOOLS.parent / "data" / "demo.db",
          _TOOLS.parent / "tests" / "fixtures" / "frozen_book.db")

# Chronological / dependency order — NOT filename order (see module docstring).
_MIGRATIONS = [
    "migrate_personal_vea_rationale",
    "migrate_personal_intl_rationales",
    "migrate_personal_revisit_clause_bold",
    "migrate_personal_missing_falsifiers",
    # #283, 2026-08-22. Independent of the four above — a different ticker (VGIT) and
    # a different sentence — so it is order-free in fact, and appended last because
    # this list encodes WHEN edits landed, not what depends on what.
    "migrate_personal_vgit_jurisdiction",
    # #710/#720. Rewrites the sentence step 5 wrote so that it names no state; step 5
    # reports a book already carrying it as done.
    "migrate_vgit_state_exemption",
]
# Steps whose main() takes the book on its command line rather than a TRACKER_DB
# constant. Their output names each cell's outcome; "updated" is a write.
_TAKES_THE_BOOK = frozenset({"migrate_vgit_state_exemption"})


def _load(name: str):
    path = _TOOLS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _emit(reason: str) -> None:
    for line in reason.splitlines():
        print(f"      {line}")


def main() -> int:
    return run(TRACKER_DB)


def run(db_path: Path) -> int:
    """The sync against ``db_path``: tracker.db from main(); a scratch copy of it in a
    test. Every step is pointed at that one book, and a committed book is refused."""
    db_path = Path(db_path)
    if any(db_path.resolve() == p.resolve() for p in _NEVER):
        print(f"ABORT: {db_path} is a committed book; this runner writes the personal one.")
        return 1
    n = len(_MIGRATIONS)
    print(f"Personal prose sync — {n} migrations against {db_path.name}, in chronological order.\n")

    applied: list[str] = []
    already: list[str] = []
    for i, name in enumerate(_MIGRATIONS, 1):
        mod = _load(name)
        if hasattr(mod, "TRACKER_DB"):
            mod.TRACKER_DB = db_path
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                rc = mod.main([str(db_path)]) if name in _TAKES_THE_BOOK else mod.main()
        except Exception as exc:  # unexpected — never swallow; name the culprit and stop
            print(f"[{i}/{n}] {name}: ERROR — {type(exc).__name__}: {exc}")
            _emit(buf.getvalue().strip())
            print(f"\nSTOPPED at step {i}/{n} ({name}) — raised unexpectedly; "
                  f"steps {i}..{n} were NOT run.")
            raise

        reason = buf.getvalue().strip()
        if rc != 0:  # a documented abort (text diverged, or no personal DB here)
            print(f"[{i}/{n}] {name}: ABORTED (exit {rc})")
            _emit(reason)
            done = applied + already
            after = _MIGRATIONS[i:]
            print(f"\nSTOPPED at step {i}/{n} ({name}) — it aborted. "
                  f"Completed before it: {', '.join(done) if done else '(none)'}. "
                  f"Not run after it: {', '.join(after) if after else '(none)'}.")
            print("Resolve the divergence it reported, then re-run this script "
                  "(completed steps are no-ops).")
            return rc

        if name in _TAKES_THE_BOOK:
            wrote = any(line.endswith(": updated") for line in reason.splitlines())
        else:
            wrote = "nothing to do" not in reason
        status = "applied" if wrote else "already-applied"
        (already if status == "already-applied" else applied).append(name)
        print(f"[{i}/{n}] {name}: {status}")
        _emit(reason)

    print(f"\nDone — {len(applied)} applied, {len(already)} already-applied, 0 aborted.")
    if applied:
        print("  applied:         " + ", ".join(applied))
    if already:
        print("  already-applied: " + ", ".join(already))
    print(f"{db_path.name} prose is in sync with the shipped seed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
