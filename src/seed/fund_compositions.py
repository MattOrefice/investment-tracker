"""Seed approximate fund composition look-through weights.

Each fund's rows must sum to 1.00 ± 0.01. Uses existing sleeve_category
vocabulary from the securities table — no new sleeve names are introduced.

Source: manual estimates as of 2026-05-27 based on fund prospectus/
factsheet disclosures. Underlying sleeve names match the canonical
sleeve_category vocabulary established in Phase 25.3.

Personal-mode only: run against tracker.db, never demo.db.
"""
import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

AS_OF_DATE = "2026-05-27"
SOURCE     = "manual_estimate_2026_05"

# (fund_symbol, underlying_sleeve, weight)
# Each fund's weights must sum to 1.00 ± 0.01 — asserted in seed_fund_compositions().
_COMPOSITIONS: list[tuple[str, str, float]] = [
    # RFUTX — American Funds 2060 Target Date R6
    # ~2060 glide path: ~90% equity / 10% bonds at current vintage
    ("RFUTX", "us_large_core",           0.45),
    ("RFUTX", "us_small_core",           0.10),
    ("RFUTX", "intl_developed",          0.27),
    ("RFUTX", "emerging_markets",        0.08),
    ("RFUTX", "core_fi_treasury",        0.08),
    ("RFUTX", "cash",                    0.02),

    # GAOSX — JPMorgan Global Allocation Fund I
    # Flexible multi-asset; ~62% equity, ~28% FI, ~10% real/cash
    ("GAOSX", "us_large_core",           0.42),
    ("GAOSX", "intl_developed",          0.15),
    ("GAOSX", "emerging_markets",        0.05),
    ("GAOSX", "core_fi_treasury",        0.22),
    ("GAOSX", "high_yield_fi",           0.06),
    ("GAOSX", "real_assets_commodities", 0.05),
    ("GAOSX", "cash",                    0.05),

    # 31564E540 — Fidelity Freedom Index 2065 Fund Class T
    # ~2065 glide path: ~90% equity / 10% bonds at current vintage
    ("31564E540", "us_large_core",       0.45),
    ("31564E540", "us_small_core",       0.10),
    ("31564E540", "intl_developed",      0.28),
    ("31564E540", "emerging_markets",    0.07),
    ("31564E540", "core_fi_treasury",    0.08),
    ("31564E540", "cash",                0.02),
]


def _validate_weights() -> None:
    """Assert each fund's rows sum to 1.00 ± 0.01."""
    from itertools import groupby
    by_fund: dict[str, float] = {}
    for symbol, _, weight in _COMPOSITIONS:
        by_fund[symbol] = by_fund.get(symbol, 0.0) + weight
    for symbol, total in by_fund.items():
        assert abs(total - 1.0) <= 0.01, (
            f"fund_compositions weight sum for {symbol!r} = {total:.4f}, not 1.00 ± 0.01"
        )


def seed_fund_compositions(db_path: str | Path) -> int:
    """UPSERT fund composition rows. Returns the number of rows written."""
    _validate_weights()
    db_path = Path(db_path)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executemany(
            """
            INSERT INTO fund_compositions
                (fund_symbol, underlying_sleeve, weight, as_of_date, source)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(fund_symbol, underlying_sleeve) DO UPDATE SET
                weight     = excluded.weight,
                as_of_date = excluded.as_of_date,
                source     = excluded.source
            """,
            [
                (symbol, sleeve, weight, AS_OF_DATE, SOURCE)
                for symbol, sleeve, weight in _COMPOSITIONS
            ],
        )
        conn.commit()
        n = len(_COMPOSITIONS)
        logger.info("Seeded %d fund composition rows in %s", n, db_path)
        return n
    finally:
        conn.close()
