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
    SLEEVE_PRIORITY_BY_ACCOUNT_TYPE,
    ACCOUNT_SHELTER_PRIORITY,
    SLEEVE_ASSUMED_YIELD,
    MIN_ANNUAL_BENEFIT,
    sleeve_priority,
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
        {"ticker": "AVUV",  "name": "Avantis US Small Cap Value","tax_efficiency": "high",   "sleeve_category": "us_small_value"},
        {"ticker": "JEPI",  "name": "JPM Equity Premium",       "tax_efficiency": "medium", "sleeve_category": "hedged_equity"},
        {"ticker": "VOO",   "name": "Vanguard S&P 500 ETF",     "tax_efficiency": "high",   "sleeve_category": "us_large_core"},
        {"ticker": "SPAXX", "name": "Fidelity Money Market",    "tax_efficiency": "low",    "sleeve_category": "cash"},
    ])
    # total_gain_loss / cost_basis_total are NaN for the cash rows (as the parser returns them).
    positions_df = pd.DataFrame([
        {"pseudonym": "acct_tax",  "symbol": "VNQ",   "description": "REIT",  "current_value": 10000.0, "total_gain_loss": 2000.0, "cost_basis_total": 8000.0},
        {"pseudonym": "acct_roth", "symbol": "VNQ",   "description": "REIT",  "current_value":  5000.0, "total_gain_loss": 1000.0, "cost_basis_total": 4000.0},
        {"pseudonym": "acct_tax",  "symbol": "AVUV",  "description": "SCV",   "current_value":  6000.0, "total_gain_loss":  500.0, "cost_basis_total": 5500.0},
        {"pseudonym": "acct_tax",  "symbol": "JEPI",  "description": "HEQ",   "current_value":  4000.0, "total_gain_loss":  300.0, "cost_basis_total": 3700.0},
        {"pseudonym": "acct_tax",  "symbol": "VOO",   "description": "LC",    "current_value":  8000.0, "total_gain_loss": 1000.0, "cost_basis_total": 7000.0},
        {"pseudonym": "acct_tax",  "symbol": "SPAXX", "description": "CASH",  "current_value":   100.0, "total_gain_loss": float("nan"), "cost_basis_total": float("nan")},
        {"pseudonym": "acct_roth", "symbol": "SPAXX", "description": "CASH",  "current_value":   200.0, "total_gain_loss": float("nan"), "cost_basis_total": float("nan")},
    ])
    return positions_df, accounts_df, securities_df


def _register():
    pos, acct, sec = _fixture()
    return build_location_register(pos, acct, sec, TAX_PROFILE,
                                   SLEEVE_PRIORITY_BY_ACCOUNT_TYPE, ACCOUNT_SHELTER_PRIORITY)


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
    # VNQ in taxable: gain 2000 * combined LTCG rate (federal 15% + PA 3.07%).
    a = reg[(reg["symbol"] == "VNQ") & (reg["case"] == "A")].iloc[0]
    _ltcg = TAX_PROFILE["federal_ltcg"] + TAX_PROFILE["state_ltcg"]
    assert a["cost_to_realize"] == pytest.approx(2000.0 * _ltcg, abs=0.01)
    assert not bool(a["is_free"])
    assert a["payback_months"] is not None and a["payback_months"] > 0


# ── build_deploy_view: absent sleeve excluded, not sorted last ─────────────────

def test_deploy_view_excludes_absent_sleeve():
    pos, acct, sec = _fixture()
    dv = build_deploy_view(pos, acct, sec, "roth_ira", "acct_roth", 1000.0)
    roth_map = SLEEVE_PRIORITY_BY_ACCOUNT_TYPE["roth_ira"]
    # Sleeves absent from the roth map are not deploy targets -> never appear.
    for absent in ("cash", "hedged_equity", "core_fi_treasury", "intl_developed"):
        assert sleeve_display_name(absent) not in set(dv["sleeve"]), f"{absent} leaked into deploy view"
    # And nothing is coerced to a huge sentinel and sorted last.
    assert dv["priority"].notna().all(), "no deploy row may have a null priority"
    assert dv["priority"].max() <= max(roth_map.values()), (
        "an absent sleeve appears to have been sorted last via a large sentinel"
    )
    # Every sleeve shown maps back to a real roth-map key.
    known = {sleeve_display_name(s) for s in roth_map}
    assert set(dv["sleeve"]).issubset(known)


