"""A full tax profile for tests that price state tax (#706).

TAX_PROFILE holds the federal rates only. State and local rates belong to where the
owner lives, which the committed tree does not name, so a test prices with a SYNTHETIC
residence: personal_profile.DEMO_TAX_RESIDENCE, whose one invented jurisdiction has an
invented rate. The profile is built the way every page builds its own,
liquidity.residence_tax_profile, so a test exercises the same path.
"""
from __future__ import annotations

from src.liquidity import residence_tax_profile
from src.personal_profile import DEMO_TAX_RESIDENCE

SYNTHETIC_TAX_PROFILE: dict[str, float] = residence_tax_profile(DEMO_TAX_RESIDENCE)
