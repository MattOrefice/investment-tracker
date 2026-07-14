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


_ROTH_FREE = "Roth IRA — contribution basis (free)"
_ROTH_EARN_LOCKED = "Roth IRA — earnings (locked until 59½)"
_ROTH_EARN_QUAL = "Roth IRA — earnings (qualified, tax-free)"


def _fixture():
    """Taxable loss / small gain / big gain / cash, a Roth (securities + cash, total
    $2,500), and a pre-tax IRA holding. The Roth is represented as basis/earnings
    tranches (not per-holding), so RGROW/RCASH never appear as ladder rows."""
    accounts = pd.DataFrame([
        {"pseudonym": "tax",  "tax_treatment": "taxable",         "display_name": "Taxable"},
        {"pseudonym": "roth", "tax_treatment": "roth_ira",        "display_name": "Roth IRA"},
        {"pseudonym": "trad", "tax_treatment": "traditional_ira", "display_name": "Trad IRA"},
    ])
    positions = pd.DataFrame([
        {"pseudonym": "tax",  "symbol": "LOSS",  "current_value": 1000.0, "total_gain_loss": -200.0, "cost_basis_total": 1200.0},
        {"pseudonym": "tax",  "symbol": "SMALL", "current_value": 1050.0, "total_gain_loss":   50.0, "cost_basis_total": 1000.0},  # +5%
        {"pseudonym": "tax",  "symbol": "BIG",   "current_value": 1500.0, "total_gain_loss":  500.0, "cost_basis_total": 1000.0},  # +50%
        {"pseudonym": "tax",  "symbol": "SPAXX", "current_value":  300.0, "total_gain_loss": float("nan"), "cost_basis_total": float("nan")},
        {"pseudonym": "roth", "symbol": "RGROW", "current_value": 2000.0, "total_gain_loss":  800.0, "cost_basis_total": 1200.0},
        {"pseudonym": "roth", "symbol": "RCASH", "current_value":  500.0, "total_gain_loss": float("nan"), "cost_basis_total": float("nan")},
        {"pseudonym": "trad", "symbol": "TDX",   "current_value": 1000.0, "total_gain_loss":    0.0, "cost_basis_total": 1000.0},
    ])
    return positions, accounts


def test_liquidity_tiers_and_costs():
    pos, acct = _fixture()
    # No basis entered (default) → the whole Roth ($2,500) is one locked earnings row.
    lad = build_liquidity_ladder(pos, acct, TAX_PROFILE).set_index("symbol")
    ltcg = ltcg_rate(TAX_PROFILE)
    penord = EARLY_WITHDRAWAL_PENALTY + ordinary_rate(TAX_PROFILE)

    # Tiers: taxable loss/small/cash -> 1, taxable meaningful gain -> 2, sheltered -> 3
    assert lad.loc["LOSS", "tier"] == 1 and lad.loc["SMALL", "tier"] == 1 and lad.loc["SPAXX", "tier"] == 1
    assert lad.loc["BIG", "tier"] == 2
    assert lad.loc[_ROTH_EARN_LOCKED, "tier"] == 3 and lad.loc["TDX", "tier"] == 3
    # Roth per-holding lots never appear — the Roth is fungible tranches, not tickers.
    assert "RGROW" not in lad.index and "RCASH" not in lad.index

    # Costs: a loss and cash owe nothing; a taxable gain owes LTCG on the gain only;
    # pre-tax owes penalty+ordinary on the WHOLE value; with no basis, the whole Roth
    # is the locked earnings tranche at the same penalty+ordinary rate.
    assert lad.loc["LOSS", "cost_to_cash"] == 0.0
    assert lad.loc["SPAXX", "cost_to_cash"] == 0.0
    assert lad.loc["BIG", "cost_to_cash"] == pytest.approx(500.0 * ltcg, abs=0.01)
    assert lad.loc["TDX", "cost_to_cash"] == pytest.approx(1000.0 * penord, abs=0.01)
    assert lad.loc[_ROTH_EARN_LOCKED, "value"] == pytest.approx(2500.0, abs=0.01)
    assert lad.loc[_ROTH_EARN_LOCKED, "cost_to_cash"] == pytest.approx(2500.0 * penord, abs=0.01)
    # every Tier-3 row (locked Roth earnings + pre-tax) uses value × (penalty + ordinary), > $0
    t3 = lad[lad["tier"] == 3]
    assert (t3["cost_to_cash"] > 0).all(), "no Tier-3 row may show $0 cost"
    for sym, r in t3.iterrows():
        assert r["cost_to_cash"] == pytest.approx(r["value"] * penord, abs=0.01), (
            f"{sym}: Tier-3 cost must be whole-withdrawal value × {penord:.4f}"
        )


