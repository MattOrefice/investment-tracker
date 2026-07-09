"""Tests for the Asset Location page's data layer (src/household.py additions +
src/location_config.py).

The fixture deliberately has TWO accounts of different tax_treatment holding the
SAME ticker (VNQ in taxable and in a Roth). A single-account or single-treatment
fixture cannot distinguish case A (relocate for tax drag) from case C
(premium-space waste), which is the whole point of this page.
"""
import pathlib
import sqlite3

import pandas as pd
import pytest

from src.household import (
    build_location_register,
    build_deploy_view,
    build_account_breakdown,
    compute_embedded_gain,
    sleeve_display_name,
)
from src.location_config import (
    TAX_PROFILE,
    SLEEVE_LOCATION_PRIORITY,
    ACCOUNT_SHELTER_PRIORITY,
    sleeve_location_priority,
)

ROOT       = pathlib.Path(__file__).resolve().parent.parent
TRACKER_DB = ROOT / "data" / "tracker.db"


# ── Fixture: two accounts, different tax_treatment, same ticker ────────────────

def _fixture():
    accounts_df = pd.DataFrame([
        {"pseudonym": "acct_tax",  "display_name": "Taxable Acct", "tax_treatment": "taxable",  "managed_by": "self"},
        {"pseudonym": "acct_roth", "display_name": "Roth Acct",    "tax_treatment": "roth_ira", "managed_by": "external"},
    ])
    securities_df = pd.DataFrame([
        {"ticker": "VNQ",   "name": "Vanguard Real Estate ETF", "tax_efficiency": "low",    "sleeve_category": "real_assets_reit"},
        {"ticker": "IJR",   "name": "iShares Core Small-Cap",   "tax_efficiency": "high",   "sleeve_category": "us_small_core"},
        {"ticker": "JEPI",  "name": "JPM Equity Premium",       "tax_efficiency": "medium", "sleeve_category": "hedged_equity"},
        {"ticker": "VOO",   "name": "Vanguard S&P 500 ETF",     "tax_efficiency": "high",   "sleeve_category": "us_large_core"},
        {"ticker": "SPAXX", "name": "Fidelity Money Market",    "tax_efficiency": "low",    "sleeve_category": "cash"},
    ])
    # total_gain_loss / cost_basis_total are NaN for the cash rows (as the parser returns them).
    positions_df = pd.DataFrame([
        {"pseudonym": "acct_tax",  "symbol": "VNQ",   "description": "REIT",  "current_value": 10000.0, "total_gain_loss": 2000.0, "cost_basis_total": 8000.0},
        {"pseudonym": "acct_roth", "symbol": "VNQ",   "description": "REIT",  "current_value":  5000.0, "total_gain_loss": 1000.0, "cost_basis_total": 4000.0},
        {"pseudonym": "acct_tax",  "symbol": "IJR",   "description": "SC",    "current_value":  6000.0, "total_gain_loss":  500.0, "cost_basis_total": 5500.0},
        {"pseudonym": "acct_tax",  "symbol": "JEPI",  "description": "HEQ",   "current_value":  4000.0, "total_gain_loss":  300.0, "cost_basis_total": 3700.0},
        {"pseudonym": "acct_tax",  "symbol": "VOO",   "description": "LC",    "current_value":  8000.0, "total_gain_loss": 1000.0, "cost_basis_total": 7000.0},
        {"pseudonym": "acct_tax",  "symbol": "SPAXX", "description": "CASH",  "current_value":   100.0, "total_gain_loss": float("nan"), "cost_basis_total": float("nan")},
        {"pseudonym": "acct_roth", "symbol": "SPAXX", "description": "CASH",  "current_value":   200.0, "total_gain_loss": float("nan"), "cost_basis_total": float("nan")},
    ])
    return positions_df, accounts_df, securities_df


def _register():
    pos, acct, sec = _fixture()
    return build_location_register(pos, acct, sec, TAX_PROFILE,
                                   SLEEVE_LOCATION_PRIORITY, ACCOUNT_SHELTER_PRIORITY)


