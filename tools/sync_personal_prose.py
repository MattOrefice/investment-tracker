"""Run the personal-book prose migrations in order — the single carry-over entry point.

tracker.db is gitignored, so the authored-prose syncs that update it in place do
NOT travel with the repo. On a machine that already carries a personal tracker.db
(seeded before these prose changes landed), run this ONCE after pulling to bring
its holding_rationale text up to what shipped in the seed:

    python tools/sync_personal_prose.py

It runs the five personal prose migrations in CHRONOLOGICAL order — the order the
edits actually landed, which is deliberately NOT filename-sorted order:

    1. migrate_personal_vea_rationale        (drop the stale 19% weight)
    2. migrate_personal_intl_rationales      (fill IDHQ/AVIV/AVDV; de-superlative AVUV/IEMG)
    3. migrate_personal_revisit_clause_bold  (bold + split the five existing revisit clauses)
    4. migrate_personal_missing_falsifiers   (add the five missing falsifiers; PDBC opening rewrite)
    5. migrate_personal_vgit_jurisdiction    (VGIT: DC -> Pennsylvania's flat rate, #283)

Today's per-ticker substring swaps happen to be order-independent, but that is
coincidental, not guaranteed — the real order is encoded here so a future
migration that IS order-dependent stays correct.

Each migration is independently idempotent (already-applied → writes nothing) and
aborts on a text mismatch rather than guessing. This runner PROPAGATES that abort:
if any migration aborts it STOPS immediately and reports which one, why, and what
did/didn't run. It never catches-and-continues — a half-applied prose sync is
exactly the state this exists to prevent. Safe to run repeatedly; on a fully
synced DB every step reports "already-applied" and nothing is written.

WHY this is a manual runner and not an auto-discovered bootstrap migration
--------------------------------------------------------------------------
src/bootstrap.run_pending_migrations auto-runs every tools/migrate_*.py that
exposes migrate_db(db_path); modules exposing only main() are deliberately skipped
(bootstrap.py documents this for the SAA migrations). These five are main()-only
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
]


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
    n = len(_MIGRATIONS)
    print(f"Personal prose sync — {n} migrations against tracker.db, in chronological order.\n")

    applied: list[str] = []
    already: list[str] = []
    for i, name in enumerate(_MIGRATIONS, 1):
        mod = _load(name)
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                rc = mod.main()
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

        status = "already-applied" if "nothing to do" in reason else "applied"
        (already if status == "already-applied" else applied).append(name)
        print(f"[{i}/{n}] {name}: {status}")
        _emit(reason)

    print(f"\nDone — {len(applied)} applied, {len(already)} already-applied, 0 aborted.")
    if applied:
        print("  applied:         " + ", ".join(applied))
    if already:
        print("  already-applied: " + ", ".join(already))
    print("tracker.db prose is in sync with the shipped seed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
