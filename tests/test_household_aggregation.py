"""Tests for Phase 25.5: household allocation aggregation.

Snapshot-style tests against the real Jul-08-2026 positions CSV (newest dated
export in data/uploads/). All live tests skip automatically if the CSV is
not present.
"""
import pathlib
import sqlite3

import pandas as pd
import pytest

from src.household_data import find_latest_positions_csv

ROOT        = pathlib.Path(__file__).resolve().parent.parent
TRACKER_DB  = ROOT / "data" / "tracker.db"
# Newest dated positions CSV in data/uploads/ (None if the personal file is absent).
HOLDINGS_CSV = find_latest_positions_csv()

ALLOWED_RATIONALE = {"no_exposure", "on_target", "underweight", "overweight", "off_saa_exposure"}

# Actuals computed from the Jul-08 data (look_through + total scope).
# Assertions below use ±2pp tolerance.
ACTUAL_LT_WEIGHTS = {
    "US Large Core":         25.77,
    "International Developed": 12.75,
    "Emerging Markets":       4.79,
    "Core Fixed Income":      3.94,
    "US Large Quality":       2.49,
    "Real Assets":            1.52,
    "cash":                   4.23,   # off-SAA post-38a (cash untargeted, routes to off-SAA bucket)
    "US Large Value":         1.04,
    "US Small Cap":           0.07,   # AVUV added in the Jul-08 export (was 0 on May-27)
    "TIPS":                   0.04,   # SCHP added in the Jul-08 export (was 0 on May-27)
}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _tracker_db_has_schema() -> bool:
    """True only if tracker.db exists AND carries the schema (the trades table)."""
    if not TRACKER_DB.exists() or TRACKER_DB.stat().st_size == 0:
        return False
    conn = sqlite3.connect(str(TRACKER_DB))
    try:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='trades'"
        ).fetchone() is not None
    finally:
        conn.close()


def _skip_if_no_holdings():
    # These tests read BOTH the positions CSV and tracker.db. A CSV alone is not
    # enough: on a machine with a CSV but an unpopulated tracker.db (no schema) the
    # reads below would error, not skip — masking nothing real. Require both.
    if HOLDINGS_CSV is None or not HOLDINGS_CSV.exists():
        pytest.skip("Holdings CSV not present (personal-mode only)")
    if not _tracker_db_has_schema():
        pytest.skip("tracker.db not populated (no schema / trades table) — personal-mode only")


def _load_fixtures():
    """Load all DataFrames needed by compute_household_allocation from tracker.db."""
    from src.ingestion.fidelity import parse_fidelity_csv
    positions_df = parse_fidelity_csv(HOLDINGS_CSV)

    conn = sqlite3.connect(str(TRACKER_DB))
    accounts_df     = pd.read_sql_query("SELECT * FROM accounts", conn)
    securities_df   = pd.read_sql_query("SELECT * FROM securities", conn)
    compositions_df = pd.read_sql_query("SELECT * FROM fund_compositions", conn)
    saa_targets_df  = pd.read_sql_query(
        "SELECT asset_class_id, name, target_weight "
        "FROM asset_classes WHERE parent_id IS NOT NULL AND target_weight > 0",
        conn,
    )
    conn.close()
    return positions_df, accounts_df, securities_df, compositions_df, saa_targets_df


# ── Dollar-sum assertions ──────────────────────────────────────────────────────

def test_look_through_total_sums_to_portfolio_value():
    _skip_if_no_holdings()
    from src.household import compute_household_allocation
    pos, acct, sec, comp, saa = _load_fixtures()
    result = compute_household_allocation(pos, acct, sec, comp, saa,
                                          mode="look_through", scope="total")
    total = result["dollar_value"].sum()
    # Invariant: look-through redistributes fund value into sleeves but conserves
    # the total — it must equal the raw Current value sum from the source CSV. No
    # hardcoded portfolio figure: never goes stale, no real $ in tracked source.
    source_total = pos["current_value"].sum()
    assert abs(total - source_total) <= 1.0, (
        f"look_through/total sum {total:,.2f} != source Current value sum {source_total:,.2f}"
    )


def test_self_only_sums_to_self_directed_value():
    _skip_if_no_holdings()
    from src.household import compute_household_allocation
    pos, acct, sec, comp, saa = _load_fixtures()
    result = compute_household_allocation(pos, acct, sec, comp, saa,
                                          mode="look_through", scope="self_only")
    total = result["dollar_value"].sum()
    # Invariant: self_only conserves the value of the self-directed accounts —
    # the sum of Current value for positions whose account is managed_by='self'
    # (the exact filter self_only applies). No hardcoded figure.
    self_pseudos = set(acct.loc[acct["managed_by"] == "self", "pseudonym"].dropna())
    source_self = pos.loc[pos["pseudonym"].isin(self_pseudos), "current_value"].sum()
    assert abs(total - source_self) <= 1.0, (
        f"self_only sum {total:,.2f} != source self-directed sum {source_self:,.2f}"
    )