def test_liquidity_roth_basis_tranches():
    """Basis-aware Roth ordering (contributions first, then earnings) — NOT pro-rata.
    With a $1,500 basis on a $2,500 Roth and the owner under 59½: $1,500 becomes a
    Tier-1 free tranche (net = gross), $1,500..$2,500 the Tier-3 earnings tranche at
    the whole-withdrawal rate; the two sum to the Roth total and no per-holding row
    survives. At 59½+, the earnings tranche also becomes free (Tier 1)."""
    pos, acct = _fixture()
    penord = EARLY_WITHDRAWAL_PENALTY + ordinary_rate(TAX_PROFILE)

    lad = build_liquidity_ladder(pos, acct, TAX_PROFILE,
                                 roth_contribution_basis=1500.0, roth_is_qualified_age=False).set_index("symbol")
    assert _ROTH_FREE in lad.index and _ROTH_EARN_LOCKED in lad.index
    assert "RGROW" not in lad.index and "RCASH" not in lad.index, "no per-holding Roth split (not pro-rata)"
    free, earn = lad.loc[_ROTH_FREE], lad.loc[_ROTH_EARN_LOCKED]
    assert free["tier"] == 1 and free["cost_to_cash"] == 0.0 and free["net_cash"] == free["value"] == 1500.0
    assert earn["tier"] == 3 and earn["value"] == pytest.approx(1000.0, abs=0.01)
    assert earn["cost_to_cash"] == pytest.approx(1000.0 * penord, abs=0.01)
    assert free["value"] + earn["value"] == pytest.approx(2500.0, abs=0.01), "tranches sum to the Roth total"

    # 59½+ → the earnings tranche is free too (whole Roth Tier 1).
    lad_q = build_liquidity_ladder(pos, acct, TAX_PROFILE,
                                   roth_contribution_basis=1500.0, roth_is_qualified_age=True).set_index("symbol")
    assert lad_q.loc[_ROTH_EARN_QUAL, "tier"] == 1 and lad_q.loc[_ROTH_EARN_QUAL, "cost_to_cash"] == 0.0

    # A basis covering the whole Roth → one free tranche, no earnings row (underwater-safe cap).
    lad_full = build_liquidity_ladder(pos, acct, TAX_PROFILE, roth_contribution_basis=99_999.0).set_index("symbol")
    assert _ROTH_EARN_LOCKED not in lad_full.index and _ROTH_EARN_QUAL not in lad_full.index
    assert lad_full.loc[_ROTH_FREE, "value"] == pytest.approx(2500.0, abs=0.01) and lad_full.loc[_ROTH_FREE, "tier"] == 1


def test_liquidity_sorted_cheapest_first_and_cumulative():
    pos, acct = _fixture()
    lad = build_liquidity_ladder(pos, acct, TAX_PROFILE)
    assert list(lad["tier"]) == sorted(lad["tier"]), "must be tier-ascending (cheapest tier first)"
    # net_cash == value − cost; cumulative == running sum of net_cash
    for _, r in lad.iterrows():
        assert r["net_cash"] == pytest.approx(round(r["value"] - r["cost_to_cash"], 2))
    assert lad["cumulative_net_cash"].tolist() == pytest.approx(lad["net_cash"].cumsum().round(2).tolist())
    assert (lad["cumulative_net_cash"].diff().dropna() >= -0.01).all(), "cumulative must be non-decreasing"


def test_liquidity_taxable_ranks_before_roth_basis_at_equal_cost():
    """Within a cost_to_cash tie in Tier 1, taxable holdings must sort AHEAD of the
    Roth contribution-basis tranche — not behind it just because the Roth tranche
    is the larger dollar amount. Tapping Roth basis has a hidden cost (permanently
    forfeited tax-free space) a same-cost taxable sale does not, so taxable Tier-1
    capacity should exhaust first. Tier assignment and tier totals are unchanged —
    only the row order (and therefore the cumulative walk) shifts."""
    pos, acct = _fixture()
    # $1,500 basis < LOSS's $1,000 value? No — size the basis LARGER than every
    # individual taxable Tier-1 row (LOSS=1000, SPAXX=300) so a value-only
    # tie-break would have wrongly floated it to the very top.
    lad = build_liquidity_ladder(pos, acct, TAX_PROFILE,
                                 roth_contribution_basis=1500.0, roth_is_qualified_age=False)
    t1 = lad[lad["tier"] == 1].reset_index(drop=True)
    # All of these are $0-cost Tier-1 rows.
    assert (t1[t1["symbol"].isin(["LOSS", "SPAXX"])]["cost_to_cash"] == 0.0).all()
    assert (t1[t1["symbol"] == _ROTH_FREE]["cost_to_cash"] == 0.0).all()
    roth_pos = t1.index[t1["symbol"] == _ROTH_FREE][0]
    taxable_pos = t1.index[t1["symbol"].isin(["LOSS", "SPAXX"])]
    assert roth_pos > taxable_pos.max(), (
        "Roth contribution-basis tranche must sort AFTER every taxable Tier-1 row, "
        f"got roth at position {roth_pos}, taxable at {list(taxable_pos)}"
    )
    # Tier totals must be unchanged by the reorder (same rows, same sum: LOSS,
    # SMALL, SPAXX, and the Roth basis tranche are all Tier 1 in this fixture).
    assert lad[lad["tier"] == 1]["value"].sum() == pytest.approx(1000.0 + 1050.0 + 300.0 + 1500.0, abs=0.01)


