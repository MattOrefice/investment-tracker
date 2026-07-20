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
#
# RFUTX's look-through composition was replaced with real factsheet data (was a
# generic manual estimate with no us_large_value sleeve at all); the unvested,
# $0-vested workplace account was also excluded from the household total. Both
# shift these weights — US Large Value materially (1.04 -> 3.83, since RFUTX's
# factsheet-sourced split carries an explicit value-tilt slice the old estimate
# didn't), the rest by under a point.
ACTUAL_LT_WEIGHTS = {
    "US Large Core":         24.64,
    # Phase 39: VEA (and IEFA, its off-SAA substitute) keep sleeve_category
    # 'intl_developed', which now resolves to International Core — so the whole
    # 11.27 lands there. The three tilt sleeves carry no household position yet.
    "International Core":      11.27,
    "International Quality":    0.00,
    "International Large Value": 0.00,
    "International Small Value": 0.00,
    "Emerging Markets":       4.80,
    "Core Fixed Income":      3.91,
    "US Large Quality":       2.52,
    "Real Assets":            1.51,
    "cash":                   4.90,   # off-SAA post-38a (cash untargeted, routes to off-SAA bucket)
    "US Large Value":         3.83,
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
    # Invariants, not pinned figures: total_aum is the source Current value sum;
    # self_aum is the sum for self-directed (managed_by='self') accounts.
    source_total = pos["current_value"].sum()
    self_pseudos = set(acct.loc[acct["managed_by"] == "self", "pseudonym"].dropna())
    source_self  = pos.loc[pos["pseudonym"].isin(self_pseudos), "current_value"].sum()
    assert abs(summary["total_aum"] - source_total) <= 1.0
    assert abs(summary["self_aum"] - source_self) <= 1.0
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


# ── SAA sleeve coverage — the personal-mode instrument ─────────────────────────
# tests/test_saa_sleeve_coverage.py runs this invariant in CI, but has to
# RECONSTRUCT the securities frame (demo.db carries sleeve_category / is_in_saa
# as NULL — the household mapping is personal-mode only). Personal mode has the
# real columns populated, so these run the same invariant against tracker.db
# through the real path: no reconstruction, no synthetic positions, the actual
# household book.
#
# These do NOT run in CI, deliberately. They are the instrument for verifying the
# SAA change against real data — the only one that exercises the real securities
# table, the real look-through compositions, and the real positions together.

def test_personal_every_strategic_sleeve_appears_on_saa():
    """Every sleeve asset_classes targets must surface on-SAA in the real
    household allocation.

    compute_household_allocation derives its sleeve set from the securities join
    (household.py:151-167), not from asset_classes — so a sleeve whose is_in_saa
    carrier disappears does not become a zero-exposure row. It vanishes: no row,
    no drift, no error, while its target still sits in the DB. The function's own
    assertion (household.py:207) compares DOLLAR sums, which still reconcile
    because the money relabels itself off-SAA.
    """
    _skip_if_no_holdings()
    from src.household import compute_household_allocation, exclude_non_household_positions

    pos, acct, sec, comp, saa = _load_fixtures()
    pos = exclude_non_household_positions(pos, acct)
    alloc = compute_household_allocation(pos, acct, sec, comp, saa,
                                         mode="look_through", scope="total")

    on_saa = set(alloc[~alloc["is_off_saa"]]["sleeve"])
    expected = set(saa["name"])
    assert on_saa == expected, (
        f"On-SAA sleeves in the real household allocation do not match asset_classes.\n"
        f"  missing from the frame (silently dropped): {sorted(expected - on_saa)}\n"
        f"  present but not a strategic target:        {sorted(on_saa - expected)}\n"
        f"A missing sleeve has no is_in_saa carrier in tracker.db's securities table."
    )


def test_personal_on_saa_target_weights_sum_matches_db():
    """The real allocation's on-SAA targets must sum to the DB's, within 1bp.

    A dropped sleeve shows up here as a shortfall of exactly its target — the
    frame keeps reconciling on dollars while the TARGETS silently stop adding up
    to the SAA.
    """
    _skip_if_no_holdings()
    from src.household import compute_household_allocation, exclude_non_household_positions

    pos, acct, sec, comp, saa = _load_fixtures()
    pos = exclude_non_household_positions(pos, acct)
    alloc = compute_household_allocation(pos, acct, sec, comp, saa,
                                         mode="look_through", scope="total")

    frame_sum = float(alloc[~alloc["is_off_saa"]].drop_duplicates("sleeve")["target_weight"].sum())
    db_sum = float(saa["target_weight"].sum())
    assert abs(frame_sum - db_sum) < 1e-4, (
        f"On-SAA target weights sum to {frame_sum:.10f} but asset_classes sums to "
        f"{db_sum:.10f} (delta {abs(frame_sum - db_sum):.2e}). A shortfall equal to "
        f"one sleeve's target means that sleeve is unreachable through the securities join."
    )


def test_personal_guard_fails_when_a_sleeve_loses_its_carrier():
    """Proves the two guards above can FAIL against real data.

    Drops the sole is_in_saa carrier for intl_developed from the real securities
    frame (in memory — tracker.db is never written) and asserts both invariants
    break. Without this, the guards could pass forever without ever having been
    capable of failing.
    """
    _skip_if_no_holdings()
    from src.household import compute_household_allocation, exclude_non_household_positions

    pos, acct, sec, comp, saa = _load_fixtures()
    pos = exclude_non_household_positions(pos, acct)

    carriers = sec[(sec["sleeve_category"] == "intl_developed") & (sec["is_in_saa"] == 1)]
    assert not carriers.empty, (
        "No is_in_saa security carries intl_developed in tracker.db — the mutation "
        "cannot be applied, so the guards above are already unprotected."
    )

    mutated = sec.copy()
    mutated.loc[carriers.index, "is_in_saa"] = 0
    alloc = compute_household_allocation(pos, acct, mutated, comp, saa,
                                         mode="look_through", scope="total")

    on_saa = set(alloc[~alloc["is_off_saa"]]["sleeve"])
    expected = set(saa["name"])
    assert on_saa != expected, (
        "Guard did NOT fail under mutation — dropping the sole intl_developed "
        "carrier left the on-SAA sleeve set intact. The guard is vacuous."
    )
    dropped = expected - on_saa
    assert dropped, "Expected a sleeve to disappear; none did."
    assert not any(alloc["sleeve"].isin(dropped)), (
        f"{sorted(dropped)} still has a row — the failure mode is a TOTAL "
        f"disappearance, not a zero-exposure row."
    )

    frame_sum = float(alloc[~alloc["is_off_saa"]].drop_duplicates("sleeve")["target_weight"].sum())
    db_sum = float(saa["target_weight"].sum())
    assert abs(frame_sum - db_sum) >= 1e-4, (
        "Target-sum guard did NOT fail under mutation. The guard is vacuous."
    )
    lost = float(saa[saa["name"].isin(dropped)]["target_weight"].sum())
    assert abs((db_sum - frame_sum) - lost) < 1e-9, (
        f"Shortfall ({db_sum - frame_sum:.10f}) should equal the dropped sleeve's "
        f"target ({lost:.10f})."
    )
