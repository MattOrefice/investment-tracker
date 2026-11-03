"""Household securities sleeve-mapping loader.

Reads data/seed/securities_household.csv and UPSERTs into the securities
table, keyed on ticker (CSV column: symbol).

For existing tickers: updates name (from the CSV's description column),
sleeve_category, tax_efficiency and is_in_saa, WITHOUT touching any other column
(asset_class_id, expense_ratio, etc.). The seed is the source for names (#707): a
name corrected in the seed reaches a book seeded before the correction. No ticker
may carry two different names across the seeds (tests/test_seed_names.py), so the
SAA seed (src/seed_securities.py) and this one never overwrite each other.

For new tickers: inserts with the parking-lot asset_class_id ('Other / Non-SAA')
so the NOT NULL constraint is satisfied.

Personal-mode only: run against tracker.db. Do not run against demo.db.
"""
import csv
import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

PARKING_LOT_NAME = "Other / Non-SAA"
DEFAULT_CSV = Path(__file__).resolve().parents[2] / "data" / "seed" / "securities_household.csv"


def _get_parking_lot_id(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT asset_class_id FROM asset_classes WHERE name = ?",
        (PARKING_LOT_NAME,),
    ).fetchone()
    if row is None:
        raise RuntimeError(
            f"Parking-lot asset class {PARKING_LOT_NAME!r} not found. "
            "Run migrate_securities_phase25_3.py first."
        )
    return row["asset_class_id"]


def load_household_securities(db_path: str | Path, csv_path: str | Path) -> dict:
    """UPSERT household securities from CSV into the securities table.

    Returns a summary dict: {inserted, updated, skipped, errors}.
    """
    db_path  = Path(db_path)
    csv_path = Path(csv_path)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    counts = {"inserted": 0, "updated": 0, "skipped": 0, "errors": 0}

    try:
        parking_lot_id = _get_parking_lot_id(conn)

        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                symbol       = row["symbol"].strip()
                description  = row["description"].strip()
                sleeve_cat   = row["sleeve_category"].strip()
                tax_eff      = row["tax_efficiency"].strip()
                is_in_saa    = 1 if row["is_in_saa"].strip().lower() == "true" else 0

                if not symbol:
                    counts["skipped"] += 1
                    continue

                try:
                    conn.execute(
                        """
                        INSERT INTO securities
                            (ticker, name, asset_class_id,
                             sleeve_category, tax_efficiency, is_in_saa)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(ticker) DO UPDATE SET
                            name            = excluded.name,
                            sleeve_category = excluded.sleeve_category,
                            tax_efficiency  = excluded.tax_efficiency,
                            is_in_saa       = excluded.is_in_saa
                        """,
                        (symbol, description, parking_lot_id,
                         sleeve_cat, tax_eff, is_in_saa),
                    )
                    # Distinguish insert vs update for reporting
                    if conn.execute(
                        "SELECT changes()"
                    ).fetchone()[0] == 1:
                        # changes()==1 means one row was written; check if it was
                        # an insert (new ticker) by looking at total_changes context
                        pass
                    counts["updated"] += 1  # insert and update both count
                except Exception:
                    logger.warning("Failed to load security %r — skipping", symbol, exc_info=True)
                    counts["errors"] += 1

        conn.commit()
        logger.info(
            "Loaded household securities from %s: %d rows processed, %d errors",
            csv_path.name, counts["updated"], counts["errors"],
        )
    finally:
        conn.close()

    return counts


def main(argv: "list[str] | None" = None) -> int:
    """The loader's own entry point: ``python -m src.seed.securities_loader <db>``.

    Loads data/seed/securities_household.csv (or a second argument) into the named
    book and prints the counts. The book is named explicitly, never defaulted, so a
    run cannot write a book it did not mean to.
    """
    import sys
    args = sys.argv[1:] if argv is None else argv
    if not args or len(args) > 2:
        print("usage: python -m src.seed.securities_loader <db> [<seed.csv>]")
        return 2
    db = Path(args[0])
    if not db.exists():
        print(f"no book at {db}")
        return 2
    counts = load_household_securities(db, Path(args[1]) if len(args) == 2 else DEFAULT_CSV)
    print(counts)
    return 1 if counts["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