def test_absent_priority_is_none_not_lowest():
    # Absent from an account type's map -> None (not a deploy target), distinct
    # from ranked-last.
    assert sleeve_priority("roth_ira", "cash") is None                    # cash: never a target
    assert sleeve_priority("roth_ira", "hedged_equity") is None
    assert sleeve_priority("roth_ira", "definitely_not_a_sleeve") is None
    assert sleeve_priority("roth_ira", "us_small_value") == 1             # rank 1 = small VALUE (AVUV)
    assert sleeve_priority("roth_ira", "us_small_core") == 6              # broad small-core: a real (low) rank
    assert sleeve_priority("taxable", "us_large_core") == 1               # account-conditional


def test_deploy_view_sorted_by_priority_ascending():
    pos, acct, sec = _fixture()
    dv = build_deploy_view(pos, acct, sec, "roth_ira", "acct_roth", 1000.0)
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


# ── Case D is comparative: roth_rank < taxable_rank (no one-sided over-fire) ────

def _case_d_fixture():
    """Every ticker in taxable; a displaceable REIT in the Roth so _worst_roth_below
    is satisfied. Case D must fire on AVUV only (roth 1 < taxable 2)."""
    acct = pd.DataFrame([
        {"pseudonym": "t", "display_name": "Taxable", "tax_treatment": "taxable"},
        {"pseudonym": "r", "display_name": "Roth",    "tax_treatment": "roth_ira"},
    ])
    sec = pd.DataFrame([
        {"ticker": "MCO",  "name": "Moody's",      "tax_efficiency": "high", "sleeve_category": "single_stock"},
        {"ticker": "IXUS", "name": "iShares Intl", "tax_efficiency": "high", "sleeve_category": "intl_all_exus"},
        {"ticker": "VXUS", "name": "Vanguard Intl","tax_efficiency": "high", "sleeve_category": "intl_all_exus"},
        {"ticker": "VOO",  "name": "S&P 500",      "tax_efficiency": "high", "sleeve_category": "us_large_core"},
        {"ticker": "AVUV", "name": "Avantis SCV",  "tax_efficiency": "high", "sleeve_category": "us_small_value"},
        {"ticker": "ZZZ",  "name": "Nowhere",      "tax_efficiency": "high", "sleeve_category": "not_a_mapped_sleeve"},
        {"ticker": "USRT", "name": "REIT",         "tax_efficiency": "low",  "sleeve_category": "real_assets_reit"},
    ])
    pos = pd.DataFrame(
        [{"pseudonym": "t", "symbol": s, "current_value": 10000.0, "total_gain_loss": 100.0, "cost_basis_total": 9900.0}
         for s in ("MCO", "IXUS", "VXUS", "VOO", "AVUV", "ZZZ")]
        + [{"pseudonym": "r", "symbol": "USRT", "current_value": 5000.0, "total_gain_loss": 100.0, "cost_basis_total": 4900.0}]
    )
    return pos, acct, sec


def _case_d_symbols():
    pos, acct, sec = _case_d_fixture()
    reg = build_location_register(pos, acct, sec, TAX_PROFILE,
                                  SLEEVE_PRIORITY_BY_ACCOUNT_TYPE, ACCOUNT_SHELTER_PRIORITY)
    return set(reg[reg["case"] == "D"]["symbol"])


def test_case_d_mco_no_row():
    # single_stock is absent from the roth map (INF) -> never stranded.
    assert "MCO" not in _case_d_symbols()


def test_case_d_international_no_row():
    # intl is taxable-rank 1 and absent from the roth map (the FTC is only
    # claimable in taxable) -> never case D.
    d = _case_d_symbols()
    assert "IXUS" not in d and "VXUS" not in d


def test_case_d_us_large_core_no_row():
    # roth 5 > taxable 1 -> taxable is the better home, no strand.
    assert "VOO" not in _case_d_symbols()


def test_case_d_avuv_fires():
    # roth 1 < taxable 2 -> a shelter wants it more than taxable -> case D.
    assert "AVUV" in _case_d_symbols()


