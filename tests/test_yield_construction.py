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

from datetime import date, timedelta

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


# ── #231: the age of a dated snapshot ────────────────────────────────────────
#
# WHY RENDERED AND NOT ASSERTED. A test that fails when real data ages is the #200
# defect: it reddens with no commit, which blocks unrelated PRs, silently turns the
# 13F baseline into 14F, and makes bumping the date the cheapest fix — training the
# reader to ignore the very banner asof.py:23-27 exists to keep credible. The repo
# already answered this: staleness_note has 7+ RENDER call sites and no test asserts
# that real committed data is fresh; its own tests inject a today-relative frontier.
# So the age surface is rendered, and every test below derives its dates from the
# entry's own as_of, which cannot expire.
#
# WHY NOT staleness_note ITSELF. Its remediation sentence names
# tools/refresh_market_data.py, which rewrites five committed CSVs. FRED is not among
# them — the construction's series come from macro_cache, a runtime 24-hour cache — so
# reusing it would render an instruction that does not apply to this data. The
# nearest-looking precedent decides the PATTERN and not the function.


def _con_register():
    import pandas as pd
    return pd.DataFrame({
        "symbol":      ["SCHP", "VOO",           "VNQ"],
        "sleeve":      ["tips",  "us_large_core", "real_assets_reit"],
        "yield_basis": ["table", "table",         "table"],
    })


def _as_of() -> date:
    return date.fromisoformat(SLEEVE_YIELD_CONSTRUCTION["tips"]["as_of"])


def test_the_note_names_the_as_of_date_and_its_age():
    """A snapshot the reader cannot date is not meaningfully dated. The existing text
    said "a dated snapshot, not a live feed" — the nature of the figure — without ever
    giving the date, so the reader was told the class and not the instance."""
    from src.location_actions import yield_assumption_note

    note = yield_assumption_note(_con_register(), today=_as_of() + timedelta(days=30))
    assert _as_of().isoformat() in note, f"the as-of date is not rendered: {note!r}"
    assert "30 days" in note, f"the age is not rendered: {note!r}"


def test_the_age_disclosure_is_scoped_to_the_constructed_row():
    """THE POINT OF THE WORDING. Naming one row's date in a note covering 26 rows
    invites the inference that the other 25 are current. They are not: 4 proxy-backed
    rows share one measurement date in a config comment that nothing here checks, and
    18 authored rows carry no date at all.

    The fix for "1 of 26 rows is dated" is not dating everything — it is the note
    saying what it covers.
    """
    from src.location_actions import yield_assumption_note

    note = yield_assumption_note(_con_register(), today=_as_of() + timedelta(days=30))
    assert "speaks only for" in note, (
        f"the date is rendered unscoped, so it reads as covering the table: {note!r}"
    )
    assert "no date at all" in note, (
        "the note does not say the authored rows are undated"
    )
    assert "not checked here" in note, (
        "the note does not say the proxy rows' measurement date is unchecked"
    )


def test_a_fresh_construction_renders_no_review_prompt():
    """Fresh renders nothing extra — the staleness_note rule, and this function's own
    docstring: a disclosure that always warns teaches the reader to skip it.

    Anchored at EXACTLY the threshold, which must still count as fresh, mirroring
    test_staleness_note_at_threshold_is_still_fresh.
    """
    from src.location_actions import yield_assumption_note
    from src.location_config import YIELD_CONSTRUCTION_REVIEW_DAYS

    fresh = _as_of() + timedelta(days=YIELD_CONSTRUCTION_REVIEW_DAYS)
    note = yield_assumption_note(_con_register(), today=fresh)
    assert "annual review" not in note, (
        f"the review prompt fires at the threshold, which should still be fresh: {note!r}"
    )


def test_a_construction_past_its_review_cadence_prompts_a_review():
    from src.location_actions import yield_assumption_note
    from src.location_config import YIELD_CONSTRUCTION_REVIEW_DAYS

    stale = _as_of() + timedelta(days=YIELD_CONSTRUCTION_REVIEW_DAYS + 1)
    note = yield_assumption_note(_con_register(), today=stale)
    assert "annual review" in note, f"no review prompt past the cadence: {note!r}"
    assert "stamp a new" in note, "the prompt does not say what to do"


def test_the_review_prompt_disclaims_materiality():
    """The cadence exists so the entry gets LOOKED AT, not because drift is material —
    measured at #231: a full year is worth $0.07 typical, $0.62 worst-in-23-years,
    against $1.68 for the entire candidate range. A prompt reading as a risk warning
    would misrepresent its own measurement."""
    from src.location_actions import yield_assumption_note
    from src.location_config import YIELD_CONSTRUCTION_REVIEW_DAYS

    stale = _as_of() + timedelta(days=YIELD_CONSTRUCTION_REVIEW_DAYS + 400)
    note = yield_assumption_note(_con_register(), today=stale)
    assert "not a materiality warning" in note, (
        f"the prompt does not disclaim materiality: {note!r}"
    )


