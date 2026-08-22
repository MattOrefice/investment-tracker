"""The realization term takes the same character as the income term. #278.

`cost_to_realize` was a flat `embedded_gain * ltcg` — right for almost everything and
wrong for a physical-metal grantor trust, whose federal long-term rate is 28% rather
than 15%. #278 as filed dismissed this path ("`ltcg` … is used only for
`cost_to_realize` on a sale, never for the recurring drag"), which is true and does
not make it correct there.

WHY THESE TESTS DO NOT USE IAU, stated because the omission would otherwise look like
avoidance. **Gold cannot reach a taxable realization in this model at all**, and that
is structural rather than a fact about today's positions:

  - case A/B require `annual_benefit > 0`, and `SLEEVE_ASSUMED_YIELD["real_assets_gold"]`
    is an authored **0.0** — gold throws off no income — so an A/B row is suppressed
    before it can carry a cost
  - case D requires `roth_rank < taxable_rank`, and `real_assets_gold` is in **neither**
    priority map, so both are infinity and no D fires
  - case C is in-shelter by definition, so `cost_to_realize` is 0 by construction

So the collectibles correction is **entirely latent**: it changes no rendered figure
today and becomes live the moment gold is held in taxable, or any collectibles-
character sleeve carries a yield. Tests that pinned IAU's rendered cost would
therefore be pinning a zero, and would pass with the fix reverted.

The kill has to happen at the CALL SITE regardless — a test of
`realization_rate("collectibles", …)` passes on a register that never calls it, which
is the presence-is-not-use trap. So these give a *yield-bearing* sleeve the
collectibles character and read the register's own output.
"""
import pandas as pd
import pytest

import src.household as hh
import src.location_config as lc
from src.location_config import (ACCOUNT_SHELTER_PRIORITY,
                                 SLEEVE_PRIORITY_BY_ACCOUNT_TYPE, TAX_PROFILE)

GAIN = 10_000.0
VALUE = 60_000.0


def _fixture():
    acct = pd.DataFrame([
        {"pseudonym": "acct_tax", "display_name": "Taxable", "tax_treatment": "taxable"},
    ])
    sec = pd.DataFrame([
        {"ticker": "COLL", "name": "Collectible Fund", "tax_efficiency": "low",
         "sleeve_category": "multi_sector_fi"},
    ])
    pos = pd.DataFrame([
        {"pseudonym": "acct_tax", "symbol": "COLL", "current_value": VALUE,
         "total_gain_loss": GAIN, "cost_basis_total": VALUE - GAIN},
    ])
    return pos, acct, sec


def _register(pos, acct, sec):
    return hh.build_location_register(pos, acct, sec, TAX_PROFILE,
                                      SLEEVE_PRIORITY_BY_ACCOUNT_TYPE,
                                      ACCOUNT_SHELTER_PRIORITY)


def test_a_collectibles_holding_pays_the_collectibles_rate_on_a_taxable_sale(monkeypatch):
    """THE CALL SITE, which is the whole defect. Read off the register's own
    `cost_to_realize`, not off realization_rate's return — the function was never
    wrong, the line that failed to call it was."""
    monkeypatch.setitem(lc.SLEEVE_TAX_CHARACTER, "multi_sector_fi", "collectibles")
    reg = _register(*_fixture())
    row = reg[reg["symbol"] == "COLL"]
    assert len(row) == 1, "fixture did not produce a taxable row with a gain"
    expected = GAIN * (lc.COLLECTIBLES_FEDERAL_RATE + TAX_PROFILE["state_ltcg"])
    assert row.iloc[0]["cost_to_realize"] == pytest.approx(round(expected, 2))


def test_the_same_holding_pays_the_ordinary_ltcg_rate_without_the_character():
    """Both sides of the comparison move. Identical fixture, character left alone:
    if this and the test above agreed, neither would be measuring the character."""
    reg = _register(*_fixture())
    row = reg[reg["symbol"] == "COLL"]
    expected = GAIN * (TAX_PROFILE["federal_ltcg"] + TAX_PROFILE["state_ltcg"])
    assert row.iloc[0]["cost_to_realize"] == pytest.approx(round(expected, 2))


def test_the_two_characters_produce_different_costs(monkeypatch):
    """Assert-it-mutated at the register level: a `cost_to_realize` that ignored
    character would satisfy one of the two tests above by coincidence."""
    plain = _register(*_fixture()).iloc[0]["cost_to_realize"]
    monkeypatch.setitem(lc.SLEEVE_TAX_CHARACTER, "multi_sector_fi", "collectibles")
    coll = _register(*_fixture()).iloc[0]["cost_to_realize"]
    assert coll > plain, "the collectibles rate is not reaching cost_to_realize"


def test_the_income_rate_is_not_what_reaches_cost_to_realize(monkeypatch):
    """M4's target, named. `collectibles` income is ordinary (25.07%) and its gain is
    31.07% — so a call site that reached for income_rate instead would produce a
    plausible, wrong number rather than an error."""
    monkeypatch.setitem(lc.SLEEVE_TAX_CHARACTER, "multi_sector_fi", "collectibles")
    reg = _register(*_fixture())
    cost = reg.iloc[0]["cost_to_realize"]
    wrong = round(GAIN * hh.income_rate("collectibles", TAX_PROFILE), 2)
    assert cost != pytest.approx(wrong), (
        "cost_to_realize equals the INCOME rate applied to the gain — the two terms "
        "have been conflated")


def test_the_income_term_is_untouched_by_the_gain_character(monkeypatch):
    """The converse, and the reason one vocabulary can serve two terms: switching to
    `collectibles` must move the gain and leave the income alone, because its income
    treatment is ordinary either way."""
    before = _register(*_fixture()).iloc[0]["annual_benefit"]
    monkeypatch.setitem(lc.SLEEVE_TAX_CHARACTER, "multi_sector_fi", "collectibles")
    after = _register(*_fixture()).iloc[0]["annual_benefit"]
    assert before == pytest.approx(after)


# ── the latency, asserted so it cannot quietly stop being true ────────────────

def test_gold_cannot_reach_a_taxable_realization_today():
    """RECORDS THE LATENCY, and goes red when it ends.

    If gold ever gains a non-zero assumed yield, or enters either priority map, a
    collectibles row can reach `cost_to_realize` on the real book — and this fix
    stops being latent. That is a change worth noticing, not a test worth deleting:
    the point of the assertion is the day it fails.
    """
    assert lc.SLEEVE_ASSUMED_YIELD["real_assets_gold"] == 0.0, (
        "gold now carries a yield — a case A/B row can survive the benefit filter, "
        "so the collectibles rate now reaches a rendered cost_to_realize (#278)")
    assert "real_assets_gold" not in SLEEVE_PRIORITY_BY_ACCOUNT_TYPE["roth_ira"]
    assert "real_assets_gold" not in SLEEVE_PRIORITY_BY_ACCOUNT_TYPE["taxable"]


def test_gold_still_declares_the_collectibles_character_anyway():
    """Latent is not the same as absent. The declaration is what makes the model able
    to say the thing; whether a row currently exercises it is a fact about the book."""
    assert lc.SLEEVE_TAX_CHARACTER["real_assets_gold"] == "collectibles"
