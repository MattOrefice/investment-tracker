"""Phase 46: carry the International Developed -> 4-sleeve split to the PERSONAL book.

The split itself is migrate_saa_phase39._patch_db, reused verbatim: it renames
"International Developed" -> "International Core" IN PLACE (preserving asset_class_id,
so VEA / EFA / trades / theses follow the rename), inserts International Quality /
Large Value / Small Value, updates targets / bands / sort_order / rationale, reassigns
the tilt securities IDHQ / AVIV / AVDV to the new sleeves, and inserts the IQLT / EFV /
SCZ benchmark rows. That file applies the split to demo.db ONLY, by design: writing a
strategic TARGET into a book whose holdings have not caught up creates an unfunded
sleeve, and the Suggested-$ invariant (src/rebalance.py:267) fires on exactly that.

This migration carries the same split to the personal book, but ONLY once the carrier
positions exist in acct_01's TRADE LEDGER — so holdings precede targets and the
invariant never fires. Discovered by run_pending_migrations, it is SELF-CONDITIONING:

  * already split (International Quality exists)              -> no-op
  * no "International Developed" sleeve to split (demo — it is
    already Core; a fresh build seeds 12 sleeves directly)   -> no-op
  * split pending AND acct_01's ledger holds all three
    carriers (IDHQ, AVIV, AVDV)                              -> apply the split
  * split pending AND any carrier missing                    -> HOLD (no-op), print why

WHY HOLD, NOT RAISE. run_pending_migrations discovers this on EVERY personal bootstrap,
and app.py calls bootstrap_personal_db() UNWRAPPED at import (app.py:19). A raise would
crash the whole app — and the carrier trades are entered THROUGH the app (pages/10
Trade Log, st.form), so a crash would lock you out of the exact screen you use to
satisfy the guard: a deadlock. The enforcement is identical either way — the split
never lands without the carriers — the hold is simply non-fatal, and it self-applies on
the first bootstrap AFTER the carriers exist, so there is nothing to remember. A
deliberate premature split is still refused: the split is not written.

Requiring ALL THREE carriers (not "any", not "none") is deliberate: the invariant is
per-sleeve — Intl Quality, Large Value and Small Value each need their own held ticker,
so a partial carrier set would still fire it for the missing sleeve(s).

demo.db: untouched. Its split is migrate_saa_phase39.main() (already applied), and it
has no "International Developed" sleeve, so this is a guaranteed no-op there.
"""
import importlib.util
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_CARRIERS = ("IDHQ", "AVIV", "AVDV")
_SELF_DIRECTED_PSEUDONYM = "acct_01"
_REQUIRED_TABLES = {"asset_classes", "securities", "accounts", "trades"}


def _patch_db():
    """The split, imported from the demo migration — one source of truth for it."""
    spec = importlib.util.spec_from_file_location(
        "m39_split", str(ROOT / "tools" / "migrate_saa_phase39.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod._patch_db


def _carriers_held(conn) -> int:
    """Distinct carrier tickers present in acct_01's trade ledger (0..3)."""
    acct = conn.execute(
        "SELECT account_id FROM accounts WHERE pseudonym = ?",
        (_SELF_DIRECTED_PSEUDONYM,),
    ).fetchone()
    if acct is None:
        return 0
    qmarks = ",".join("?" * len(_CARRIERS))
    return conn.execute(
        f"SELECT COUNT(DISTINCT ticker) FROM trades "
        f"WHERE account_id = ? AND ticker IN ({qmarks})",
        (acct[0], *_CARRIERS),
    ).fetchone()[0]


def migrate_db(db_path) -> int:
    """Apply the personal intl split iff pending AND the carriers are held.

    Returns 1 if the split was applied, 0 otherwise (already split / not this book /
    held pending carriers)."""
    db_path = Path(db_path)
    if not db_path.exists():
        return 0

    conn = sqlite3.connect(str(db_path))
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if not _REQUIRED_TABLES <= tables:
            return 0

        if conn.execute(
            "SELECT 1 FROM asset_classes WHERE name = 'International Quality'"
        ).fetchone() is not None:
            return 0  # already split (personal already migrated, or demo, or fresh 12-sleeve build)

        if conn.execute(
            "SELECT 1 FROM asset_classes WHERE name = 'International Developed'"
        ).fetchone() is None:
            return 0  # nothing to split

        held = _carriers_held(conn)
        if held < len(_CARRIERS):
            print(
                f"  {db_path.name}: HELD — the International Developed split is pending, "
                f"but acct_01's trade ledger holds {held}/{len(_CARRIERS)} carrier "
                f"positions ({'/'.join(_CARRIERS)}). Place the token buys first, or the "
                "Suggested-$ invariant (rebalance.py:267) will fire. Split NOT applied."
            )
            return 0
    finally:
        conn.close()

    # Carriers present — apply the split (the demo migration's _patch_db, verbatim).
    _patch_db()(db_path)
    print(f"  {db_path.name}: applied the International Developed -> 4-sleeve split "
          f"(all {len(_CARRIERS)} carriers held).")
    return 1


def main() -> None:
    # Intentionally NO standalone run against tracker.db. This migration is discovered
    # by run_pending_migrations, which applies it on the first personal bootstrap AFTER
    # the carrier positions exist. Running it by hand here would bypass that gate.
    print(
        "Phase 46 has no standalone run: it applies via run_pending_migrations on the "
        "first personal bootstrap after acct_01 holds the IDHQ/AVIV/AVDV carriers."
    )


if __name__ == "__main__":
    main()
