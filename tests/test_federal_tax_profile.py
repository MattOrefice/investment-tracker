"""The federal rates come from the private profile, with their tax year and IRS source
(#724).

The federal ordinary marginal rate, the long-term gains rate and whether the net
investment income tax applies follow from the owner's income, so they are personal and
never committed. They sit in the gitignored private/personal_profile.json as
`federal_tax`, each entry with the tax year it is for, the IRS source it was read from
and that source's date. personal_profile.load_federal_tax reads them; demo mode and
tests read DEMO_FEDERAL_TAX, synthetic and labelled so.

Every profile here is synthetic, written to a temporary file. The one test that reads
the private profile checks only that it loads, and prints nothing from it.
"""
from __future__ import annotations

import copy
import json

import pytest

import src.personal_profile as pp

# Every federal bracket rate and long-term gains rate of recent tax years. A synthetic
# rate must be none of them, so it cannot be read as anyone's.
FEDERAL_BRACKETS = {0.10, 0.12, 0.22, 0.24, 0.32, 0.35, 0.37}
FEDERAL_GAINS_RATES = {0.0, 0.15, 0.20}

VALID = {
    "ordinary_rate": {"tax_year": 2031, "rate": 0.31, "source": "Synthetic Rev. Proc. 1",
                      "source_date": "2030-11-01"},
    "gains_rate": {"tax_year": 2031, "rate": 0.11, "source": "Synthetic Rev. Proc. 1",
                   "source_date": "2030-11-01"},
    "niit": {"tax_year": 2031, "applies": True, "source": "Synthetic instructions",
             "source_date": "2030-04-01"},
}


def _load(monkeypatch, tmp_path, profile) -> dict:
    path = tmp_path / "personal_profile.json"
    if profile is not None:
        path.write_text(profile if isinstance(profile, str) else json.dumps(profile),
                        encoding="utf-8")
    monkeypatch.setattr(pp, "_PROFILE_PATH", path)
    return pp.load_federal_tax(False)


def test_demo_reads_the_synthetic_profile_labelled_as_one():
    f = pp.load_federal_tax(True)
    assert f["problem"] is None and f["source"] == "demo-synthetic"
    assert f["federal_marginal"] not in FEDERAL_BRACKETS
    assert f["federal_ltcg"] not in FEDERAL_GAINS_RATES
    assert f["niit"] == 0.0
    assert all("synthetic" in src and date == "synthetic"
               for src, date in f["sources"].values()), f["sources"]


def test_a_valid_profile_gives_its_rates_year_and_sources(monkeypatch, tmp_path):
    f = _load(monkeypatch, tmp_path, {"federal_tax": VALID})
    assert f["problem"] is None and f["source"] == "private/personal_profile.json"
    assert (f["federal_marginal"], f["federal_ltcg"], f["tax_year"]) == (0.31, 0.11, 2031)
    assert f["niit"] == pytest.approx(pp.NIIT_RATE) == pytest.approx(0.038)
    assert f["sources"]["niit"] == ("Synthetic instructions", "2030-04-01")


def test_where_the_niit_does_not_apply_its_rate_is_zero(monkeypatch, tmp_path):
    raw = copy.deepcopy(VALID)
    raw["niit"]["applies"] = False
    assert _load(monkeypatch, tmp_path, {"federal_tax": raw})["niit"] == 0.0


def _broken(path: "tuple[str, ...]", value) -> dict:
    raw = copy.deepcopy(VALID)
    target = raw
    for key in path[:-1]:
        target = target[key]
    if value is KeyError:
        del target[path[-1]]
    else:
        target[path[-1]] = value
    return raw


@pytest.mark.parametrize("profile, why", [
    (None, "does not exist"),
    ("{not json", "could not be read as JSON"),
    ({"tax_residence": {}}, "has no federal_tax"),
    ({"federal_tax": []}, "federal_tax is not an object"),
    ({"federal_tax": _broken(("gains_rate",), KeyError)}, "gains_rate is missing"),
    ({"federal_tax": _broken(("ordinary_rate", "source"), "")}, "no source"),
    ({"federal_tax": _broken(("niit", "source_date"), KeyError)}, "no source_date"),
    ({"federal_tax": _broken(("gains_rate", "tax_year"), "2031")}, "is not a year"),
    ({"federal_tax": _broken(("niit", "tax_year"), 2030)}, "different tax years"),
    ({"federal_tax": _broken(("ordinary_rate", "rate"), 31)}, "a rate is a fraction"),
    ({"federal_tax": _broken(("ordinary_rate", "rate"), None)}, "is not a number"),
    ({"federal_tax": _broken(("niit", "applies"), "yes")}, "is not true or false"),
])
def test_an_unreadable_profile_gives_no_rates_and_says_why(monkeypatch, tmp_path,
                                                           profile, why):
    """No committed rate stands in: every way the entry can be wrong leaves the rates
    None, and the problem names it."""
    f = _load(monkeypatch, tmp_path, profile)
    assert f["federal_marginal"] is None and f["federal_ltcg"] is None
    assert f["niit"] is None and f["tax_year"] is None
    assert why in f["problem"], f["problem"]


def test_the_template_names_every_entry_and_types_no_rate():
    """personal_profile.example.json shows the shape and leaves the rates to be filled
    in: a template with a rate in it would be a committed rate."""
    from pathlib import Path
    raw = json.loads((Path(pp.__file__).resolve().parents[1]
                      / "personal_profile.example.json").read_text(encoding="utf-8"))
    fed = raw["federal_tax"]
    assert set(fed) == set(pp._FEDERAL_ENTRIES)
    assert fed["ordinary_rate"]["rate"] is None and fed["gains_rate"]["rate"] is None
    assert fed["niit"]["applies"] is None
    assert all({"tax_year", "source", "source_date"} <= set(e) for e in fed.values())


def test_the_private_profile_loads_when_present():
    """Personal mode only. The owner's federal_tax loads with no problem; nothing read
    from it is printed or written."""
    if not pp._PROFILE_PATH.exists():
        pytest.skip("no private profile (personal-mode only)")
    f = pp.load_federal_tax(False)
    assert f["problem"] is None, "the private federal_tax did not load"
    assert isinstance(f["tax_year"], int)
