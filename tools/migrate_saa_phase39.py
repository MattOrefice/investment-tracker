"""Phase 39 migration: split International Developed into four mirrored sleeves.

Applies the US 17/15/9/8-of-49 structure to the same 20% developed-international
region, producing International Core / Quality / Large Value / Small Value. The
split is weight-neutral — the four targets sum to 0.20 / _EXCASH_NORM exactly, so
no other sleeve moves and the ex-cash total stays 1.0.

"International Developed" is RENAMED in place rather than deleted and re-inserted.
That preserves its asset_class_id, so VEA, EFA and every trade / thesis / position
row already pointing at it follow the rename automatically instead of orphaning.

Targets, bands, benchmarks and rationale prose are imported from src.seed_saa and
src.seed_securities rather than restated here — the seed modules are the single
source of truth, and a migration that duplicated the prose could drift from them.

Patches both data/tracker.db and data/demo.db in-place. Idempotent: every write is
absolute and guarded by an existence check, so running twice leaves both DBs in the
same state.

sleeve_category / is_in_saa are only written where the target DB already uses that
convention (tracker.db populates them; demo.db leaves them NULL throughout). The
household aggregation joins securities.asset_class_id -> asset_classes.name and
de-duplicates on sleeve_category, so the three new holdings need DISTINCT category
strings or three of the four sleeves silently lose their household mapping.
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.seed_saa import SUB_CLASSES, SORT_ORDERS          # noqa: E402
from src.seed_securities import HOLDINGS, BENCHMARKS       # noqa: E402

OLD_NAME = "International Developed"
NEW_CORE = "International Core"

_INTL = [s for s in SUB_CLASSES if s["name"].startswith("International ")]

# Household sleeve_category for each new holding. VEA deliberately KEEPS
# 'intl_developed': it still resolves to International Core via the id join, and
# IEFA (is_in_saa=0, a clean VEA substitute) keeps counting toward that sleeve
# exactly as before. Moving VEA would drop IEFA to off-SAA and change drift.
NEW_HOLDING_CATEGORIES = {
    "IDHQ": "intl_quality",
    "AVIV": "intl_large_value",
    "AVDV": "intl_small_value",
}


def _patch_db(db_path: Path) -> None:
    if not db_path.exists():
        print(f"  SKIP: {db_path} — not found")
        return
    print(f"\nPatching {db_path} ...")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        core = next(s for s in _INTL if s["name"] == NEW_CORE)

        # ── 1. rename the existing sleeve in place (preserves asset_class_id) ──
        row = conn.execute(
            "SELECT asset_class_id FROM asset_classes WHERE name = ?", (OLD_NAME,)
        ).fetchone()
        if row is not None:
            conn.execute(
                "UPDATE asset_classes SET name = ? WHERE asset_class_id = ?",
                (NEW_CORE, row["asset_class_id"]),
            )
            print(f"  renamed {OLD_NAME!r} -> {NEW_CORE!r} "
                  f"(asset_class_id {row['asset_class_id']} preserved)")
        elif conn.execute(
            "SELECT 1 FROM asset_classes WHERE name = ?", (NEW_CORE,)
        ).fetchone() is None:
            raise RuntimeError(
                f"Neither {OLD_NAME!r} nor {NEW_CORE!r} present in {db_path} — "
                "refusing to guess which sleeve to split."
            )
        else:
            print(f"  {NEW_CORE!r} already present — rename already applied")

        # ── 2. resolve the Equity parent by NAME, never by a hardcoded id ──────
        parent = conn.execute(
            "SELECT asset_class_id FROM asset_classes "
            "WHERE name = 'Equity' AND parent_id IS NULL"
        ).fetchone()
        if parent is None:
            raise RuntimeError(f"No 'Equity' parent row in {db_path}")
        equity_id = parent["asset_class_id"]

        # ── 3. upsert all four sleeves (Core exists post-rename; three are new) ─
        for spec in _INTL:
            existing = conn.execute(
                "SELECT asset_class_id FROM asset_classes WHERE name = ?",
                (spec["name"],),
            ).fetchone()
            if existing is None:
                conn.execute(
                    """INSERT INTO asset_classes
                         (name, parent_id, target_weight, tolerance_band,
                          sort_order, rationale, benchmark_ticker)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (spec["name"], equity_id, spec["target_weight"],
                     spec["tolerance_band"], SORT_ORDERS[spec["name"]],
                     spec["rationale"], spec["benchmark_ticker"]),
                )
                print(f"  inserted sleeve {spec['name']!r}")
            else:
                conn.execute(
                    """UPDATE asset_classes
                          SET parent_id = ?, target_weight = ?, tolerance_band = ?,
                              sort_order = ?, rationale = ?, benchmark_ticker = ?
                        WHERE asset_class_id = ?""",
                    (equity_id, spec["target_weight"], spec["tolerance_band"],
                     SORT_ORDERS[spec["name"]], spec["rationale"],
                     spec["benchmark_ticker"], existing["asset_class_id"]),
                )
                print(f"  updated sleeve {spec['name']!r}")

        # ── 4. the Emerging Markets rationale gained its cap-weight argument ────
        em = next(s for s in SUB_CLASSES if s["name"] == "Emerging Markets")
        conn.execute(
            "UPDATE asset_classes SET rationale = ? WHERE name = ? AND parent_id IS NOT NULL",
            (em["rationale"], "Emerging Markets"),
        )

        # ── 5. securities: three new holdings + three new benchmark rows ────────
        ac_map = {
            r["name"]: r["asset_class_id"]
            for r in conn.execute("SELECT asset_class_id, name FROM asset_classes")
        }
        uses_categories = conn.execute(
            "SELECT 1 FROM securities WHERE sleeve_category IS NOT NULL LIMIT 1"
        ).fetchone() is not None

        new_tickers = set(NEW_HOLDING_CATEGORIES) | {"IQLT", "EFV", "SCZ"}
        for spec in HOLDINGS + BENCHMARKS:
            if spec["ticker"] not in new_tickers:
                continue
            ac_id = ac_map.get(spec["asset_class"])
            if ac_id is None:
                raise RuntimeError(
                    f"Sleeve {spec['asset_class']!r} missing for {spec['ticker']} "
                    f"in {db_path} — refusing to insert an orphaned security."
                )
            if conn.execute(
                "SELECT 1 FROM securities WHERE ticker = ?", (spec["ticker"],)
            ).fetchone() is None:
                conn.execute(
                    """INSERT INTO securities
                         (ticker, name, asset_class_id, security_type,
                          expense_ratio, holding_rationale)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (spec["ticker"], spec["name"], ac_id,
                     spec.get("security_type"), spec.get("expense_ratio"),
                     spec.get("holding_rationale")),
                )
                print(f"  inserted security {spec['ticker']}")
            else:
                conn.execute(
                    "UPDATE securities SET asset_class_id = ? WHERE ticker = ?",
                    (ac_id, spec["ticker"]),
                )

            if uses_categories and spec["ticker"] in NEW_HOLDING_CATEGORIES:
                conn.execute(
                    "UPDATE securities SET sleeve_category = ?, is_in_saa = 1 "
                    "WHERE ticker = ?",
                    (NEW_HOLDING_CATEGORIES[spec["ticker"]], spec["ticker"]),
                )
                print(f"    {spec['ticker']} sleeve_category = "
                      f"{NEW_HOLDING_CATEGORIES[spec['ticker']]}")

        conn.commit()

        # ── 6. read back and assert, rather than trusting the writes ────────────
        rows = {
            r["name"]: (r["target_weight"], r["tolerance_band"])
            for r in conn.execute(
                "SELECT name, target_weight, tolerance_band FROM asset_classes "
                "WHERE parent_id IS NOT NULL AND target_weight > 0"
            )
        }
        intl_total = sum(rows[s["name"]][0] for s in _INTL)
        expected   = 0.20 / 0.98
        assert abs(intl_total - expected) < 1e-12, (
            f"international total {intl_total!r} != {expected!r}"
        )
        for s in _INTL:
            band = rows[s["name"]][1]
            assert band == 0.02, f"{s['name']} band is {band}, expected 0.02"
        total = sum(w for w, _ in rows.values())
        assert abs(total - 1.0) < 1e-9, f"sub-class targets sum to {total!r}, not 1.0"
        assert conn.execute(
            "SELECT 1 FROM asset_classes WHERE name = ?", (OLD_NAME,)
        ).fetchone() is None, f"{OLD_NAME!r} still present"

        print(f"  OK — 4 international sleeves, bands read back at 0.02, "
              f"sub-classes sum to {total:.12f}")
    finally:
        conn.close()


def main() -> None:
    # DEMO ONLY — deliberately not tracker.db.
    #
    # This migration writes strategic TARGETS. Targets and the holdings that
    # fund them have to land together: a sleeve carrying a target with nothing
    # in it is an unfunded strategic position, and the Suggested-$ invariant in
    # src/rebalance.py:267 fires on exactly that. It is right to fire.
    #
    # An earlier version of this file patched both DBs. That put Intl Quality
    # (6.25%), Large Value (3.75%) and Small Value (3.33%) into the personal
    # book with zero holdings behind them, because the funding trades are
    # paper-only and correctly guarded to demo.db. The invariant then fired
    # against the real household — the alarm was accurate, the state was not.
    #
    # The seam is the DB, not the invariant, because the two databases are at
    # genuinely different points: the demo book can be rebalanced by fiat in the
    # same migration, the personal book cannot — it moves when real trades are
    # placed. Splitting on that boundary keeps each DB internally coherent
    # rather than making the check tolerate an incoherent one.
    #
    # The personal restructure lands as its own migration when the real trades
    # exist, writing targets and holdings atomically so the invariant never
    # fires. Until then tracker.db keeps its coherent 9-sleeve book.
    _patch_db(ROOT / "data" / "demo.db")


if __name__ == "__main__":
    main()
