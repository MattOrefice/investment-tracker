"""Tests for the liquidity hierarchy (src/liquidity.py) + the 'If you need cash' page.

Synthetic tests (tiering, cost model, sort/cumulative, inverse-of-shelter) run
everywhere; the render test skips without personal-mode inputs.
"""
import pathlib

import pandas as pd
import pytest

from src.liquidity import build_liquidity_ladder, EARLY_WITHDRAWAL_PENALTY
from src.location_config import TAX_PROFILE, ordinary_rate, ltcg_rate

ROOT = pathlib.Path(__file__).resolve().parents[1]
TRACKER_DB = ROOT / "data" / "tracker.db"


def _fixture():
    """One holding per tier/branch: taxable loss, taxable small gain, taxable big
    gain, taxable cash, Roth (with basis), and a pre-tax IRA holding."""
    accounts = pd.DataFrame([
        {"pseudonym": "tax",  "tax_treatment": "taxable",         "display_name": "Taxable"},
        {"pseudonym": "roth", "tax_treatment": "roth_ira",        "display_name": "Roth"},
        {"pseudonym": "trad", "tax_treatment": "traditional_ira", "display_name": "Trad IRA"},
    ])
    positions = pd.DataFrame([
        {"pseudonym": "tax",  "symbol": "LOSS",  "current_value": 1000.0, "total_gain_loss": -200.0, "cost_basis_total": 1200.0},
        {"pseudonym": "tax",  "symbol": "SMALL", "current_value": 1050.0, "total_gain_loss":   50.0, "cost_basis_total": 1000.0},  # +5%
        {"pseudonym": "tax",  "symbol": "BIG",   "current_value": 1500.0, "total_gain_loss":  500.0, "cost_basis_total": 1000.0},  # +50%
        {"pseudonym": "tax",  "symbol": "SPAXX", "current_value":  300.0, "total_gain_loss": float("nan"), "cost_basis_total": float("nan")},
        {"pseudonym": "roth", "symbol": "RGROW", "current_value": 2000.0, "total_gain_loss":  800.0, "cost_basis_total": 1200.0},
        {"pseudonym": "trad", "symbol": "TDX",   "current_value": 1000.0, "total_gain_loss":    0.0, "cost_basis_total": 1000.0},
    ])
    return positions, accounts


def test_liquidity_tiers_and_costs():
    pos, acct = _fixture()
    lad = build_liquidity_ladder(pos, acct, TAX_PROFILE).set_index("symbol")
    ltcg = ltcg_rate(TAX_PROFILE)
    penord = EARLY_WITHDRAWAL_PENALTY + ordinary_rate(TAX_PROFILE)

    # Tiers: taxable loss/small/cash -> 1, taxable meaningful gain -> 2, sheltered -> 3
    assert lad.loc["LOSS", "tier"] == 1 and lad.loc["SMALL", "tier"] == 1 and lad.loc["SPAXX", "tier"] == 1
    assert lad.loc["BIG", "tier"] == 2
    assert lad.loc["RGROW", "tier"] == 3 and lad.loc["TDX", "tier"] == 3

    # Costs: a loss and cash owe nothing; a taxable gain owes LTCG on the gain only;
    # pre-tax owes penalty+ordinary on the WHOLE value; Roth owes it on the GROWTH only.
    assert lad.loc["LOSS", "cost_to_cash"] == 0.0
    assert lad.loc["SPAXX", "cost_to_cash"] == 0.0
    assert lad.loc["BIG", "cost_to_cash"] == pytest.approx(500.0 * ltcg, abs=0.01)
    assert lad.loc["TDX", "cost_to_cash"] == pytest.approx(1000.0 * penord, abs=0.01)
    # Roth is CONSERVATIVE — the WHOLE value penalized+taxed (not growth-only), since the
    # tool can't see contribution basis or age; it must never show $0.
    assert lad.loc["RGROW", "cost_to_cash"] == pytest.approx(2000.0 * penord, abs=0.01)
    # every Tier-3 row (Roth + pre-tax) uses value × (penalty + ordinary), and > $0
    t3 = lad[lad["tier"] == 3]
    assert (t3["cost_to_cash"] > 0).all(), "no Tier-3 row may show $0 cost"
    for sym, r in t3.iterrows():
        assert r["cost_to_cash"] == pytest.approx(r["value"] * penord, abs=0.01), (
            f"{sym}: Tier-3 cost must be whole-withdrawal value × {penord:.4f}"
        )


def test_liquidity_sorted_cheapest_first_and_cumulative():
    pos, acct = _fixture()
    lad = build_liquidity_ladder(pos, acct, TAX_PROFILE)
    assert list(lad["tier"]) == sorted(lad["tier"]), "must be tier-ascending (cheapest tier first)"
    # net_cash == value − cost; cumulative == running sum of net_cash
    for _, r in lad.iterrows():
        assert r["net_cash"] == pytest.approx(round(r["value"] - r["cost_to_cash"], 2))
    assert lad["cumulative_net_cash"].tolist() == pytest.approx(lad["net_cash"].cumsum().round(2).tolist())
    assert (lad["cumulative_net_cash"].diff().dropna() >= -0.01).all(), "cumulative must be non-decreasing"


def test_liquidity_is_the_inverse_of_shelter_priority():
    """The core insight: sheltered money (Roth / IRA) is the LEAST liquid (Tier 3),
    even though it's the most valuable to shelter — liquidity inverts asset location."""
    pos, acct = _fixture()
    lad = build_liquidity_ladder(pos, acct, TAX_PROFILE).set_index("symbol")
    assert lad.loc["RGROW", "tier"] == 3 and lad.loc["TDX", "tier"] == 3
    assert lad.loc["LOSS", "tier"] == 1
    # a taxable loss converts to cash more cheaply than a sheltered dollar
    assert lad.loc["LOSS", "cost_to_cash"] < lad.loc["TDX", "cost_to_cash"]