def test_self_and_external_sum_to_total():
    _skip_if_no_holdings()
    from src.household import compute_household_allocation
    pos, acct, sec, comp, saa = _load_fixtures()
    self_total = compute_household_allocation(
        pos, acct, sec, comp, saa, mode="look_through", scope="self_only"
    )["dollar_value"].sum()
    ext_total = compute_household_allocation(
        pos, acct, sec, comp, saa, mode="look_through", scope="external_only"
    )["dollar_value"].sum()
    combined = self_total + ext_total
    # Invariant: self + external partition the portfolio — their sum equals the
    # source Current value total (conservation), no hardcoded figure.
    source_total = pos["current_value"].sum()
    assert abs(combined - source_total) <= 1.0, (
        f"self ({self_total:,.2f}) + external ({ext_total:,.2f}) = {combined:,.2f} "
        f"!= source total {source_total:,.2f}"
    )


# ── Sleeve weight assertions (look_through, total scope) ──────────────────────

@pytest.fixture(scope="module")
def lt_total_result():
    _skip_if_no_holdings()
    from src.household import compute_household_allocation
    pos, acct, sec, comp, saa = _load_fixtures()
    return compute_household_allocation(pos, acct, sec, comp, saa,
                                        mode="look_through", scope="total")


def test_us_large_core_is_largest_saa_sleeve(lt_total_result):
    saa_rows = lt_total_result[~lt_total_result["is_off_saa"]]
    largest = saa_rows.loc[saa_rows["dollar_value"].idxmax(), "sleeve"]
    assert largest == "US Large Core", (
        f"Expected US Large Core as largest SAA sleeve, got {largest!r}"
    )


@pytest.mark.parametrize("sleeve,expected_pct", list(ACTUAL_LT_WEIGHTS.items()))
def test_sleeve_weight_within_2pp(lt_total_result, sleeve, expected_pct):
    # Actuals were computed from May-27 data; tolerance allows small data drift.
    row = lt_total_result[lt_total_result["sleeve"] == sleeve]
    actual = float(row["percent_weight"].iloc[0]) if not row.empty else 0.0
    assert abs(actual - expected_pct) <= 2.0, (
        f"{sleeve}: actual {actual:.2f}% differs from snapshot {expected_pct:.2f}% by >2pp"
    )


def test_tips_and_small_cap_now_held_underweight(lt_total_result):
    # The Jul-08 export introduced SCHP (TIPS) and AVUV (US Small Value/Cap),
    # which were absent on May-27. Both SAA sleeves are now held but tiny —
    # far below target, so 'underweight' rather than the former 'no_exposure'.
    for sleeve in ("TIPS", "US Small Cap"):
        row = lt_total_result[lt_total_result["sleeve"] == sleeve]
        assert not row.empty, f"{sleeve} row missing from allocation table"
        assert row.iloc[0]["dollar_value"] > 0.0, (
            f"{sleeve} should now hold >$0 (SCHP/AVUV added in Jul-08 export)"
        )
        assert row.iloc[0]["rationale"] == "underweight", (
            f"{sleeve} should be underweight (held but far below target), "
            f"got {row.iloc[0]['rationale']!r}"
        )
        assert not row.iloc[0]["is_off_saa"], f"{sleeve} is an SAA sleeve"


# ── as_held mode: fund sleeves not decomposed ─────────────────────────────────

def test_as_held_funds_stay_in_fund_sleeves():
    _skip_if_no_holdings()
    from src.household import compute_household_allocation
    pos, acct, sec, comp, saa = _load_fixtures()
    result = compute_household_allocation(pos, acct, sec, comp, saa,
                                          mode="as_held", scope="total")
    sleeves = set(result["sleeve"])
    assert "target_date" in sleeves, "RFUTX/31564E540 should appear as target_date in as_held mode"
    assert "multi_asset" in sleeves, "GAOSX should appear as multi_asset in as_held mode"
    # SAA-decomposed sleeves from look-through should NOT dominate in as_held
    # (target_date alone should be the biggest sleeve at ~33%)
    td_row = result[result["sleeve"] == "target_date"]
    assert td_row["dollar_value"].sum() > 60_000, (
        "target_date sleeve should hold >$60k (RFUTX + 31564E540) in as_held mode"
    )


# ── off-SAA properties ────────────────────────────────────────────────────────

def test_off_saa_categories_have_zero_target_and_correct_flag(lt_total_result):
    off_saa = lt_total_result[lt_total_result["is_off_saa"]]
    assert len(off_saa) > 0, "Expected off-SAA rows in total allocation"
    for _, row in off_saa.iterrows():
        assert row["target_weight"] == 0.0, (
            f"Off-SAA sleeve {row['sleeve']!r} should have target_weight=0, "
            f"got {row['target_weight']}"
        )
        assert row["rationale"] == "off_saa_exposure", (
            f"Off-SAA sleeve {row['sleeve']!r} should have rationale=off_saa_exposure"
        )


def test_thematic_and_hedged_equity_are_off_saa(lt_total_result):
    for sleeve in ("thematic", "hedged_equity"):
        row = lt_total_result[lt_total_result["sleeve"] == sleeve]
        assert not row.empty, f"{sleeve} row missing"
        assert row.iloc[0]["is_off_saa"], f"{sleeve} should be off-SAA"


