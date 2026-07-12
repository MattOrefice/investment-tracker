"""Personal profile (date of birth, Roth contribution basis) for the Liquidity page.

SECURITY — real values live ONLY in ``private/personal_profile.json`` (gitignored,
user-populated, NEVER committed), the same pattern as ``private/account_map.json``.
This module commits nothing but OBVIOUSLY-SYNTHETIC demo constants. It never reads
or writes a real date of birth or basis into version control.

Why a profile at all: the IRS Roth withdrawal ordering (contributions out first,
tax- and penalty-free at any age; earnings only after 59½ + a 5-year account) can't
be inferred from a positions export — it needs the contribution basis and the
owner's age. Those are supplied here.

Demo mode uses the synthetic constants below (so the feature is demonstrable with
no real data). Personal mode reads the gitignored file; if it is absent or invalid,
the profile degrades gracefully to a zero basis — which reproduces the prior
whole-Roth-locked behavior rather than crashing (unlike account_map.json, a missing
profile is not fatal; it just disables the basis split).
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PROFILE_PATH = _REPO_ROOT / "private" / "personal_profile.json"

# ── Synthetic demo constants — OBVIOUSLY NOT REAL ────────────────────────────────
# Used in demo mode and tests. Chosen so both Roth tranches are non-zero on a
# typical Roth balance (a partial basis) and the owner is well under 59½ (earnings
# stay locked), so the split is visible. Never replace these with real values —
# real values belong only in private/personal_profile.json.
DEMO_ROTH_CONTRIBUTION_BASIS: float = 15_000.0
DEMO_DATE_OF_BIRTH: str = "1990-01-01"   # synthetic placeholder; age < 59½

# The age at which Roth earnings become penalty-free (the 5-year account rule is
# handled by caveat, not computed — see the page).
QUALIFIED_WITHDRAWAL_AGE: float = 59.5


def is_qualified_age(dob: date | None, as_of: date) -> bool:
    """True if the owner is at least 59.5 years old as of ``as_of``. Approximate
    (age = elapsed days / 365.25); the exact 59½ boundary and the 5-year account
    rule are disclosed in the page caveat, not relied on for a hard cutoff. A None
    DOB (no profile) is treated as not-qualified — the conservative default."""
    if dob is None:
        return False
    return ((as_of - dob).days / 365.25) >= QUALIFIED_WITHDRAWAL_AGE


def load_roth_profile(is_demo: bool, as_of: date) -> dict:
    """Return the Roth profile for the liquidity model:
        {roth_contribution_basis: float, date_of_birth: date|None,
         is_qualified_age: bool, source: str}

    Demo mode returns the synthetic constants above. Personal mode reads
    private/personal_profile.json (keys: ``date_of_birth`` ISO string,
    ``roth_contribution_basis`` number); a missing or unparseable file returns a
    zero basis / None DOB (graceful — the whole Roth stays locked, as before).
    ``is_qualified_age`` is computed from the DOB against ``as_of``.
    """
    if is_demo:
        dob = date.fromisoformat(DEMO_DATE_OF_BIRTH)
        return {
            "roth_contribution_basis": float(DEMO_ROTH_CONTRIBUTION_BASIS),
            "date_of_birth": dob,
            "is_qualified_age": is_qualified_age(dob, as_of),
            "source": "demo-synthetic",
        }

    if _PROFILE_PATH.exists():
        try:
            raw = json.loads(_PROFILE_PATH.read_text(encoding="utf-8"))
            basis = float(raw.get("roth_contribution_basis", 0.0) or 0.0)
            dob_raw = raw.get("date_of_birth")
            dob = date.fromisoformat(str(dob_raw)) if dob_raw else None
            return {
                "roth_contribution_basis": max(0.0, basis),
                "date_of_birth": dob,
                "is_qualified_age": is_qualified_age(dob, as_of),
                "source": "private/personal_profile.json",
            }
        except Exception:
            # Malformed file: fall through to the safe zero-basis default rather
            # than raising and taking the whole page down.
            pass

    return {
        "roth_contribution_basis": 0.0,
        "date_of_birth": None,
        "is_qualified_age": False,
        "source": "absent",
    }
