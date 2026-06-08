"""Phase 38a migration: cash becomes operational float, not a strategic sleeve.

Rescales the 9 non-cash SAA sleeves (and their 3 parents) to sum to 1.0 — each
prior target ÷ 0.98 (the prior non-cash total) — and sets the Cash parent and
Cash / SPAXX sub-class target_weight to 0 (retained as untargeted rows so the
SPAXX security mapping and operational-cash plumbing keep working).

Patches both data/tracker.db and data/demo.db in-place. Idempotent: the values
are absolute, so running twice leaves both DBs in the same state.

Holdings are NOT re-seeded — the paper portfolio still holds SPAXX as
operational float; only the strategic TARGETS and the ex-cash denominator
change. Position theses are left as-is for the same reason.
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_EXCASH_NORM = 0.98  # prior non-cash target total

# name -> new target_weight (parents). Cash parent untargeted.
PARENT_UPDATES: dict[str, float] = {
    "Equity":      0.78 / _EXCASH_NORM,
    "Income":      0.10 / _EXCASH_NORM,
    "Real Assets": 0.10 / _EXCASH_NORM,
    "Cash":        0.0,
}

# name -> new target_weight (sub-classes). Cash / SPAXX untargeted.
SUBCLASS_UPDATES: dict[str, float] = {
    "US Large Core":           0.17 / _EXCASH_NORM,
    "US Large Quality":        0.15 / _EXCASH_NORM,
    "US Large Value":          0.09 / _EXCASH_NORM,
    "US Small Cap":            0.08 / _EXCASH_NORM,
    "International Developed":  0.20 / _EXCASH_NORM,
    "Emerging Markets":        0.09 / _EXCASH_NORM,
    "Core Fixed Income":       0.06 / _EXCASH_NORM,
    "TIPS":                    0.04 / _EXCASH_NORM,
    "Real Assets":             0.10 / _EXCASH_NORM,
    "Cash / SPAXX":            0.0,
}


def _patch_db(db_path: Path) -> None:
    if not db_path.exists():
        print(f"  SKIP: {db_path} — not found")
        return
    print(f"\nPatching {db_path} ...")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        for name, new_wt in PARENT_UPDATES.items():
            row = conn.execute(
                "SELECT target_weight FROM asset_classes "
                "WHERE name = ? AND parent_id IS NULL",
                (name,),
            ).fetchone()
            if row is None:
                print(f"  WARNING: parent '{name}' not found — skipping")
                continue
            conn.execute(
                "UPDATE asset_classes SET target_weight = ? "
                "WHERE name = ? AND parent_id IS NULL",
                (new_wt, name),
            )
            print(f"  parent {name:<12}: {row['target_weight']:.6f} -> {new_wt:.6f}")

        for name, new_wt in SUBCLASS_UPDATES.items():
            row = conn.execute(
                "SELECT target_weight FROM asset_classes "
                "WHERE name = ? AND parent_id IS NOT NULL",
                (name,),
            ).fetchone()
            if row is None:
                print(f"  WARNING: sub-class '{name}' not found — skipping")
                continue
            conn.execute(
                "UPDATE asset_classes SET target_weight = ? "
                "WHERE name = ? AND parent_id IS NOT NULL",
                (new_wt, name),
            )
            print(f"  sub  {name:<24}: {row['target_weight']:.6f} -> {new_wt:.6f}")

        conn.commit()
        print("  Done.")
    finally:
        conn.close()


def main() -> None:
    for db_path in [ROOT / "data" / "tracker.db", ROOT / "data" / "demo.db"]:
        _patch_db(db_path)


if __name__ == "__main__":
    main()