def test_case_d_sleeve_absent_from_both_maps_no_row():
    assert "ZZZ" not in _case_d_symbols()


# ── Federally-exempt sleeves generate no A/B action (municipal) ─────────────────
# high_yield_muni income is federally exempt: relocating it into a pre-tax shelter
# converts exempt interest into ordinary income at withdrawal — strictly worse — so
# it generates NO case A/B action, categorically (not because its drag is small). It
# keeps its state-only drag magnitude; it just never fires a relocation row. The
# comparison holding (NONEX / multi_sector_fi) has the SAME assumed yield AND value
# in the SAME taxable account, so the only thing that differs is the federal
# exemption. Keyed on the SLEEVE, never a symbol.

_MUNI_VALUE = 50000.0


def _muni_fixture():
    acct = pd.DataFrame([
        {"pseudonym": "acct_tax", "display_name": "Taxable Acct", "tax_treatment": "taxable"},
    ])
    sec = pd.DataFrame([
        {"ticker": "GHYIX", "name": "HY Muni",  "tax_efficiency": "medium", "sleeve_category": "high_yield_muni"},
        {"ticker": "NONEX", "name": "Multi FI", "tax_efficiency": "medium", "sleeve_category": "multi_sector_fi"},
    ])
    pos = pd.DataFrame([
        {"pseudonym": "acct_tax", "symbol": "GHYIX", "current_value": _MUNI_VALUE, "total_gain_loss": 2000.0, "cost_basis_total": 48000.0},
        {"pseudonym": "acct_tax", "symbol": "NONEX", "current_value": _MUNI_VALUE, "total_gain_loss": 2000.0, "cost_basis_total": 48000.0},
    ])
    return pos, acct, sec


def _muni_register(pos, acct, sec):
    return build_location_register(pos, acct, sec, TAX_PROFILE,
                                   SLEEVE_PRIORITY_BY_ACCOUNT_TYPE, ACCOUNT_SHELTER_PRIORITY)


def test_exempt_sleeve_no_ab_row_but_nonexempt_same_yield_and_value_does():
    """Pin BOTH sides at once: identical yield, value, and account — the muni
    generates no case A/B row; the non-exempt sleeve does."""
    assert SLEEVE_ASSUMED_YIELD["multi_sector_fi"] == pytest.approx(SLEEVE_ASSUMED_YIELD["high_yield_muni"])
    reg = _muni_register(*_muni_fixture())
    assert reg[reg["symbol"] == "GHYIX"].empty, "a federally-exempt sleeve must generate no case A/B row"
    nonexempt = reg[reg["symbol"] == "NONEX"]
    assert len(nonexempt) == 1 and nonexempt.iloc[0]["case"] in ("A", "B"), (
        "a non-exempt sleeve at the same yield/value must still generate a row"
    )


def test_adding_sleeve_to_exempt_set_suppresses_its_rows(monkeypatch):
    """Declaring multi_sector_fi federally exempt (adding it to the set) must drop its
    row with NO other edit — proving the exclusion consults the set, not a symbol."""
    import src.location_config as lc
    pos, acct, sec = _muni_fixture()

    before = _muni_register(pos, acct, sec)
    assert not before[before["symbol"] == "NONEX"].empty, "baseline: NONEX registers a row"

    monkeypatch.setattr(lc, "FEDERALLY_EXEMPT_SLEEVES",
                        frozenset({"high_yield_muni", "multi_sector_fi"}))
    after = _muni_register(pos, acct, sec)
    assert after[after["symbol"] == "NONEX"].empty, (
        "adding multi_sector_fi to FEDERALLY_EXEMPT_SLEEVES must suppress its rows"
    )


def test_yield_table_does_not_encode_tax_status():
    # The muni's yield is its real income throw-off, not a zero smuggling in
    # "tax-exempt". Exemption lives in FEDERALLY_EXEMPT_SLEEVES, not this table.
    assert SLEEVE_ASSUMED_YIELD["high_yield_muni"] > 0


