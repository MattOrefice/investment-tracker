"""#297 — a blend's disclosed basis names its LEAST-verified component.

"look-through" says the yield is derived from the composition. It did not say what the
components rest on, so an AUTHORED component (a judgement) was hidden behind a label
that reads as mechanical. Sleeve keys are drawn from the live basis maps, not hardcoded,
so the tests follow the config.
"""
import pandas as pd

from src.household import BASIS_VERIFICATION_ORDER, blend_weakest_basis
from src.location_actions import format_assumed_yield
from src.location_config import (SLEEVE_YIELD_AUTHORED, SLEEVE_YIELD_CONSTRUCTION,
                                 SLEEVE_YIELD_PROXY)

AUTHORED = sorted(SLEEVE_YIELD_AUTHORED)[0]
PROXY = sorted(SLEEVE_YIELD_PROXY)[0]
CONSTRUCTED = sorted(SLEEVE_YIELD_CONSTRUCTION)[0]


def _comp(*parts):
    return pd.DataFrame([{"fund_symbol": "FND", "underlying_sleeve": s, "weight": w}
                         for s, w in parts])


def test_an_authored_component_is_named_even_when_it_is_small():
    assert blend_weakest_basis("FND", _comp((PROXY, 0.95), (AUTHORED, 0.05))) == "authored"


def test_a_measured_only_blend_names_its_weakest_measurement():
    assert blend_weakest_basis("FND", _comp((PROXY, 0.5), (CONSTRUCTED, 0.5))) == "proxy"
    assert blend_weakest_basis("FND", _comp((CONSTRUCTED, 1.0))) == "constructed"


def test_no_usable_composition_names_nothing():
    assert blend_weakest_basis("FND", None) is None
    assert blend_weakest_basis("OTHER", _comp((PROXY, 1.0))) is None


def test_the_ordering_puts_the_judgement_first():
    assert BASIS_VERIFICATION_ORDER[0] == "authored"
    assert set(BASIS_VERIFICATION_ORDER) == {"authored", "proxy", "constructed", "structural"}


def test_the_rendered_marker_names_the_weakest_component():
    assert (format_assumed_yield(0.0248, "look_through", "authored")
            == "2.48% (look-through; least-verified component: authored)")
    # Without a named component it renders as before; other bases are unchanged.
    assert format_assumed_yield(0.0248, "look_through") == "2.48% (look-through)"
    assert format_assumed_yield(0.06, "authored") == "6.00% (authored)"


def test_the_register_carries_the_weakest_basis_for_a_blend_row():
    """Wiring, not just the helper: a blend held in taxable reaches the register with
    its least-verified component named, and a non-blend row names none."""
    from src.household import build_location_register
    from src.location_config import (ACCOUNT_SHELTER_PRIORITY, BLEND_SLEEVES,
                                      SLEEVE_PRIORITY_BY_ACCOUNT_TYPE, TAX_PROFILE)
    blend = sorted(BLEND_SLEEVES)[0]
    accounts = pd.DataFrame([{"pseudonym": "t", "display_name": "Taxable",
                              "tax_treatment": "taxable", "managed_by": "self"}])
    securities = pd.DataFrame([
        {"ticker": "FND", "name": "Blend", "tax_efficiency": "low", "sleeve_category": blend},
        {"ticker": "VNQ", "name": "REIT", "tax_efficiency": "low",
         "sleeve_category": "real_assets_reit"},
    ])
    positions = pd.DataFrame([
        {"pseudonym": "t", "symbol": s, "description": s, "current_value": 10000.0,
         "total_gain_loss": 0.0, "cost_basis_total": 10000.0} for s in ("FND", "VNQ")])
    reg = build_location_register(positions, accounts, securities, TAX_PROFILE,
                                  SLEEVE_PRIORITY_BY_ACCOUNT_TYPE, ACCOUNT_SHELTER_PRIORITY,
                                  compositions_df=_comp((PROXY, 0.95), (AUTHORED, 0.05)))
    fnd = reg[reg["symbol"] == "FND"]
    assert len(fnd) and set(fnd["yield_basis"]) == {"look_through"}, fnd
    assert set(fnd["yield_basis_weakest"]) == {"authored"}
    assert reg.loc[reg["symbol"] == "VNQ", "yield_basis_weakest"].isna().all()


def test_the_rendered_column_names_the_weakest_component():
    """Page 14 renders the column through assumed_yield_cells: the register row for a
    blend carries its least-verified component all the way to the cell."""
    from src.location_actions import assumed_yield_cells
    rows = pd.DataFrame({"assumed_yield": [0.0248, 0.06], "yield_basis": ["look_through", "authored"],
                         "yield_basis_weakest": ["authored", None]})
    assert assumed_yield_cells(rows) == [
        "2.48% (look-through; least-verified component: authored)", "6.00% (authored)"]
