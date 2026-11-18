"""Full tax profiles for tests that price tax (#706, #724).

No committed rate is the owner's. A test prices with SYNTHETIC rates: the federal ones
from personal_profile.DEMO_FEDERAL_TAX (rates that are no recent year's federal bracket
or gains rate), the state and local ones from DEMO_TAX_RESIDENCE (one invented
jurisdiction at an invented rate). Each profile is built the way every page builds its
own, liquidity.residence_tax_profile, so a test exercises the same path.

SYNTHETIC_TAX_PROFILE has no net investment income tax, as the demo profile does not.
SYNTHETIC_NIIT_PROFILE is the same with the tax applying, for the tests of where it
reaches.
"""
from __future__ import annotations

import copy

from src.liquidity import residence_tax_profile
from src.personal_profile import DEMO_FEDERAL_TAX, DEMO_TAX_RESIDENCE, _federal


def synthetic_federal(niit_applies: bool = False) -> dict:
    """The demo federal profile as load_federal_tax returns it, with the net investment
    income tax applying or not."""
    raw = copy.deepcopy(DEMO_FEDERAL_TAX)
    raw["niit"]["applies"] = niit_applies
    fed, problem = _federal(raw)
    assert problem is None, problem
    return {**fed, "source": "demo-synthetic", "problem": None}


SYNTHETIC_FEDERAL: dict = synthetic_federal()
SYNTHETIC_TAX_PROFILE: dict[str, float] = residence_tax_profile(DEMO_TAX_RESIDENCE,
                                                                SYNTHETIC_FEDERAL)
SYNTHETIC_NIIT_PROFILE: dict[str, float] = residence_tax_profile(
    DEMO_TAX_RESIDENCE, synthetic_federal(niit_applies=True))