# ── Group 7 (frozen_tod_income): population 4, register rows 3, coverage intact ──
# GHYIX is counted in the population ({count}/{value}, matched_symbols) but is not a
# register row; the other three (BILPX/GAOSX/JHEQX) are. assert_full_coverage must
# still see zero orphans — GHYIX's vanished row must not orphan anything.

def _group7_fixture():
    """Four of the five frozen_tod_income symbols in the TOD account, sleeves and
    efficiencies mirroring data/seed/securities_household.csv. JCPB (the Aug-2026
    advisor addition) is deliberately absent: this fixture pins GHYIX's
    vanished-row behavior, which is independent of the book's fifth holding."""
    acct = pd.DataFrame([
        {"pseudonym": "tod", "display_name": "Individual Taxable (TOD)", "tax_treatment": "taxable"},
    ])
    sec = pd.DataFrame([
        {"ticker": "BILPX", "name": "Liquid Fed",    "tax_efficiency": "low",    "sleeve_category": "liquid_alt"},
        {"ticker": "GAOSX", "name": "Global Alloc",  "tax_efficiency": "medium", "sleeve_category": "multi_asset"},
        {"ticker": "GHYIX", "name": "HY Muni",       "tax_efficiency": "medium", "sleeve_category": "high_yield_muni"},
        {"ticker": "JHEQX", "name": "Hedged Equity", "tax_efficiency": "medium", "sleeve_category": "hedged_equity"},
    ])
    pos = pd.DataFrame([
        {"pseudonym": "tod", "symbol": s, "current_value": 20000.0, "total_gain_loss": 500.0, "cost_basis_total": 19500.0}
        for s in ("BILPX", "GAOSX", "GHYIX", "JHEQX")
    ])
    return pos, acct, sec


def test_group7_population_four_rows_three_caption_and_coverage():
    from src.location_actions import (
        ACTION_GROUPS, filter_register_for_group, resolve_placeholders,
        resolve_caption, assert_full_coverage, _fmt_dollars,
    )
    pos, acct, sec = _group7_fixture()
    reg = _muni_register(pos, acct, sec)
    g7 = next(g for g in ACTION_GROUPS if g["key"] == "frozen_tod_income")

    reg7 = filter_register_for_group(reg, g7)
    assert set(reg7["symbol"]) == {"BILPX", "GAOSX", "JHEQX"}, "GHYIX must not be a register row"
    assert len(reg7) == 3

    resolved = resolve_placeholders(g7, pos, acct, sec, reg)
    assert resolved["count"] == "4", "population (matched_symbols) must count all four holdings"

    cap = resolve_caption(g7, pos, acct, reg)
    # Caption states the shown-rows value (matches the table's Value total), not only
    # the population value: "4 holdings worth $80,000. 3 appear below (worth $60,000)".
    shown = _fmt_dollars(float(reg7["current_value"].sum())).lstrip("$")
    assert cap and "3 appear below" in cap and shown in cap and "generates no action" in cap, cap

    # A vanished row (GHYIX) must not orphan anything.
    assert_full_coverage(reg)   # raises on any orphan


def test_caption_verb_agrees_with_singular_row_count():
    """Fix 4: the '{row_count} appear below' clause must agree in number — one row
    'appears', many 'appear'. The rule lives in resolve_caption (not per template),
    so a lone register row renders 'Only 1 appears below', never 'Only 1 appear'."""
    from src.location_actions import (
        ACTION_GROUPS, filter_register_for_group, resolve_caption,
    )
    acct = pd.DataFrame([
        {"pseudonym": "tod", "display_name": "Individual Taxable (TOD)", "tax_treatment": "taxable"},
    ])
    sec = pd.DataFrame([
        {"ticker": "GHYIX", "name": "HY Muni",       "tax_efficiency": "medium", "sleeve_category": "high_yield_muni"},
        {"ticker": "JHEQX", "name": "Hedged Equity", "tax_efficiency": "medium", "sleeve_category": "hedged_equity"},
    ])
    pos = pd.DataFrame([
        {"pseudonym": "tod", "symbol": s, "current_value": 20000.0, "total_gain_loss": 500.0, "cost_basis_total": 19500.0}
        for s in ("GHYIX", "JHEQX")
    ])
    reg = _muni_register(pos, acct, sec)
    g7 = next(g for g in ACTION_GROUPS if g["key"] == "frozen_tod_income")
    assert len(filter_register_for_group(reg, g7)) == 1, "fixture must yield exactly one register row"
    cap = resolve_caption(g7, pos, acct, reg)
    assert "1 appears below" in cap, f"singular verb expected: {cap!r}"
    assert "1 appear below" not in cap