# ── Case C: the case the existing function cannot see ──────────────────────────

def test_case_c_fires_for_low_efficiency_in_roth():
    reg = _register()
    c = reg[(reg["symbol"] == "VNQ") & (reg["case"] == "C")]
    assert len(c) == 1, "VNQ in the Roth (low efficiency) must produce a case-C row"
    assert c.iloc[0]["account"] == "Roth Acct"


def test_all_four_cases_present():
    reg = _register()
    assert set(reg["case"]) == {"A", "B", "C", "D"}, f"cases present: {sorted(set(reg['case']))}"


def test_correctly_placed_holding_produces_no_row():
    # VOO: high efficiency in taxable, us_large_core (priority 5, not 1–4). No case.
    reg = _register()
    assert reg[reg["symbol"] == "VOO"].empty, "high-efficiency, non-priority-1–4 in taxable must not be flagged"


# ── In-shelter moves are free ──────────────────────────────────────────────────

def test_in_shelter_move_is_free():
    reg = _register()
    c = reg[(reg["symbol"] == "VNQ") & (reg["case"] == "C")].iloc[0]
    assert c["cost_to_realize"] == 0.0, "an in-shelter (Roth) sale must have zero realization cost"
    assert c["is_free"] is True or bool(c["is_free"]) is True
    assert pd.isna(c["payback_months"]), "a free action has no payback period"


def test_taxable_move_has_positive_cost_and_payback():
    reg = _register()
    # VNQ in taxable: gain 2000 * (0 + 0.0307) = 61.4 cost.
    a = reg[(reg["symbol"] == "VNQ") & (reg["case"] == "A")].iloc[0]
    assert a["cost_to_realize"] == pytest.approx(2000.0 * 0.0307, abs=0.01)
    assert not bool(a["is_free"])
    assert a["payback_months"] is not None and a["payback_months"] > 0


# ── build_deploy_view: absent sleeve excluded, not sorted last ─────────────────

def test_deploy_view_excludes_priority_none_sleeve():
    pos, acct, sec = _fixture()
    dv = build_deploy_view(pos, acct, sec, SLEEVE_LOCATION_PRIORITY, "acct_roth", 1000.0)
    # 'thematic' has no priority -> must not appear as a deploy candidate.
    assert sleeve_display_name("thematic") not in set(dv["sleeve"]), "unranked sleeve leaked into deploy view"
    # And it must not be coerced to a huge sentinel and sorted last.
    assert dv["priority"].notna().all(), "no deploy row may have a null priority"
    assert dv["priority"].max() <= max(SLEEVE_LOCATION_PRIORITY.values()), (
        "an absent sleeve appears to have been sorted last via a large sentinel"
    )
    # Every sleeve shown maps back to a real priority key.
    known = {sleeve_display_name(s) for s in SLEEVE_LOCATION_PRIORITY}
    assert set(dv["sleeve"]).issubset(known)


def test_absent_priority_is_none_not_lowest():
    # Absent -> None (not a deploy target), which is distinct from ranked-last.
    assert sleeve_location_priority("thematic") is None
    assert sleeve_location_priority("multi_asset") is None
    assert sleeve_location_priority("definitely_not_a_sleeve") is None
    assert sleeve_location_priority("us_small_core") == 1
    assert sleeve_location_priority("cash") == 11  # ranked last, and that is a real rank


def test_deploy_view_sorted_by_priority_ascending():
    pos, acct, sec = _fixture()
    dv = build_deploy_view(pos, acct, sec, SLEEVE_LOCATION_PRIORITY, "acct_roth", 1000.0)
    assert list(dv["priority"]) == sorted(dv["priority"]), "deploy view must be priority-ascending"


# ── compute_embedded_gain: exclude NaN, never zero-fill ────────────────────────