def test_us_small_core_is_off_saa_not_merged_into_us_small_cap(lt_total_result):
    # us_small_core from fund look-through must NOT be merged into "US Small Cap" target.
    # The two sleeves are distinct: us_small_core (broad) vs us_small_value (factor-tilted).
    sc_row = lt_total_result[lt_total_result["sleeve"] == "us_small_core"]
    assert not sc_row.empty, "us_small_core should appear as its own off-SAA row"
    assert sc_row.iloc[0]["is_off_saa"], "us_small_core must be off_saa (SAA targets us_small_value)"
    assert sc_row.iloc[0]["dollar_value"] > 15_000, (
        "us_small_core should hold >$15k (look-through from RFUTX + 31564E540 + direct)"
    )


# ── Rationale vocabulary ──────────────────────────────────────────────────────

def test_all_rationale_values_are_valid(lt_total_result):
    bad = lt_total_result[~lt_total_result["rationale"].isin(ALLOWED_RATIONALE)]
    assert bad.empty, f"Invalid rationale values found:\n{bad[['sleeve','rationale']]}"


# ── Internal sum assertion holds for all three scopes ─────────────────────────

def test_internal_sum_assertion_passes_for_all_scopes():
    """The function asserts internally; if it runs without raising, the assertion holds."""
    _skip_if_no_holdings()
    from src.household import compute_household_allocation
    pos, acct, sec, comp, saa = _load_fixtures()
    for scope in ("total", "self_only", "external_only"):
        # Would raise AssertionError inside the function if sum doesn't match.
        compute_household_allocation(pos, acct, sec, comp, saa,
                                     mode="look_through", scope=scope)


# ── household_summary ─────────────────────────────────────────────────────────

def test_household_summary_keys_and_values():
    _skip_if_no_holdings()
    from src.household import household_summary
    pos, acct, *_ = _load_fixtures()
    summary = household_summary(pos, acct, as_of_date="2026-07-08")
    assert set(summary.keys()) == {"total_aum", "account_count", "as_of_date", "self_aum", "external_aum"}
    assert abs(summary["total_aum"] - PORTFOLIO_TOTAL) <= 1.0
    assert abs(summary["self_aum"] - SELF_TOTAL) <= 1.0
    assert abs(summary["self_aum"] + summary["external_aum"] - summary["total_aum"]) <= 1.0
    assert summary["account_count"] == 7
    assert summary["as_of_date"] == "2026-07-08"


# ── Phase 38b-1 — cash routes to off-SAA (synthetic; runs in CI, no CSV needed) ──

def test_cash_routes_to_off_saa_synthetic():
    """Post-38a, cash is untargeted → it must land in the off-SAA bucket with its
    real dollar value, never as a 0-target SAA sleeve. Synthetic so it guards the
    routing logic in CI regardless of the personal-mode holdings CSV.
    """
    from src.household import compute_household_allocation

    positions_df = pd.DataFrame([
        {"pseudonym": "A1", "symbol": "VOO",   "current_value": 8000.0},
        {"pseudonym": "A1", "symbol": "SPAXX", "current_value": 2000.0},
    ])
    accounts_df = pd.DataFrame([
        {"pseudonym": "A1", "managed_by": "self", "display_name": "Acct",
         "tax_treatment": "taxable"},
    ])
    # SPAXX: in-SAA flag set, but its asset class (cash) is untargeted post-38a.
    securities_df = pd.DataFrame([
        {"ticker": "VOO",   "sleeve_category": "us_large_core", "is_in_saa": 1, "asset_class_id": 10},
        {"ticker": "SPAXX", "sleeve_category": "cash",          "is_in_saa": 1, "asset_class_id": 100},
    ])
    compositions_df = pd.DataFrame(columns=["fund_symbol", "underlying_sleeve", "weight"])
    # saa_targets_df mirrors the page query (target_weight > 0): cash (id 100) absent.
    saa_targets_df = pd.DataFrame([
        {"asset_class_id": 10, "name": "US Large Core", "target_weight": 0.1735},
    ])

    alloc = compute_household_allocation(
        positions_df, accounts_df, securities_df, compositions_df, saa_targets_df,
        mode="look_through", scope="total",
    )

    cash = alloc[alloc["sleeve"] == "cash"]
    assert not cash.empty, "cash holding missing from allocation"
    row = cash.iloc[0]
    assert bool(row["is_off_saa"]) is True, "cash must route to the off-SAA bucket"
    assert row["target_weight"] == 0.0, "off-SAA cash must have no target"
    assert row["rationale"] == "off_saa_exposure"
    assert abs(row["dollar_value"] - 2000.0) < 1e-6, "cash dollar value must be intact"
    # Whole-household denominator (incl cash): cash = 2000 / 10000 = 20%.
    assert abs(row["percent_weight"] - 20.0) < 1e-6, "cash weight is a share of the whole household"
    # Cash is NOT a strategic SAA row.
    saa_rows = alloc[~alloc["is_off_saa"]]
    assert "cash" not in set(saa_rows["sleeve"]), "cash must not appear as a strategic SAA sleeve"
