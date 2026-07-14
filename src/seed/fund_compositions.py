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

_MANUAL_AS_OF = "2026-05-27"
_MANUAL_SOURCE = "manual_estimate_2026_05"
_AMFUNDS_2060_AS_OF = "2026-03-31"
_AMFUNDS_2060_SOURCE = "factsheet_2026_03_AmFunds2060"

# (fund_symbol, underlying_sleeve, weight, as_of_date, source)
# Each fund's weights must sum to 1.00 ± 0.01 — asserted in seed_fund_compositions().
_COMPOSITIONS: list[tuple[str, str, float, str, str]] = [
    # RFUTX — American Funds 2060 Target Date Retirement Fund (R6). Sourced from
    # the fund factsheet (2026-03-31): Stocks 87.76 / Bonds 7.70 / Cash 3.82 /
    # Other 0.72; US 66.73 / ex-US ~33.27. The sleeve-level split below is
    # APPROXIMATED to hit those published totals — global sub-funds' US/intl
    # split and the small-cap/quality mapping are reasoned, not exact
    # holdings-level data (American Funds has no dedicated quality product,
    # so that sleeve is 0; growth+blend collapse to core, matching the SAA's
    # own sleeve set, which has no growth sleeve). Rounding remainder (2pp)
    # absorbed into us_large_core per that same reasoning.
    ("RFUTX", "us_large_core",    0.42, _AMFUNDS_2060_AS_OF, _AMFUNDS_2060_SOURCE),
    ("RFUTX", "us_large_value",   0.08, _AMFUNDS_2060_AS_OF, _AMFUNDS_2060_SOURCE),
    ("RFUTX", "us_small_core",    0.07, _AMFUNDS_2060_AS_OF, _AMFUNDS_2060_SOURCE),
    ("RFUTX", "intl_developed",   0.23, _AMFUNDS_2060_AS_OF, _AMFUNDS_2060_SOURCE),
    ("RFUTX", "emerging_markets", 0.08, _AMFUNDS_2060_AS_OF, _AMFUNDS_2060_SOURCE),
    ("RFUTX", "core_fi_treasury", 0.08, _AMFUNDS_2060_AS_OF, _AMFUNDS_2060_SOURCE),
    ("RFUTX", "cash",             0.04, _AMFUNDS_2060_AS_OF, _AMFUNDS_2060_SOURCE),

    # GAOSX — JPMorgan Global Allocation Fund I
    # Flexible multi-asset; ~62% equity, ~28% FI, ~10% real/cash
    ("GAOSX", "us_large_core",           0.42, _MANUAL_AS_OF, _MANUAL_SOURCE),
    ("GAOSX", "intl_developed",          0.15, _MANUAL_AS_OF, _MANUAL_SOURCE),
    ("GAOSX", "emerging_markets",        0.05, _MANUAL_AS_OF, _MANUAL_SOURCE),
    ("GAOSX", "core_fi_treasury",        0.22, _MANUAL_AS_OF, _MANUAL_SOURCE),
    ("GAOSX", "high_yield_fi",           0.06, _MANUAL_AS_OF, _MANUAL_SOURCE),
    ("GAOSX", "real_assets_commodities", 0.05, _MANUAL_AS_OF, _MANUAL_SOURCE),
    ("GAOSX", "cash",                    0.05, _MANUAL_AS_OF, _MANUAL_SOURCE),

    # 31564E540 — Fidelity Freedom Index 2065 Fund Class T
    # ~2065 glide path: ~90% equity / 10% bonds at current vintage
    ("31564E540", "us_large_core",       0.45, _MANUAL_AS_OF, _MANUAL_SOURCE),
    ("31564E540", "us_small_core",       0.10, _MANUAL_AS_OF, _MANUAL_SOURCE),
    ("31564E540", "intl_developed",      0.28, _MANUAL_AS_OF, _MANUAL_SOURCE),
    ("31564E540", "emerging_markets",    0.07, _MANUAL_AS_OF, _MANUAL_SOURCE),
    ("31564E540", "core_fi_treasury",    0.08, _MANUAL_AS_OF, _MANUAL_SOURCE),
    ("31564E540", "cash",                0.02, _MANUAL_AS_OF, _MANUAL_SOURCE),
]


def _validate_weights() -> None:
    """Assert each fund's rows sum to 1.00 ± 0.01."""
    by_fund: dict[str, float] = {}
    for symbol, _, weight, _as_of, _source in _COMPOSITIONS:
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
            _COMPOSITIONS,
        )
        conn.commit()
        n = len(_COMPOSITIONS)
        logger.info("Seeded %d fund composition rows in %s", n, db_path)
        return n
    finally:
        conn.close()
