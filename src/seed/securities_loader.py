"""Household securities sleeve-mapping loader.

Reads data/seed/securities_household.csv and UPSERTs into the securities
table, keyed on ticker (CSV column: symbol).

For existing tickers: enriches sleeve_category, tax_efficiency, is_in_saa
WITHOUT touching any other column (name, asset_class_id, expense_ratio, etc.).

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