# ── Fix 2: the register carries each position's market value (Value ($) column) ──
# The Underlying-positions expander gains a "Value ($)" column so a small annual
# drag can be read against the position size that produces it. The value is the
# register row's current_value — the same positions-CSV field {value} already sums,
# so there is exactly one source of truth.

def test_register_carries_position_market_value():
    reg = _register()
    assert "current_value" in reg.columns, "register must carry current_value for the Value column"
    pos, acct, _ = _fixture()
    disp = pos.merge(acct[["pseudonym", "display_name"]], on="pseudonym", how="left")
    assert not reg.empty
    for _, r in reg.iterrows():
        src = disp[(disp["symbol"] == r["symbol"]) & (disp["display_name"] == r["account"])]
        assert len(src) == 1, f"no unique source position for {r['symbol']}/{r['account']}"
        assert r["current_value"] == pytest.approx(float(src.iloc[0]["current_value"])), (
            "register current_value must equal the source position's market value"
        )


# ── Fix 1: a register_rows group shows ALL backing rows, sub-threshold included ──
# Regression for the SAA-sleeves "count 4, table empty, no caption" bug. A
# register_rows group whose rows are ALL below MIN_ANNUAL_BENEFIT still has
# count == row_count; the page renders every reg_row, so the tiny-drag rows appear
# with their Value rather than as an empty table under a non-zero header. There is
# no filter-based gap to caption — count == row_count.
#
# The register used to carry a `surfaced` boolean (annual_benefit >= threshold) that
# this test asserted against. Once the page stopped filtering on it, nothing read it,
# and it was removed. The invariant below is now stated in terms of the threshold
# itself rather than the dropped column — which is the stronger form anyway: it pins
# the BEHAVIOUR (sub-threshold rows are still returned and still counted) instead of
# the presence of a particular column.

def test_register_rows_group_shows_all_backing_rows_including_subthreshold():
    from src.location_actions import _pop_holdings, filter_register_for_group
    acct = pd.DataFrame([
        {"pseudonym": "t", "display_name": "SynthTax", "tax_treatment": "taxable"},
    ])
    sec = pd.DataFrame([
        {"ticker": "TSYX", "name": "Treasury sleeve", "tax_efficiency": "low",
         "sleeve_category": "core_fi_treasury"},
    ])
    pos = pd.DataFrame([
        {"pseudonym": "t", "symbol": "TSYX", "current_value": 10.0,   # $10 -> drag << $1
         "total_gain_loss": 0.0, "cost_basis_total": 10.0},
    ])
    reg = build_location_register(pos, acct, sec, TAX_PROFILE,
                                  SLEEVE_PRIORITY_BY_ACCOUNT_TYPE, ACCOUNT_SHELTER_PRIORITY)
    group = {"key": "synthetic_saa", "symbols": ["TSYX"], "case_filter": ["A"],
             "accounts": ["SynthTax"], "population": "register_rows"}
    reg_rows = filter_register_for_group(reg, group)
    assert len(reg_rows) == 1 and reg_rows.iloc[0]["case"] == "A"
    assert (reg_rows["annual_benefit"] < MIN_ANNUAL_BENEFIT).all(), (
        "fixture must be sub-threshold, or this proves nothing"
    )
    # The OLD expander filtered to rows at/above the threshold -> 0 rows under a
    # count of 1 (the bug). The register must still RETURN that row.
    assert len(reg_rows[reg_rows["annual_benefit"] >= MIN_ANNUAL_BENEFIT]) == 0
    # The presentation threshold must not have leaked back into the model.
    assert "surfaced" not in reg_rows.columns, (
        "the register is canonical — a presentation flag must not live on it"
    )
    # count (population) == row_count, and the page now shows every reg_row.
    assert len(_pop_holdings(group, pos, acct, reg)) == len(reg_rows) == 1
