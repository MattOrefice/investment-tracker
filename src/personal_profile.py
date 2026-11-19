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

# A synthetic federal profile for demo mode and tests (#724). Neither rate is a federal
# bracket or a federal gains rate in any recent tax year, so it cannot be read as
# anyone's. The owner's federal rates, the tax year they are for and the IRS source each
# was read from belong only in private/personal_profile.json (`federal_tax`).
DEMO_FEDERAL_TAX: dict = {
    "ordinary_rate": {"tax_year": 2027, "rate": 0.20,
                      "source": "synthetic demo value, not a rate read for anyone",
                      "source_date": "synthetic"},
    "gains_rate": {"tax_year": 2027, "rate": 0.10,
                   "source": "synthetic demo value, not a rate read for anyone",
                   "source_date": "synthetic"},
    "niit": {"tax_year": 2027, "applies": False,
             "source": "synthetic demo value, not a determination for anyone",
             "source_date": "synthetic"},
}

# The net investment income tax: 3.8% of the lesser of net investment income and modified
# AGI above a threshold (26 U.S.C. § 1411(a)(1); Instructions for Form 8960). Whether it
# applies is the owner's fact, read from the profile; the rate is the statute's.
NIIT_RATE: float = 0.038

# A rate above this is not a state or local income-tax rate; it is a typo (5 for
# 0.05). The loader refuses it rather than pricing a sale at 500%.
_MAX_JURISDICTION_RATE: float = 0.25
# The same guard for a federal rate: none exceeds 37%.
_MAX_FEDERAL_RATE: float = 0.37

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


_FEDERAL_ENTRIES = ("ordinary_rate", "gains_rate", "niit")


def _federal(raw) -> "tuple[dict | None, str | None]":
    """The federal profile from a ``federal_tax`` object, or (None, why not).

    Three entries, each carrying the tax year it is for, the IRS source it was read
    from and that source's date: ``ordinary_rate`` and ``gains_rate`` ({tax_year, rate,
    source, source_date}, the rate a fraction) and ``niit`` ({tax_year, applies,
    source, source_date}). All three must name the same tax year: a combined rate that
    mixed two years' law would be no year's. An entry missing its year, source or date
    is refused, as a residence's rate is: a rate on the page is cited or it goes."""
    if not isinstance(raw, dict):
        return None, "federal_tax is not an object"
    years, out = set(), {}
    for key in _FEDERAL_ENTRIES:
        e = raw.get(key)
        if not isinstance(e, dict):
            return None, f"federal_tax.{key} is missing or not an object"
        source = str(e.get("source") or "").strip()
        source_date = str(e.get("source_date") or "").strip()
        if not source or not source_date:
            return None, f"federal_tax.{key} has no source or no source_date"
        year = e.get("tax_year")
        if not isinstance(year, int) or isinstance(year, bool) or not 2000 <= year <= 2100:
            return None, f"federal_tax.{key}.tax_year is not a year"
        years.add(year)
        if key == "niit":
            if not isinstance(e.get("applies"), bool):
                return None, "federal_tax.niit.applies is not true or false"
            value = NIIT_RATE if e["applies"] else 0.0
        else:
            try:
                value = float(e.get("rate"))
            except (TypeError, ValueError):
                return None, f"federal_tax.{key}.rate is not a number"
            if not 0.0 <= value <= _MAX_FEDERAL_RATE:
                return None, (f"federal_tax.{key}.rate is {value}, outside 0 to "
                              f"{_MAX_FEDERAL_RATE} (a rate is a fraction: 0.12, not 12)")
        out[key] = {"value": value, "source": source, "source_date": source_date}
    if len(years) != 1:
        return None, f"federal_tax's entries name different tax years: {sorted(years)}"
    return {
        "federal_marginal": out["ordinary_rate"]["value"],
        "federal_ltcg": out["gains_rate"]["value"],
        "niit": out["niit"]["value"],
        "tax_year": years.pop(),
        "sources": {k: (v["source"], v["source_date"]) for k, v in out.items()},
    }, None