def test_embedded_gain_excludes_nan_rows():
    pos, _, _ = _fixture()
    eg, n_excluded = compute_embedded_gain(pos)
    assert n_excluded == 2, f"the two cash rows (NaN gain/cost) must be excluded, got {n_excluded}"
    assert "SPAXX" not in set(eg["symbol"]), "NaN cash rows must be excluded, not zero-filled"
    # VNQ held in two accounts -> two grouped rows.
    assert (eg["symbol"] == "VNQ").sum() == 2
    # A real holding keeps its true (non-zero) gain.
    vnq_tax = eg[(eg["symbol"] == "VNQ") & (eg["pseudonym"] == "acct_tax")].iloc[0]
    assert vnq_tax["embedded_gain"] == 2000.0
    assert vnq_tax["gain_pct"] == pytest.approx(2000.0 / 8000.0 * 100)


def test_embedded_gain_does_not_zero_fill():
    """A NaN gain must not silently become $0 (which would misreport a cash line
    as a fully-realized position)."""
    pos, _, _ = _fixture()
    eg, _ = compute_embedded_gain(pos)
    assert not (eg["embedded_gain"] == 0.0).any(), "no row should carry a zero-filled gain"


# ── build_account_breakdown byte-identity across the refactor ──────────────────

def _old_build_account_breakdown(positions_df, accounts_df, securities_df):
    """The pre-refactor implementation, inlined, to prove the refactor preserved output."""
    sec = securities_df[["ticker", "sleeve_category"]].copy()
    acct = (accounts_df[["pseudonym", "display_name", "managed_by", "tax_treatment"]]
            .dropna(subset=["pseudonym"]).copy())
    joined = positions_df.merge(sec, left_on="symbol", right_on="ticker", how="left")
    joined["sleeve_category"] = joined["sleeve_category"].fillna("unknown")
    totals = positions_df.groupby("pseudonym")["current_value"].sum().reset_index()
    sleeve_by_acct = joined.groupby(["pseudonym", "sleeve_category"])["current_value"].sum().reset_index()
    dom_idx = sleeve_by_acct.groupby("pseudonym")["current_value"].idxmax()
    dominant = sleeve_by_acct.loc[dom_idx, ["pseudonym", "sleeve_category"]].rename(
        columns={"sleeve_category": "dominant_sleeve"})
    result = (totals.merge(dominant, on="pseudonym", how="left").merge(acct, on="pseudonym", how="left")
              .drop(columns=["pseudonym"]).rename(columns={
                  "current_value": "Total AUM ($)", "display_name": "Account",
                  "managed_by": "Managed By", "tax_treatment": "Tax Treatment",
                  "dominant_sleeve": "Dominant Sleeve"})
              .sort_values("Total AUM ($)", ascending=False).reset_index(drop=True))
    result["Dominant Sleeve"] = result["Dominant Sleeve"].map(sleeve_display_name)
    return result[["Account", "Managed By", "Tax Treatment", "Dominant Sleeve", "Total AUM ($)"]]


def test_build_account_breakdown_unchanged_by_refactor_synthetic():
    pos, acct, sec = _fixture()
    pd.testing.assert_frame_equal(
        build_account_breakdown(pos, acct, sec),
        _old_build_account_breakdown(pos, acct, sec),
    )


def test_build_account_breakdown_unchanged_by_refactor_real_data():
    from src.household_data import find_latest_positions_csv
    csv = find_latest_positions_csv()
    if csv is None or not TRACKER_DB.exists() or TRACKER_DB.stat().st_size == 0:
        pytest.skip("personal-mode inputs absent")
    from src.ingestion.fidelity import parse_fidelity_csv
    pos = parse_fidelity_csv(csv)
    conn = sqlite3.connect(str(TRACKER_DB))
    acct = pd.read_sql_query("SELECT * FROM accounts", conn)
    sec = pd.read_sql_query("SELECT * FROM securities", conn)
    conn.close()
    pd.testing.assert_frame_equal(
        build_account_breakdown(pos, acct, sec),
        _old_build_account_breakdown(pos, acct, sec),
    )
