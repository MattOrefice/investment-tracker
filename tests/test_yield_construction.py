"""#213 — the TIPS yield is CONSTRUCTED from published series, not assumed.

`SLEEVE_ASSUMED_YIELD["tips"]` shipped at 0.025 and read as a real yield, because it
essentially was one: measured at #213, 2.50% sits within 15bp of DFII10's 10-year TIPS
real yield of 2.35%. That is evidence about what the entry WAS, not a diagnosis of what
it should be — a TIPS holder's taxable income is the real yield **plus** the inflation
accrual, and the entry carried only the first half.

WHY THIS IS A CORRECTION AND NOT A DISCLOSURE. Everywhere else this workstream found a
number it could not source, it disclosed (see #191, #210). Here both components are
published and already cached, so a disclosure would have documented a gap that need not
exist. The asymmetry decided it: constructing the value lets the card DELETE a caveat,
while disclosing would have added one.

WHY IT IS DEFENSIBLE RATHER THAN MERELY REASONABLE. The two components sum to 4.61%
against DGS10's 4.60% nominal 10-year on the same date — the Fisher identity reproduced
to 1bp from a third, independent cached series. So the construction is validated by
arithmetic rather than by argument, and `test_the_construction_re_adds_to_the_shipped
_value` below is that validation as an assertion. This is the analogue of
`test_entry_values_match_their_proxies_recomputed_from_the_cache` in
tests/test_yield_proxies.py: a declared basis that is never recomputed is a claim.

WHY A DICT RATHER THAN A COMMENT. Only a structure can be enumerated and re-added. A
comment describing the arithmetic can be wrong forever; a dict whose components a test
sums cannot drift from the value it explains.

FROZEN AND DATED, NOT LIVE. src/macro.py can fetch both series and already caches them,
but the yield table has no network dependency and daily variance, and that property is
worth more than four weeks of vintage.
"""
from __future__ import annotations

from datetime import date

from src.location_config import (
    SLEEVE_ASSUMED_YIELD,
    SLEEVE_YIELD_CONSTRUCTION,
    SLEEVE_YIELD_PROXY,
)


# ── the declaration ──────────────────────────────────────────────────────────

def test_tips_declares_a_construction():
    """The entry that #213 corrected must say so where the value lives."""
    assert "tips" in SLEEVE_YIELD_CONSTRUCTION, (
        "tips carries a constructed value with no declared construction — the #191 "
        "defect in a new place"
    )


def test_every_constructed_entry_declares_formula_series_and_as_of():
    """Where a proxy entry declares ticker, spread and coverage, a constructed entry
    declares its formula, its series IDs and its as-of date. Same obligation, different
    fields — the value does not travel without its basis."""
    assert SLEEVE_YIELD_CONSTRUCTION, "empty map — this test would pass vacuously"
    for sleeve, decl in SLEEVE_YIELD_CONSTRUCTION.items():
        assert decl.get("formula"), f"{sleeve} declares no formula"
        assert decl.get("components"), f"{sleeve} declares no component series"
        assert decl.get("as_of"), f"{sleeve} declares no as-of date"
        assert len(decl["components"]) >= 2, (
            f"{sleeve}'s formula combines fewer than two series — a one-series "
            f"construction is a proxy, and belongs in SLEEVE_YIELD_PROXY"
        )


def test_the_construction_re_adds_to_the_shipped_value():
    """THE LOAD-BEARING TEST. Sum the declared components; they must equal the value.

    This is what makes a constructed entry defensible rather than merely plausible.
    Without it the declaration is prose sitting next to a number, free to disagree with
    it — which is exactly the failure #191 closed for proxy entries and would reopen
    here in a new shape.
    """
    assert SLEEVE_YIELD_CONSTRUCTION, "empty map — this test would pass vacuously"
    for sleeve, decl in SLEEVE_YIELD_CONSTRUCTION.items():
        total = round(sum(decl["components"].values()), 4)
        assert total == SLEEVE_ASSUMED_YIELD[sleeve], (
            f"{sleeve}: declared components sum to {total:.4%} but the table ships "
            f"{SLEEVE_ASSUMED_YIELD[sleeve]:.4%} — the declaration and the value have "
            f"drifted apart"
        )


def test_the_as_of_date_is_real_and_not_in_the_future():
    """A dated snapshot with an unparseable or future date is not dated."""
    assert SLEEVE_YIELD_CONSTRUCTION, "empty map — this test would pass vacuously"
    for sleeve, decl in SLEEVE_YIELD_CONSTRUCTION.items():
        stamped = date.fromisoformat(decl["as_of"])
        assert stamped <= date.today(), f"{sleeve} is stamped in the future"


def test_the_two_declared_bases_are_disjoint():
    """An entry has one basis. Appearing in both maps means neither can be trusted as
    the answer to "where did this number come from"."""
    both = set(SLEEVE_YIELD_CONSTRUCTION) & set(SLEEVE_YIELD_PROXY)
    assert not both, f"sleeves declaring two different bases: {sorted(both)}"


def test_the_tenor_approximation_is_named_in_the_entry():
    """SCHP holds broad-maturity TIPS while the construction uses the 10-year point.
    That approximation is declared rather than silent — #231 tracks refining it.

    Named because an undeclared approximation is the same defect class as an undeclared
    proxy: the reader cannot see that a choice was made.
    """
    note = SLEEVE_YIELD_CONSTRUCTION["tips"].get("note", "")
    assert "10-year" in note, "the entry does not say which tenor it used"
    assert "broad-maturity" in note or "broad maturity" in note, (
        "the entry does not name what the tenor approximates"
    )
    assert "#231" in note, "the approximation is named but not tracked"


