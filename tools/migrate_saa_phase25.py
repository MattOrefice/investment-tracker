"""Phase 25.0 migration: revise SAA from 15% FI to 10% FI.

Patches both data/tracker.db and data/demo.db in-place.
Idempotent: running twice leaves both DBs in the correct state.
"""
import sys
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PARENT_UPDATES: dict[str, float] = {
    "Equity": 0.78,
    "Income": 0.10,
    "Cash":   0.02,
}

SUBCLASS_UPDATES: dict[str, float] = {
    "US Large Core":           0.17,
    "US Large Quality":        0.15,
    "US Large Value":          0.09,
    "US Small Cap":            0.08,
    "International Developed": 0.20,
    "Emerging Markets":        0.09,
    "Core Fixed Income":       0.06,
    "TIPS":                    0.04,
    "Cash / SPAXX":            0.02,
}

# (old_fragment, new_fragment) patches applied via SQL REPLACE on rationale column
RATIONALE_PATCHES = [
    ("9% in a 72% growth portfolio is intentionally thin",
     "6% in a 78% growth portfolio is intentionally thin"),
    ("6% is 40% of the FI sleeve",
     "4% is 40% of the FI sleeve"),
    ("3% handles rebalancing friction",
     "2% handles rebalancing friction"),
]

# Position thesis target_weight updates (ticker -> new_weight).
# VNQ and PDBC intentionally absent — Real Assets sleeve is unchanged at 10%
# and the equal-split seeder keeps each at 5%.
THESIS_UPDATES: dict[str, float] = {
    "VOO":   0.17,
    "SPHQ":  0.15,
    "VTV":   0.09,
    "AVUV":  0.08,
    "VEA":   0.20,
    "IEMG":  0.09,
    "VGIT":  0.06,
    "SCHP":  0.04,
    "SPAXX": 0.02,
}

# Same applied to VGIT's holding_rationale in securities table
VGIT_PATCHES = [
    ("appropriate for a 9% sleeve inside a 72% growth portfolio",
     "appropriate for a 6% sleeve inside a 78% growth portfolio"),
    ("portfolio's 72% growth allocation",
     "portfolio's 78% growth allocation"),
]


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
            print(f"  parent {name}: {row['target_weight']:.4f} -> {new_wt:.4f}")

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
            print(f"  sub  {name}: {row['target_weight']:.4f} -> {new_wt:.4f}")

        for old_text, new_text in RATIONALE_PATCHES:
            conn.execute(
                "UPDATE asset_classes "
                "SET rationale = REPLACE(rationale, ?, ?) "
                "WHERE rationale LIKE ?",
                (old_text, new_text, f"%{old_text[:40]}%"),
            )

        for old_text, new_text in VGIT_PATCHES:
            conn.execute(
                "UPDATE securities "
                "SET holding_rationale = REPLACE(holding_rationale, ?, ?) "
                "WHERE ticker = 'VGIT' AND holding_rationale LIKE ?",
                (old_text, new_text, f"%{old_text[:40]}%"),
            )

        for ticker, new_wt in THESIS_UPDATES.items():
            row = conn.execute(
                "SELECT thesis_id, target_weight FROM theses "
                "WHERE title LIKE ? AND level = 'position'",
                (f"% — {ticker}",),
            ).fetchone()
            if row is None:
                print(f"  WARNING: thesis for {ticker} not found — skipping")
                continue
            conn.execute(
                "UPDATE theses SET target_weight = ? WHERE thesis_id = ?",
                (new_wt, row["thesis_id"]),
            )
            print(f"  thesis {ticker}: {row['target_weight']:.4f} -> {new_wt:.4f}")

        conn.commit()
        print("  Rationale patches applied. Done.")
    finally:
        conn.close()


def main() -> None:
    for db_path in [ROOT / "data" / "tracker.db", ROOT / "data" / "demo.db"]:
        _patch_db(db_path)


if __name__ == "__main__":
    main()