def load_federal_tax(is_demo: bool) -> dict:
    """Return the federal rates the Liquidity and Asset Location pages price with (#724):
        {federal_marginal, federal_ltcg, niit, tax_year, sources, source, problem}

    ``niit`` is the net investment income tax's rate where it applies (NIIT_RATE) and
    0.0 where it does not. ``sources`` maps each entry to (source, source_date).

    Demo returns DEMO_FEDERAL_TAX, labelled synthetic. Personal reads ``federal_tax``
    from private/personal_profile.json. Absent, unparseable or invalid, the rates are
    None and ``problem`` says why: a page cannot price a sale or a shelter without the
    federal rates, and no committed rate stands in for the owner's.
    """
    none = {"federal_marginal": None, "federal_ltcg": None, "niit": None,
            "tax_year": None, "sources": {}}
    if is_demo:
        fed, problem = _federal(DEMO_FEDERAL_TAX)
        return {**fed, "source": "demo-synthetic", "problem": problem}
    if not _PROFILE_PATH.exists():
        return {**none, "source": "absent",
                "problem": "private/personal_profile.json does not exist"}
    try:
        raw = json.loads(_PROFILE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {**none, "source": "invalid",
                "problem": "private/personal_profile.json could not be read as JSON"}
    if not isinstance(raw, dict) or "federal_tax" not in raw:
        return {**none, "source": "absent",
                "problem": "private/personal_profile.json has no federal_tax"}
    fed, problem = _federal(raw["federal_tax"])
    if problem:
        return {**none, "source": "invalid", "problem": problem}
    return {**fed, "source": "private/personal_profile.json", "problem": None}


def _withheld(raw) -> "tuple[dict | None, str | None]":
    """The withheld names from a ``withheld_names`` object, or (None, why not)."""
    if not isinstance(raw, dict):
        return None, "withheld_names is not an object"
    names = raw.get("names")
    if (not isinstance(names, list) or not names
            or not all(isinstance(n, dict) and str(n.get("name") or "").strip()
                       for n in names)):
        return None, "withheld_names.names is not a list of {name, kind} entries"
    tickers = raw.get("employer_stock_tickers", [])
    if not isinstance(tickers, list) or not all(isinstance(t, str) and t.strip()
                                                for t in tickers):
        return None, "withheld_names.employer_stock_tickers is not a list of tickers"
    out = {"names": [str(n["name"]).strip() for n in names],
           "employer_stock_tickers": [t.strip() for t in tickers]}
    for key in ("account_labels", "security_names"):
        value = raw.get(key, {})
        if not isinstance(value, dict) or not all(
                isinstance(k, str) and isinstance(v, str) and v.strip()
                for k, v in value.items()):
            return None, f"withheld_names.{key} is not an object of names"
        out[key] = {k: v.strip() for k, v in value.items()}
    return out, None


def load_withheld_names(is_demo: bool) -> dict:
    """The names committed text must never carry, and the real labels the personal book
    shows in their place (#725):
        {names, employer_stock_tickers, account_labels, security_names, source, problem}

    ``names`` are employers and workplace-plan providers; ``employer_stock_tickers``
    are tickers committed text must not tie to an employer. ``account_labels`` (by
    pseudonym) and ``security_names`` (by ticker) are what bootstrap_personal_db writes
    into the personal book instead of the seeds' neutral labels.

    Demo has none: it returns empty collections and no problem. Personal reads
    ``withheld_names`` from private/personal_profile.json; absent or invalid, the
    collections are empty and ``problem`` says why, so the seeds' neutral labels stand.
    """
    empty = {"names": [], "employer_stock_tickers": [], "account_labels": {},
             "security_names": {}}
    if is_demo:
        return {**empty, "source": "demo", "problem": None}
    if not _PROFILE_PATH.exists():
        return {**empty, "source": "absent",
                "problem": "private/personal_profile.json does not exist"}
    try:
        raw = json.loads(_PROFILE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {**empty, "source": "invalid",
                "problem": "private/personal_profile.json could not be read as JSON"}
    if not isinstance(raw, dict) or "withheld_names" not in raw:
        return {**empty, "source": "absent",
                "problem": "private/personal_profile.json has no withheld_names"}
    found, problem = _withheld(raw["withheld_names"])
    if problem:
        return {**empty, "source": "invalid", "problem": problem}
    return {**found, "source": "private/personal_profile.json", "problem": None}
