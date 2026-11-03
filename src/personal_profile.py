"""Personal profile (date of birth, Roth contribution basis, ordinary income, tax
residence).

Consumed by the Liquidity page (DOB / Roth basis / tax residence) and the Asset
Location page (ordinary income, which sizes the 0% long-term-cap-gains budget).

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
# A round, obviously-invented salary. Chosen above the single-filer 0%-LTCG
# ceiling so the demo exercises the exhausted-bracket path (the interesting one),
# not so it resembles anyone's actual income.
DEMO_ORDINARY_INCOME: float = 100_000.0

# A synthetic tax residence for demo mode and tests: one invented jurisdiction at an
# invented rate, named so it cannot be read as a place or a published rate. Where the
# owner lives, and that place's rates, belong only in private/personal_profile.json, so
# the committed tree names no home state.
DEMO_TAX_RESIDENCE: dict = {
    "jurisdictions": [
        {"name": "Example State (synthetic)", "ordinary_rate": 0.05, "gains_rate": 0.05,
         "source": "synthetic demo value, not a published rate",
         "source_date": "synthetic"},
    ],
}

# A rate above this is not a state or local income-tax rate; it is a typo (5 for
# 0.05). The loader refuses it rather than pricing a sale at 500%.
_MAX_JURISDICTION_RATE: float = 0.25

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


def load_income_profile(is_demo: bool) -> dict:
    """Return the ordinary-income profile:
        {ordinary_income: float|None, source: str}

    Same shape and same graceful degradation as load_roth_profile: demo returns
    the synthetic constant; personal reads private/personal_profile.json (key
    ``ordinary_income``); a missing, unparseable, or non-positive value returns
    None rather than raising.

    None means UNKNOWN, and callers must treat it as such — NOT as zero. Income
    sizes the 0% long-term-cap-gains budget as (ceiling − income), so a zero
    stand-in would compute the FULL ceiling as available headroom and advise
    realizing gains tax-free that are in fact taxed. Unknown income must collapse
    the budget to nothing, the same way a missing Roth basis leaves the whole
    Roth locked: the absent-profile default is the one that cannot mislead.
    """
    if is_demo:
        return {
            "ordinary_income": float(DEMO_ORDINARY_INCOME),
            "source": "demo-synthetic",
        }

    if _PROFILE_PATH.exists():
        try:
            raw = json.loads(_PROFILE_PATH.read_text(encoding="utf-8"))
            value = raw.get("ordinary_income")
            if value is not None:
                income = float(value)
                if income > 0:
                    return {
                        "ordinary_income": income,
                        "source": "private/personal_profile.json",
                    }
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            # Malformed file or value: fall through to the unknown default
            # rather than raising and taking the whole page down.
            pass

    return {"ordinary_income": None, "source": "absent"}


def _jurisdictions(raw) -> "tuple[list[dict], str | None]":
    """The tax residence's jurisdictions, or ([], why not). Each needs a name, an
    ordinary rate and a gains rate as fractions in [0, 0.25], the published table the
    rates come from (``source``) and that table's date (``source_date``). An entry
    missing its source or date is refused: a rate on the page is cited or it goes."""
    if not isinstance(raw, dict):
        return [], "tax_residence is not an object"
    entries = raw.get("jurisdictions")
    if not isinstance(entries, list) or not entries:
        return [], "tax_residence.jurisdictions is missing or empty"
    out = []
    for i, e in enumerate(entries):
        if not isinstance(e, dict):
            return [], f"jurisdiction {i + 1} is not an object"
        name = str(e.get("name") or "").strip()
        source = str(e.get("source") or "").strip()
        source_date = str(e.get("source_date") or "").strip()
        if not name:
            return [], f"jurisdiction {i + 1} has no name"
        if not source or not source_date:
            return [], f"{name} has no source or no source_date for its rates"
        rates = {}
        for key in ("ordinary_rate", "gains_rate"):
            try:
                rate = float(e.get(key))
            except (TypeError, ValueError):
                return [], f"{name}'s {key} is not a number"
            if not 0.0 <= rate <= _MAX_JURISDICTION_RATE:
                return [], (f"{name}'s {key} is {rate}, outside 0 to "
                            f"{_MAX_JURISDICTION_RATE} (a rate is a fraction: 0.05, not 5)")
            rates[key] = rate
        out.append({"name": name, **rates, "source": source, "source_date": source_date})
    return out, None


def load_tax_residence(is_demo: bool) -> dict:
    """Return the tax residence the Liquidity page prices state and local tax with:
        {jurisdictions: [{name, ordinary_rate, gains_rate, source, source_date}, ...],
         source: str, problem: str|None}

    Demo returns DEMO_TAX_RESIDENCE. Personal reads ``tax_residence`` from
    private/personal_profile.json. Absent, unparseable or invalid, it returns no
    jurisdictions, with ``problem`` saying why, and the page says that state and local
    tax are not included. It never falls back to a committed home state: there is none.
    """
    if is_demo:
        return {"jurisdictions": [dict(j) for j in DEMO_TAX_RESIDENCE["jurisdictions"]],
                "source": "demo-synthetic", "problem": None}

    if not _PROFILE_PATH.exists():
        return {"jurisdictions": [], "source": "absent",
                "problem": "private/personal_profile.json does not exist"}
    try:
        raw = json.loads(_PROFILE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"jurisdictions": [], "source": "invalid",
                "problem": "private/personal_profile.json could not be read as JSON"}
    if not isinstance(raw, dict) or "tax_residence" not in raw:
        return {"jurisdictions": [], "source": "absent",
                "problem": "private/personal_profile.json has no tax_residence"}
    juris, problem = _jurisdictions(raw["tax_residence"])
    if problem:
        return {"jurisdictions": [], "source": "invalid", "problem": problem}
    return {"jurisdictions": juris, "source": "private/personal_profile.json",
            "problem": None}