def test_tier_capital_reconciles_to_ladder():
    """Part 2: the tier cards are pure aggregation of the ladder's own columns —
    Σ per-tier gross == total Value, Σ per-tier net == total Net cash, and gross ≥ net
    for every tier (cost is non-negative). No new tax computation is introduced."""
    pos, acct = _fixture()
    lad = build_liquidity_ladder(pos, acct, TAX_PROFILE)
    by = lad.groupby("tier").agg(gross=("value", "sum"), net=("net_cash", "sum"))
    assert abs(by["gross"].sum() - lad["value"].sum()) < 0.01, "tier grosses must sum to total value"
    assert abs(by["net"].sum() - lad["net_cash"].sum()) < 0.01, "tier nets must sum to total net cash"
    assert (by["gross"] >= by["net"] - 0.01).all(), "gross must be >= net for every tier"


def test_marginal_cost_exhibit_derivation():
    """Part 3: marginal cost per tier = tier cost ÷ tier gross (cheapest-first) and it
    steps UP across tiers (T1 ≤ T2 ≤ T3); Tier 3 == the whole-withdrawal rate (~35%).
    Boundaries are the cumulative gross at the end of Tier 1 and Tier 2. All derived
    from the ladder's existing columns — no tax recomputation."""
    pos, acct = _fixture()
    lad = build_liquidity_ladder(pos, acct, TAX_PROFILE)
    mc = lad.groupby("tier").agg(gross=("value", "sum"), cost=("cost_to_cash", "sum")).sort_index()
    mc["pct"] = mc["cost"] / mc["gross"] * 100
    assert mc.loc[1, "pct"] <= mc.loc[2, "pct"] <= mc.loc[3, "pct"], "marginal cost must step up T1→T2→T3"
    penord = EARLY_WITHDRAWAL_PENALTY + ordinary_rate(TAX_PROFILE)
    assert mc.loc[3, "pct"] == pytest.approx(penord * 100, abs=0.1), "Tier 3 marginal == whole-withdrawal rate"
    cum = mc["gross"].cumsum()
    assert cum.loc[1] == pytest.approx(mc.loc[1, "gross"]), "boundary 1 = Tier-1 gross"
    assert cum.loc[2] == pytest.approx(mc.loc[1, "gross"] + mc.loc[2, "gross"]), "boundary 2 = Tier1+2 gross"


def test_liquidity_page_renders_live(monkeypatch):
    """End-to-end render on real personal data: page loads, the ladder has the
    cumulative column, tiers are the three known labels, and cumulative is monotone."""
    from src.household_data import find_latest_positions_csv
    csv = find_latest_positions_csv()
    if csv is None or not TRACKER_DB.exists() or TRACKER_DB.stat().st_size == 0:
        pytest.skip("personal-mode inputs absent")
    import src.config
    import src.db
    monkeypatch.setattr(src.config, "IS_DEMO", False)
    monkeypatch.setattr(src.db, "DB_PATH", TRACKER_DB)
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(ROOT / "pages" / "15_Liquidity.py"), default_timeout=90).run()
    assert not at.exception, f"page raised: {at.exception}"
    # Part 2 — tier-capital cards (replaced the prose line); the three tiers reconcile
    # to Total available (gross), and the caveat + settlement caption render.
    import re
    mets = {m.label: m.value for m in at.metric}

    def _num(s):
        return float(re.sub(r"[^\d.]", "", s))

    for lbl in ("Tier 1 · taxable, minimal gains", "Tier 2 · taxable with gains",
                "Tier 3 · locked (retirement)", "Total available (gross)",
                "Total net if fully liquidated"):
        assert lbl in mets, f"tier card '{lbl}' missing"
    _sum3 = sum(_num(mets[l]) for l in ("Tier 1 · taxable, minimal gains",
                                        "Tier 2 · taxable with gains", "Tier 3 · locked (retirement)"))
    assert abs(_sum3 - _num(mets["Total available (gross)"])) < 1.5, "tier cards must sum to total gross"
    assert any("T+1" in c.value and "same-day" in c.value for c in at.caption), "settlement caption missing"
    assert any("no accessible" in i.value for i in at.info), "Roth/age caveat missing"
    # Part 3 — marginal cost exhibit: subheader + readout naming both boundaries
    assert any("Marginal cost of the next dollar" in s.value for s in at.subheader), "exhibit missing"
    assert any("before touching a taxable gain" in c.value and "before touching retirement" in c.value
               for c in at.caption), "exhibit readout missing the two boundaries"
    # ladder
    tables = [d.value for d in at.dataframe if "Cumulative net cash ($)" in list(d.value.columns)]
    assert tables, "liquidity ladder not rendered"
    t = tables[0]
    assert (t["Cumulative net cash ($)"].diff().dropna() >= -0.01).all(), "cumulative must be non-decreasing"
    assert set(t["Tier"]) <= {
        "Tier 1 · free / cheap", "Tier 2 · moderate (15% LTCG)", "Tier 3 · locked (penalty + tax)"}
    # Fix 1 — no locked (Tier-3) row shows $0 cost
    locked = t[t["Tier"].str.contains("locked")]
    assert not locked.empty and (locked["Cost to cash ($)"] > 0).all(), "no locked row may show $0 cost"
    # Feature A — entering a target names the top-N holdings to sell
    at.number_input[0].set_value(40000).run()
    assert not at.exception
    assert any("To raise" in s.value and "sell the top" in s.value for s in at.success), (
        "raise-cash summary missing"
    )