def test_the_note_does_not_borrow_the_refresh_cycle_remediation():
    """staleness_note tells the reader to run tools/refresh_market_data.py. That tool
    rewrites five committed CSVs and never touches FRED, so pointing this entry at it
    would be a false instruction."""
    from src.location_actions import yield_assumption_note
    from src.location_config import YIELD_CONSTRUCTION_REVIEW_DAYS

    stale = _as_of() + timedelta(days=YIELD_CONSTRUCTION_REVIEW_DAYS + 1)
    note = yield_assumption_note(_con_register(), today=stale)
    assert "refresh_market_data" not in note, (
        "the note points at a refresh tool that does not cover this data"
    )
    assert "refresh cycle" not in note, (
        "the note claims a refresh cycle; there is none for the FRED series"
    )


def _cadence_reasoning() -> str:
    """The contiguous comment block immediately above the constant — NOT a fixed slice.

    Both tests below asserted against ``src[i-2200:i]``, a window that reaches back past
    the constant's own comment and into SLEEVE_YIELD_CONSTRUCTION's ``note`` field. That
    note, written at #213, also contains "$1.68" — so the measurement assertion passed on
    text belonging to a DIFFERENT declaration, and a mutant deleting the figure from the
    cadence comment stayed green (N12). Same shape as a parser anchoring on the first
    mention of a name instead of its definition: the window has to be the artifact, not
    its neighbourhood.
    """
    import inspect

    import src.location_config as lc
    lines = inspect.getsource(lc).splitlines()
    i = next(n for n, line in enumerate(lines)
             if line.startswith("YIELD_CONSTRUCTION_REVIEW_DAYS"))
    block = []
    for line in reversed(lines[:i]):
        if not line.startswith("#"):
            break
        block.append(line)
    return chr(10).join(reversed(block))


def test_the_cadence_is_annual_and_distinguishes_itself_from_the_refresh_thresholds():
    """365, and the reasoning must say why it is not sized like 70/45. Those are sized
    so that firing means a refresh CYCLE was missed; there is no cycle here, so a
    borrowed number would import a justification that does not hold."""
    from src.location_config import YIELD_CONSTRUCTION_REVIEW_DAYS

    assert YIELD_CONSTRUCTION_REVIEW_DAYS == 365, (
        "the cadence VALUE is pinned here and nowhere else: the fresh/stale tests derive "
        "their dates FROM the constant, so they hold under any value and cannot detect a "
        "wrong one. They test the rule; this tests the number."
    )
    reasoning = _cadence_reasoning()
    assert "MARKET_DATA_STALE_DAYS" in reasoning, (
        "the constant does not distinguish itself from the refresh-cycle thresholds"
    )
    assert "review hygiene" in reasoning.lower(), (
        "the constant does not say it is review hygiene rather than a risk control"
    )
    assert "IRS" in reasoning or "verify annually" in reasoning, (
        "the annual cadence is not anchored to the IRS block it rides along with"
    )


def test_the_cadence_constant_records_the_measurement_that_justifies_it():
    """A cadence with no measurement behind it is a preference. The drift magnitudes and
    their dollar translation belong in the code, with their vintage, so the next reader
    can check the reasoning instead of re-deriving it."""
    reasoning = _cadence_reasoning()
    assert "bp" in reasoning, "no measured drift magnitudes"
    assert "1.68" in reasoning, (
        "the comparison that makes the drift trivial — the whole candidate range being "
        "worth $1.68 — is not recorded"
    )

def test_the_oldest_constructed_stamp_governs(monkeypatch):
    """With one constructed entry, min(stamps) and max(stamps) are the same value, so
    the "oldest governs" rule is unfalsifiable against the real config — a mutant
    swapping min for max would apply cleanly and change nothing. Two synthetic entries
    make it real.

    Oldest rather than newest because the note makes one claim for the whole category:
    reporting the freshest would let a stale entry hide behind a recent one.
    """
    import pandas as pd

    import src.location_config as lc
    from src.location_actions import yield_assumption_note

    old, new = date(2020, 1, 15), date(2026, 6, 30)
    monkeypatch.setattr(lc, "SLEEVE_YIELD_CONSTRUCTION", {
        "tips": {"formula": "a + b", "components": {"X": 0.01, "Y": 0.01},
                 "as_of": old.isoformat(), "note": "10-year broad-maturity DGS10 #231"},
        "core_fi_treasury": {"formula": "a + b", "components": {"X": 0.02, "Y": 0.02},
                             "as_of": new.isoformat(), "note": "n/a"},
    })
    reg = pd.DataFrame({
        "symbol":      ["SCHP", "VGIT"],
        "sleeve":      ["tips", "core_fi_treasury"],
        "yield_basis": ["table", "table"],
    })
    note = yield_assumption_note(reg, today=new + timedelta(days=5))

    assert old.isoformat() in note, f"the older stamp is not the one reported: {note!r}"
    assert new.isoformat() not in note, (
        "the newer stamp is reported, so a stale entry could hide behind a fresh one"
    )
    assert "The oldest is" in note, "plural phrasing did not switch"
