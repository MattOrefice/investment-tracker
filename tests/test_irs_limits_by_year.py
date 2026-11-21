"""The Asset Location page's IRS limits come from a table by tax year, each figure cited
to the document that states it (#735, decided by the owner on November 21, 2026).

src/location_config.IRS_LIMITS holds a row per tax year. Each figure in a row is read off
the document that states it, and the row cites each document with its date:
  * retirement plans and IRAs: IRS Notice 2025-67 for 2026, Notice 2026-66 for 2027;
  * HSAs: Rev. Proc. 2025-19 for 2026, Rev. Proc. 2026-24 for 2027.
The HSA catch-up is 26 U.S.C. § 223(b)(3)(B)'s own amount, not adjusted by year
(HSA_CATCHUP_55). The typed _2026 constants are gone.

Page 14's reference table (location_actions.ideal_location_reference) shows the row for
the tax year the page's federal rates are for and cites the row's documents. A year
with no row shows no limit, never another year's.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import src.location_config as lc
from src.location_actions import _fmt_dollars, escape_md, ideal_location_reference

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "pages" / "14_Asset_Location.py"
AMOUNTS = ("ira_contrib_limit", "ira_catchup_50", "workplace_elective_deferral",
           "workplace_catchup_50", "workplace_catchup_60_63", "workplace_415c_total",
           "hsa_contrib_limit_self", "hsa_contrib_limit_family")
RANGES = ("roth_magi_phaseout_single", "roth_magi_phaseout_mfj",
          "trad_ira_deduction_phaseout_single", "trad_ira_deduction_phaseout_mfj")
SOURCES = ("retirement_source", "retirement_source_date", "hsa_source", "hsa_source_date")
OLD = ("IRA_CONTRIB_LIMIT", "IRA_CATCHUP_50", "WORKPLACE_ELECTIVE_DEFERRAL",
       "WORKPLACE_CATCHUP_50", "WORKPLACE_CATCHUP_60_63", "WORKPLACE_415C_TOTAL",
       "ROTH_MAGI_PHASEOUT_SINGLE", "ROTH_MAGI_PHASEOUT_MFJ",
       "TRAD_IRA_DEDUCTION_PHASEOUT_SINGLE", "TRAD_IRA_DEDUCTION_PHASEOUT_MFJ",
       "HSA_CONTRIB_LIMIT_SELF", "HSA_CONTRIB_LIMIT_FAMILY", "HSA_CATCHUP_55")


# ── the table ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("year, notice, notice_date, rev_proc, rev_proc_date", [
    (2026, "Notice 2025-67", "November 13, 2025", "Rev. Proc. 2025-19", "May 19, 2025"),
    (2027, "Notice 2026-66", "November 12, 2026", "Rev. Proc. 2026-24", "June 15, 2026"),
])
def test_each_year_cites_the_documents_its_figures_come_from(year, notice, notice_date,
                                                             rev_proc, rev_proc_date):
    row = lc.IRS_LIMITS[year]
    assert notice in row["retirement_source"] and row["retirement_source_date"] == notice_date
    assert rev_proc in row["hsa_source"] and row["hsa_source_date"] == rev_proc_date


def test_every_row_holds_every_limit():
    assert set(lc.IRS_LIMITS) == {2026, 2027}
    for year, row in lc.IRS_LIMITS.items():
        assert set(row) == set(AMOUNTS) | set(RANGES) | set(SOURCES), year
        assert all(row[k] > 0 for k in AMOUNTS), year
        assert all(0 < row[k][0] < row[k][1] for k in RANGES), year


def test_the_hsa_catch_up_is_the_statutes():
    assert lc.HSA_CATCHUP_55["amount"] == 1_000.0
    assert "§ 223(b)(3)(B)" in lc.HSA_CATCHUP_55["source"]
    assert lc.HSA_CATCHUP_55["source_date"]


def test_the_typed_2026_constants_are_gone():
    assert [n for n in dir(lc) if n.endswith("_2026")] == []
    pat = re.compile(r"\b(?:" + "|".join(OLD) + r")_2026\b")
    files = [p for top in ("src", "pages", "tools", "tests") for p in (ROOT / top).rglob("*.py")]
    hits = [p.relative_to(ROOT).as_posix() for p in files + [ROOT / "app.py"]
            if p != Path(__file__) and pat.search(p.read_text(encoding="utf-8"))]
    assert hits == []


def test_a_year_with_no_row_has_none():
    assert lc.irs_limits(None) is None
    assert lc.irs_limits(max(lc.IRS_LIMITS) + 1) is None
    assert lc.irs_limits(min(lc.IRS_LIMITS) - 1) is None


# ── the reference table the page shows ────────────────────────────────────────

def _cells(row: dict) -> "list[str]":
    """Every limit in ``row`` as the reference prints it."""
    k = lambda x: "\\$" + f"{x / 1000:,.0f}k"                      # noqa: E731
    out = [escape_md(_fmt_dollars(row[a])) for a in AMOUNTS if a != "workplace_415c_total"]
    out.append(k(row["workplace_415c_total"]))
    out += [f"{k(row[r][0])}–{k(row[r][1])}" for r in RANGES if r != "trad_ira_deduction_phaseout_mfj"]
    return out


@pytest.mark.parametrize("year", sorted(lc.IRS_LIMITS))
def test_the_reference_shows_its_years_limits_and_cites_them(year):
    ref = ideal_location_reference(year)
    row = lc.IRS_LIMITS[year]
    assert ref["title"].endswith(f"tax year {year}")
    assert f"{year} contribution limit" in ref["table"]
    for cell in _cells(row):
        assert cell in ref["table"], cell
    assert escape_md(_fmt_dollars(lc.HSA_CATCHUP_55["amount"])) in ref["table"]
    for key in SOURCES:
        assert escape_md(row[key]) in ref["notes"], key
    assert escape_md(lc.HSA_CATCHUP_55["source"]) in ref["notes"]
    other = [y for y in lc.IRS_LIMITS if y != year]
    for y in other:
        mine = set(_cells(row))
        for cell in _cells(lc.IRS_LIMITS[y]):
            if cell not in mine:
                assert cell not in ref["table"], (year, "shows", y, cell)
        assert lc.IRS_LIMITS[y]["retirement_source"] not in ref["notes"]


def test_a_year_with_no_row_shows_no_limit():
    year = max(lc.IRS_LIMITS) + 1
    ref = ideal_location_reference(year)
    assert f"Not recorded for tax year {year}" in ref["table"]
    assert not re.search(r"\\\$\d", ref["table"] + ref["notes"]), "a figure was shown"
    assert f"No IRS limits are recorded for tax year {year}" in ref["notes"]
    assert not any(r["retirement_source"] in ref["notes"] for r in lc.IRS_LIMITS.values())


def test_the_page_shows_the_reference_for_its_federal_rates_tax_year():
    src = PAGE.read_text(encoding="utf-8")
    assert 'ideal_location_reference(_tax_profile["tax_year"])' in src
    # The page reads no limit row itself: the reference is its only route to one.
    assert not re.search(r"IRS_LIMITS\s*\[|irs_limits\s*\(|HSA_CATCHUP_55", src)


def test_the_page_renders_the_reference_for_the_profiles_tax_year_live(monkeypatch):
    """Personal mode: the page on the personal book shows the reference for the tax year
    the private federal profile names. Skips where the personal inputs are absent."""
    from src.household_data import find_latest_positions_csv
    if (find_latest_positions_csv() is None
            or not (ROOT / "data" / "tracker.db").exists()
            or not (ROOT / "private" / "account_map.json").exists()):
        pytest.skip("personal-mode inputs absent")
    import src.config
    import src.db
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    from src.liquidity import residence_tax_profile
    from src.personal_profile import load_federal_tax, load_tax_residence
    year = residence_tax_profile(load_tax_residence(False), load_federal_tax(False))["tax_year"]
    want = ideal_location_reference(year)
    monkeypatch.setattr(src.config, "IS_DEMO", False)
    monkeypatch.setattr(src.db, "DB_PATH", ROOT / "data" / "tracker.db")
    st.cache_data.clear()
    at = AppTest.from_file(str(PAGE), default_timeout=600).run()
    assert not at.exception, at.exception
    labels = [e.label for e in at.expander]
    assert want["title"] in labels, labels
    shown = [m.value for m in at.markdown]
    assert want["table"] in shown and want["notes"] in shown