def test_the_cross_check_that_validates_it_is_recorded():
    """The DGS10 agreement is the reason this construction is defensible. If it is not
    written down, a future reader has to re-derive why 4.61% was acceptable."""
    note = SLEEVE_YIELD_CONSTRUCTION["tips"].get("note", "")
    assert "DGS10" in note, "the nominal cross-check series is not named"


def test_the_construction_is_frozen_not_fetched():
    """The yield table must not acquire a network dependency. Pinned as source because
    the temptation is real: src/macro.py already caches both series, so wiring them live
    is a two-line change that would make a rendered figure vary by day.
    """
    import inspect

    import src.location_config as lc
    src = inspect.getsource(lc)
    assert "fetch_fred_series" not in src, (
        "location_config now fetches FRED at import — the yield table's no-network "
        "property is the reason the construction was frozen and dated"
    )


# ── the card prose the correction changes ────────────────────────────────────

def test_the_pros_no_longer_adds_phantom_income_to_the_figure():
    """The pros said the holdings generate "{annual_benefit} of ordinary income
    annually, PLUS the phantom income the TIPS accrue" — correct while the tips yield
    was a real yield only, and wrong once the figure includes the accrual.

    The word that mattered was "plus": it told the reader the accrual sat OUTSIDE the
    number. Now it is inside, so the clause would double-count it in prose.
    """
    from src.location_actions import _SAA_TAXABLE_PROS

    assert "phantom" not in _SAA_TAXABLE_PROS, (
        "the figure now includes the inflation accrual, so a clause adding it on top "
        "double-counts it"
    )
    assert "ordinary income and phantom income" not in _SAA_TAXABLE_PROS, (
        "and it must not return in the conflated form either — only SCHP throws it"
    )


def test_the_cons_states_the_drag_without_qualification():
    """CONFIRMED, not assumed. The cons says "The drag is {annual_benefit} a year" with
    no phantom-income qualifier — a sentence that was incomplete while the figure
    excluded the accrual and becomes correct, unedited, now that it includes it.

    Asserted because "it happens to be right already" is the kind of claim that is true
    when written and silently false after the next edit to either half.
    """
    from src.location_actions import _SAA_TAXABLE_CONS

    assert "The drag is {annual_benefit} a year" in _SAA_TAXABLE_CONS
    assert "phantom" not in _SAA_TAXABLE_CONS


def test_the_seed_still_attributes_the_accrual_to_SCHP():
    """The accrual did not stop being a fact about SCHP — it stopped being EXCLUDED from
    the figure. The seed's per-ticker note is the source of truth for the former and
    must survive a change that only concerns the latter.
    """
    import csv as _csv
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    rows = {r["symbol"]: (r.get("notes") or "").lower()
            for r in _csv.DictReader(
                (root / "data" / "seed" / "securities_household.csv").open(
                    encoding="utf-8-sig"))}
    assert "phantom" in rows["SCHP"], "seed no longer attributes phantom income to SCHP"
    assert "phantom" not in rows["PDBC"], "seed now attributes phantom income to PDBC"


# ── the rendered disclosure, which is where a declared basis becomes visible ──

def test_the_note_counts_a_constructed_row_separately_from_authored():
    """A basis that exists in the config and not in the rendered note is not disclosed.

    Before #213 taught it, `yield_assumption_note` split table rows two ways — proxied
    or "authored with NO DECLARED BASIS AT ALL" — so the constructed tips row landed in
    the second bucket and the page asserted the opposite of what the config now records.
    That is the same understatement the function's own docstring warns about for the
    proxy case, one paragraph above the code that made it.
    """
    import pandas as pd

    from src.location_actions import yield_assumption_note

    reg = pd.DataFrame({
        "symbol":      ["SCHP", "VOO",           "VNQ"],
        "sleeve":      ["tips",  "us_large_core", "real_assets_reit"],
        "yield_basis": ["table", "table",         "table"],
    })
    note = yield_assumption_note(reg)

    assert "1 of 3 rows** uses a **constructed** yield" in note, (
        f"the constructed row is not disclosed as such: {note!r}"
    )
    assert "1 of 3 rows** use an **authored** yield" in note, (
        "the genuinely-authored row (real_assets_reit) should still be counted — got "
        f"a different count, so the three-way split is mis-partitioned: {note!r}"
    )
    assert "no declared basis" in note, "the authored clause lost its point"


def test_the_note_partitions_table_rows_exactly():
    """proxied + constructed + authored must equal the table rows — no row counted
    twice, none dropped. Asserted as arithmetic because an off-by-one here reads as a
    plausible sentence."""
    import re

    import pandas as pd

    from src.location_actions import yield_assumption_note

    sleeves = ["tips", "us_large_core", "us_small_value", "real_assets_reit", "cash"]
    reg = pd.DataFrame({
        "symbol":      [f"S{i}" for i in range(len(sleeves))],
        "sleeve":      sleeves,
        "yield_basis": ["table"] * len(sleeves),
    })
    note = yield_assumption_note(reg)
    counted = sum(int(m) for m in re.findall(r"\*\*(\d+) of 5 rows\*\*", note))
    assert counted == len(sleeves), (
        f"the note accounts for {counted} of {len(sleeves)} table rows: {note!r}"
    )


def test_the_note_says_the_construction_is_dated_not_live():
    """The reader must be able to tell a frozen snapshot from a live feed — otherwise a
    four-week-old figure reads as today's market."""
    import pandas as pd

    from src.location_actions import yield_assumption_note

    reg = pd.DataFrame({"symbol": ["SCHP"], "sleeve": ["tips"], "yield_basis": ["table"]})
    note = yield_assumption_note(reg)
    assert "dated snapshot" in note and "not a live feed" in note