def test_liquidity_is_the_inverse_of_shelter_priority():
    """The core insight: sheltered money (Roth / IRA) is the LEAST liquid (Tier 3),
    even though it's the most valuable to shelter — liquidity inverts asset location."""
    pos, acct = _fixture()
    lad = build_liquidity_ladder(pos, acct, TAX_PROFILE).set_index("symbol")
    assert lad.loc[_ROTH_EARN_LOCKED, "tier"] == 3 and lad.loc["TDX", "tier"] == 3
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
    import src.personal_profile
    monkeypatch.setattr(src.config, "IS_DEMO", False)
    monkeypatch.setattr(src.db, "DB_PATH", TRACKER_DB)
    # Inject a SYNTHETIC Roth profile so the basis tranche renders without needing (or
    # committing) any real DOB/basis. $20k basis on the real Roth total, under 59½.
    monkeypatch.setattr(src.personal_profile, "load_roth_profile", lambda is_demo, as_of: {
        "roth_contribution_basis": 20_000.0, "date_of_birth": None,
        "is_qualified_age": False, "source": "test-synthetic"})
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
                "Tier 3 · locked (retirement)", "Accessible now — no penalty (Tier 1 + Tier 2)",
                "Total portfolio (gross)", "Total net if fully liquidated"):
        assert lbl in mets, f"tier card '{lbl}' missing"
    _t1, _t2, _t3 = (_num(mets["Tier 1 · taxable, minimal gains"]),
                     _num(mets["Tier 2 · taxable with gains"]), _num(mets["Tier 3 · locked (retirement)"]))
    assert abs((_t1 + _t2 + _t3) - _num(mets["Total portfolio (gross)"])) < 1.5, "tier cards must sum to portfolio gross"
    # "Accessible now" is exactly Tier 1 + Tier 2 (excludes locked Tier 3) — same source.
    assert abs(_num(mets["Accessible now — no penalty (Tier 1 + Tier 2)"]) - (_t1 + _t2)) < 1.5, (
        "accessible-now gross must equal Tier 1 + Tier 2")
    assert _num(mets["Accessible now — no penalty (Tier 1 + Tier 2)"]) < _num(mets["Total portfolio (gross)"]), (
        "accessible-now must exclude locked Tier 3")
    assert any("T+1" in c.value and "same-day" in c.value for c in at.caption), "settlement caption missing"
    # Basis-aware caveat (replaced "can't see your basis or age"): names the templated
    # basis, the Tier-1 treatment, the verification forms, and the 5-year-clock clause.
    assert any("contribution basis of" in i.value and "Tier-1 free" in i.value
               and "Form 8606" in i.value and "5-year clocks" in i.value for i in at.info), "basis caveat missing"
    assert not any("can't see your basis or age" in i.value for i in at.info), "stale caveat still present"
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
    # Roth is two synthetic tranche rows: basis (free) in Tier 1, earnings (locked) in Tier 3.
    _syms = set(t["Symbol"])
    assert "Roth IRA — contribution basis (free)" in _syms, "Roth basis tranche missing"
    assert "Roth IRA — earnings (locked until 59½)" in _syms, "Roth earnings tranche missing"
    _free = t[t["Symbol"] == "Roth IRA — contribution basis (free)"].iloc[0]
    _earn = t[t["Symbol"] == "Roth IRA — earnings (locked until 59½)"].iloc[0]
    assert "Tier 1" in _free["Tier"] and float(_free["Cost to cash ($)"]) == 0.0, "basis tranche must be Tier-1, free"
    assert float(_free["Value ($)"]) == pytest.approx(20_000.0, abs=1.0), "basis tranche == entered basis"
    assert "locked" in _earn["Tier"] and float(_earn["Cost to cash ($)"]) > 0, "earnings tranche must be locked"
    # Tier-header cards reflect the shift: Tier 1 now includes the $20k basis tranche.
    assert _num(mets["Tier 1 · taxable, minimal gains"]) > 20_000.0, "Tier 1 must include the Roth basis tranche"
    # Fix 1 — no locked (Tier-3) row shows $0 cost
    locked = t[t["Tier"].str.contains("locked")]
    assert not locked.empty and (locked["Cost to cash ($)"] > 0).all(), "no locked row may show $0 cost"
    # Feature A — entering a target names the top-N holdings to sell
    at.number_input[0].set_value(40000).run()
    assert not at.exception
    assert any("To raise" in s.value and "sell the top" in s.value for s in at.success), (
        "raise-cash summary missing"
    )
