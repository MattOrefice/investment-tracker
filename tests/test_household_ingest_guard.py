"""Guard: every ticker in the newest positions CSV must resolve through
look_through_position without raising.

This is the reason the Jul-08 ingest-refresh phase existed: an unseeded ticker
(UFO) raised ValueError inside look_through_position, which hard-errored the
Household View page at load. This test fails loudly in CI the moment a future
export introduces a ticker that is missing from the securities table, instead
of that surfacing as a page crash.

Skips cleanly when the personal-mode inputs are absent (empty data/uploads/,
no private/account_map.json, or no data/tracker.db) so demo-mode CI stays green.
"""
import pathlib
import sqlite3

import pandas as pd
import pytest

ROOT        = pathlib.Path(__file__).resolve().parent.parent
TRACKER_DB  = ROOT / "data" / "tracker.db"
ACCOUNT_MAP = ROOT / "private" / "account_map.json"


def _skip_unless_personal_inputs_present():
    from src.household_data import find_latest_positions_csv
    csv = find_latest_positions_csv()
    if csv is None:
        pytest.skip("No Portfolio_Positions_*.csv in data/uploads/ (personal-mode only)")
    if not ACCOUNT_MAP.exists():
        pytest.skip("private/account_map.json absent (personal-mode only)")
    if not TRACKER_DB.exists() or TRACKER_DB.stat().st_size == 0:
        pytest.skip("data/tracker.db absent/empty (personal-mode only)")
    return csv


def test_every_newest_csv_ticker_resolves_through_look_through():
    """Read the REAL newest CSV and call the REAL look_through_position per ticker."""
    csv = _skip_unless_personal_inputs_present()

    from src.ingestion.fidelity import parse_fidelity_csv
    from src.household import look_through_position

    df = parse_fidelity_csv(csv)
    conn = sqlite3.connect(str(TRACKER_DB))
    securities_df   = pd.read_sql_query("SELECT * FROM securities", conn)
    compositions_df = pd.read_sql_query("SELECT * FROM fund_compositions", conn)
    conn.close()

    unresolved = []
    for ticker in sorted(df["symbol"].dropna().unique()):
        try:
            look_through_position(ticker, 1000.0, compositions_df, securities_df)
        except ValueError:
            unresolved.append(ticker)

    assert not unresolved, (
        f"{len(unresolved)} ticker(s) in {csv.name} do not resolve through "
        f"look_through_position (unseeded in the securities table): {unresolved}. "
        "Add them to data/seed/securities_household.csv and re-run the loader."
    )
