"""#210 PR 2 — every equity sleeve entry declares its proxy, its spread, and its coverage.

PR 1 gave a blend a look-through and refused where no basis exists. This gives the
thirteen proxy-backed equity sleeves entries whose basis is DECLARED: a benchmark
ticker in the cache, its trailing-twelve-month distribution yield, the spread against
every other cached candidate for the same sleeve, and how much of the sleeve's held
value that proxy actually represents.

WHY THE SPREAD IS PART OF THE ENTRY. A declared basis that hides its own sensitivity
is the #191 defect wearing a proxy ticker. Measured, not asserted: EFV 4.48% vs AVIV
2.42% is +85% on intl_large_value; IWD vs VTV is +34%; EEM vs IEMG is +32%. Four
sleeves have exactly ONE cached candidate, so their spread is UNMEASURED rather than
zero — and silence there would read as the most certain when it is merely the least
checked, which is why PROXY_SPREAD_UNMEASURED exists as an explicit set.

BENCHMARK THROUGHOUT, including where a held ticker looks more apt. intl_large_value
takes EFV (a benchmark, 0% held coverage) and not AVIV (the sleeve's SAA carrier),
because departing from the framing on one row is held-weighted reasoning applied
selectively — thirteen entries meaning "benchmark" and one meaning "the ticker I plan
to buy" is an undeclared basis again.

intl_all_exus gets NO entry. The only all-ex-US candidate in the cache is EFA, which
is developed-only and excludes emerging markets — wrong in KIND for that sleeve, the
same error declined for multi_asset. It joins NOT_MODELLED_SLEEVES; the structurally
right fix is a fund_compositions row set for IXUS/VXUS, filed separately.

Fixtures use frozen_tod_income's authored symbols and there are NO conditional skips:
a fixture whose subject no consumer claims produces skips that read as passes.
"""
import re
from pathlib import Path

import pandas as pd
import pytest

from src.household import build_location_register
from src.location_config import (
    ACCOUNT_SHELTER_PRIORITY,
    NOT_MODELLED_SLEEVES,
    PROXY_SPREAD_UNMEASURED,
    SLEEVE_ASSUMED_YIELD,
    SLEEVE_PRIORITY_BY_ACCOUNT_TYPE,
    SLEEVE_YIELD_PROXY,
    TAX_PROFILE,
)

CONFIG = Path(__file__).resolve().parent.parent / "src" / "location_config.py"

# The thirteen sleeves PR 2 gives entries, with the proxy each declares.
EXPECTED_PROXIES = {
    "us_large_core":        "IWB",
    "us_large_growth":      "IWF",
    "us_large_value":       "IWD",
    "us_large_quality":     "QUAL",
    "us_small_core":        "IWM",
    "us_small_value":       "AVUV",
    "us_sector_tech":       "XLK",
    "us_sector_healthcare": "XLV",
    "intl_developed":       "EFA",
    "emerging_markets":     "EEM",
    "intl_quality":         "IQLT",
    "intl_large_value":     "EFV",
    "intl_small_value":     "SCZ",
    # #286. Fixed income, so the sleeve names below that say "equity" are now wider
    # than they read — corrected where they are assertions, kept where they are history.
    "core_fi_treasury":      "IEF",
}


# ── the entries exist and declare a proxy ────────────────────────────────────

def test_the_test_enumeration_and_the_real_map_agree_BOTH_WAYS():
    """A GAP #286 WALKED INTO. EXPECTED_PROXIES is a test-side duplicate of the config
    map, and every assertion below iterates the DUPLICATE — so a sleeve added to the
    real map was invisible here: 13 pinned, a 14th unchecked, and nothing red.

    One-directional coverage of a mirrored enumeration is the assert-collections-are-
    complete shape with the collection split across two files. Both directions now.
    """
    assert set(EXPECTED_PROXIES) == set(SLEEVE_YIELD_PROXY), (
        f"only in the test: {sorted(set(EXPECTED_PROXIES) - set(SLEEVE_YIELD_PROXY))}; "
        f"only in the config: {sorted(set(SLEEVE_YIELD_PROXY) - set(EXPECTED_PROXIES))}"
    )


def test_every_proxied_sleeve_has_an_entry():
    for sleeve in EXPECTED_PROXIES:
        assert sleeve in SLEEVE_ASSUMED_YIELD, f"{sleeve} has no yield entry"


def test_every_entry_declares_a_proxy():
    """SLEEVE_YIELD_PROXY must cover every sleeve whose value came from a proxy — a
    value without a declared basis is exactly what #191 closed."""
    for sleeve in EXPECTED_PROXIES:
        assert sleeve in SLEEVE_YIELD_PROXY, f"{sleeve} has a value but no declared proxy"
        assert SLEEVE_YIELD_PROXY[sleeve] == EXPECTED_PROXIES[sleeve]


def test_proxy_map_claims_nothing_it_cannot_back():
    """Every proxied sleeve must have a value, and no proxy may be declared for a
    sleeve that resolves some other way (a blend, or a refusal)."""
    from src.location_config import BLEND_SLEEVES
    for sleeve in SLEEVE_YIELD_PROXY:
        assert sleeve in SLEEVE_ASSUMED_YIELD, (
            f"{sleeve} declares a proxy but has no entry to back"
        )
        assert sleeve not in BLEND_SLEEVES and sleeve not in NOT_MODELLED_SLEEVES, (
            f"{sleeve} declares a proxy but does not resolve through the table"
        )


def test_single_candidate_sleeves_declare_their_spread_unmeasured():
    """Four sleeves have exactly one cached candidate. Their spread is UNKNOWN, not
    zero, and the set says so rather than leaving silence to imply certainty."""
    assert PROXY_SPREAD_UNMEASURED == frozenset({
        "us_small_core", "us_small_value", "us_sector_tech", "us_sector_healthcare"})
    for sleeve in PROXY_SPREAD_UNMEASURED:
        assert sleeve in SLEEVE_YIELD_PROXY, (
            f"{sleeve} is declared spread-unmeasured but has no proxy"
        )


def test_spread_unmeasured_and_measured_sets_partition_the_proxied_sleeves():
    """No sleeve may be in both, and none may be in neither — otherwise a reader
    cannot tell which case they are looking at."""
    assert PROXY_SPREAD_UNMEASURED <= set(SLEEVE_YIELD_PROXY)
    measured = set(SLEEVE_YIELD_PROXY) - PROXY_SPREAD_UNMEASURED
    assert measured, "no sleeve has a measured spread; the partition is degenerate"
    assert measured | PROXY_SPREAD_UNMEASURED == set(SLEEVE_YIELD_PROXY)


# ── the comment block carries the sensitivity, not just the ticker ───────────

def _entry_comments():
    """Map sleeve -> the comment text attached to its SLEEVE_YIELD_PROXY entry.

    Anchored on the ASSIGNMENT, not on the first mention of the name: the first
    occurrence in the file is inside a comment in SLEEVE_ASSUMED_YIELD, so anchoring
    on the bare name parsed that dict's tail instead and returned a PARTIAL map —
    a helper silently finding the wrong thing, which the completeness assertion below
    now catches instead of leaving to a confusing KeyError downstream.
    """
    text = CONFIG.read_text(encoding="utf-8")
    marker = "SLEEVE_YIELD_PROXY: dict[str, str] = {"
    assert marker in text, "the proxy map's assignment is not where this parser expects"
    block = text[text.index(marker):]
    block = block[:block.index("\n}\n") + 3]
    out = {}
    for line in block.splitlines():
        m = re.match(r'\s*"(\w+)":\s*"(\w+)",\s*#\s*(.+)$', line)
        if m:
            out[m.group(1)] = m.group(3)
    assert set(out) == set(SLEEVE_YIELD_PROXY), (
        "the comment parser did not recover every proxy entry — parsed "
        f"{sorted(out)}, map has {sorted(SLEEVE_YIELD_PROXY)}. A partial parse makes "
        "every assertion below vacuous for the entries it missed."
    )
    return out


def test_each_proxy_entry_comment_states_its_spread():
    """The requirement in words: each entry carries its proxy AND its spread. A
    parsed assertion, so a value added without one fails rather than merely looking
    thin in review."""
    comments = _entry_comments()
    missing = [s for s in EXPECTED_PROXIES if s not in comments]
    assert not missing, f"no inline comment on the proxy entry for: {missing}"

    for sleeve, comment in comments.items():
        if sleeve in PROXY_SPREAD_UNMEASURED:
            assert "unmeasured" in comment.lower(), (
                f"{sleeve} has one cached candidate; its comment must say the spread "
                f"is unmeasured, got: {comment!r}"
            )
        else:
            assert re.search(r"[+-]\d+%", comment), (
                f"{sleeve}'s comment states no spread: {comment!r}"
            )


def test_each_proxy_entry_comment_states_its_held_coverage():
    """How much of the sleeve the proxy represents. emerging_markets is the case that
    drove this: IEMG covers 4.7% of the sleeve by value, and EEM 0%."""
    for sleeve, comment in _entry_comments().items():
        assert re.search(r"\d+(\.\d+)?% held", comment), (
            f"{sleeve}'s comment states no held coverage: {comment!r}"
        )


def test_semiannual_proxies_say_so():
    """The six international proxies pay semi-annually, so a trailing-twelve-month
    figure is TWO payments. That is part of the sensitivity, not a footnote."""
    comments = _entry_comments()
    for sleeve in ("intl_developed", "emerging_markets", "intl_quality",
                   "intl_large_value", "intl_small_value"):
        assert "2 payments" in comments[sleeve], (
            f"{sleeve}'s proxy is semi-annual; its comment must say the TTM is two "
            f"payments, got: {comments[sleeve]!r}"
        )


# ── intl_all_exus refuses rather than borrowing a developed-only proxy ──────

def test_intl_all_exus_is_not_modelled_and_has_no_proxy():
    assert "intl_all_exus" in NOT_MODELLED_SLEEVES
    assert "intl_all_exus" not in SLEEVE_ASSUMED_YIELD
    assert "intl_all_exus" not in SLEEVE_YIELD_PROXY, (
        "EFA is developed-only; declaring it the all-ex-US proxy would be wrong in "
        "kind, the same error declined for multi_asset"
    )


# ── the register on a fixture built from claimed symbols ────────────────────

VALUE = 10_000.0
# The rate a row's income is taxed at is DERIVED from its declared tax character
# (#278), not assumed to be the combined ordinary rate. This constant used to be
# `federal_marginal + state_marginal` and was right only while every sleeve got that
# rate — us_large_core's income is qualified dividends, so hardcoding ordinary here
# would make this test assert the defect #278 removed.
def _rate_for(sleeve: str) -> float:
    from src.household import _tax_character, income_rate
    return income_rate(_tax_character(sleeve, ""), TAX_PROFILE)

COMPLETE_MIX = [("us_large_core", 0.60), ("emerging_markets", 0.40)]


def _fixture():
    """frozen_tod_income's authored symbols and account, so every row is claimed by a
    real group — an invented ticker yields skips that read as passes."""
    acct = pd.DataFrame([
        {"pseudonym": "acct_tod", "display_name": "Individual Taxable (TOD)",
         "tax_treatment": "taxable"},
    ])
    specs = [("JHEQX", "us_large_core"), ("GAOSX", "multi_asset"),
             ("JCPB", "intl_all_exus"), ("BILPX", "emerging_markets")]
    sec = pd.DataFrame([
        {"ticker": t, "name": f"{t} fund", "tax_efficiency": "medium",
         "sleeve_category": s} for t, s in specs
    ])
    pos = pd.DataFrame([
        {"pseudonym": "acct_tod", "symbol": t, "current_value": VALUE,
         "total_gain_loss": 500.0, "cost_basis_total": 9_500.0} for t, _ in specs
    ])
    comps = pd.DataFrame([
        {"fund_symbol": "GAOSX", "underlying_sleeve": s, "weight": w,
         "as_of_date": "2026-08-01", "source": "test"} for s, w in COMPLETE_MIX
    ])
    return pos, acct, sec, comps


def _register():
    pos, acct, sec, comps = _fixture()
    return build_location_register(
        pos, acct, sec, TAX_PROFILE, SLEEVE_PRIORITY_BY_ACCOUNT_TYPE,
        ACCOUNT_SHELTER_PRIORITY, compositions_df=comps)


def _row(reg, symbol):
    m = reg[reg["symbol"] == symbol]
    assert len(m) == 1, f"expected one {symbol} row, got {len(m)}"
    return m.iloc[0]


def test_a_proxied_sleeve_now_resolves_from_the_table():
    r = _row(_register(), "JHEQX")
    assert r["yield_basis"] == "table"
    assert r["assumed_yield"] == pytest.approx(SLEEVE_ASSUMED_YIELD["us_large_core"])
    assert float(r["annual_benefit"]) == pytest.approx(
        round(VALUE * SLEEVE_ASSUMED_YIELD["us_large_core"]
              * _rate_for("us_large_core"), 2))


def test_a_blend_over_proxied_sleeves_is_now_a_COMPLETE_look_through():
    """The self-retiring marker retires. Both of this composition's underlying sleeves
    have entries after PR 2, so the basis is look_through, not look_through_partial —
    right-hand side computed from the composition by hand."""
    expected = sum(SLEEVE_ASSUMED_YIELD[s] * w for s, w in COMPLETE_MIX)
    r = _row(_register(), "GAOSX")
    assert r["yield_basis"] == "look_through", (
        "an all-entered composition must not report as partial"
    )
    assert r["assumed_yield"] == pytest.approx(expected)


def test_intl_all_exus_row_refuses():
    r = _row(_register(), "JCPB")
    assert r["yield_basis"] == "not_modelled"
    assert pd.isna(r["annual_benefit"])


def test_no_row_falls_to_the_default_on_this_fixture():
    """PR 3 makes an unlisted sleeve raise, which is only safe once nothing legitimate
    resolves through the default. This is that precondition, asserted."""
    reg = _register()
    assert not reg["yield_basis"].eq("default").any(), (
        f"rows still on the default: "
        f"{sorted(reg.loc[reg['yield_basis'].eq('default'), 'sleeve'])}"
    )


# ── the values are the proxies', recomputed independently ───────────────────

def test_entry_values_match_their_proxies_recomputed_from_the_cache():
    """Independent right-hand side: the proxy's TTM is recomputed here from the
    committed dividends/prices tables, never read back from the config. Skips only if
    the personal cache is absent (CI), and asserts a non-empty comparison otherwise so
    an empty run cannot pass."""
    import sqlite3
    from datetime import date, timedelta

    db = Path(__file__).resolve().parent.parent / "data" / "tracker.db"
    if not db.exists() or db.stat().st_size == 0:
        pytest.skip("personal cache absent")

    as_of = date(2026, 8, 11)
    w0 = (as_of - timedelta(days=365)).isoformat()
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    checked = 0
    try:
        for sleeve, proxy in SLEEVE_YIELD_PROXY.items():
            px = conn.execute(
                "SELECT close FROM prices WHERE ticker=? AND price_date<=? AND "
                "close IS NOT NULL ORDER BY price_date DESC LIMIT 1",
                (proxy, as_of.isoformat())).fetchone()
            div = conn.execute(
                "SELECT COALESCE(SUM(amount),0) FROM dividends WHERE ticker=? AND "
                "ex_date>? AND ex_date<=?", (proxy, w0, as_of.isoformat())).fetchone()
            if px is None or not div[0]:
                continue
            recomputed = round(float(div[0]) / float(px[0]), 4)
            assert SLEEVE_ASSUMED_YIELD[sleeve] == pytest.approx(recomputed, abs=1e-4), (
                f"{sleeve}: entry {SLEEVE_ASSUMED_YIELD[sleeve]} does not match "
                f"{proxy}'s recomputed TTM {recomputed}"
            )
            checked += 1
    finally:
        conn.close()
    assert checked >= 10, (
        f"only {checked} entries were checkable against the cache; an almost-empty "
        "comparison passes without proving anything"
    )


# ── the disclosure must not keep claiming "no declared basis" for all of them ──

def _note_register():
    """A register with BOTH a proxied table row and an authored one, so the note has
    to distinguish them rather than making one blanket claim."""
    acct = pd.DataFrame([
        {"pseudonym": "acct_tod", "display_name": "Individual Taxable (TOD)",
         "tax_treatment": "taxable"},
    ])
    specs = [("JHEQX", "us_large_core"),      # proxied (IWB)
             ("BILPX", "multi_sector_fi")]    # authored, no proxy
    sec = pd.DataFrame([
        {"ticker": t, "name": f"{t} fund", "tax_efficiency": "medium",
         "sleeve_category": s} for t, s in specs
    ])
    pos = pd.DataFrame([
        {"pseudonym": "acct_tod", "symbol": t, "current_value": VALUE,
         "total_gain_loss": 500.0, "cost_basis_total": 9_500.0} for t, _ in specs
    ])
    return build_location_register(
        pos, acct, sec, TAX_PROFILE, SLEEVE_PRIORITY_BY_ACCOUNT_TYPE,
        ACCOUNT_SHELTER_PRIORITY, compositions_df=None)


def test_note_no_longer_claims_no_declared_basis_for_every_row():
    """INVERTED from #191. That PR's whole point was "the yield carries no declared
    basis", and it was true of all fifteen entries. It is now false for the thirteen
    proxied ones, so the blanket claim would UNDERSTATE what is known — the same
    defect running the other way. The note must split the two populations.
    """
    from src.location_actions import yield_assumption_note
    reg = _note_register()
    assert reg["yield_basis"].eq("table").sum() == 2, "precondition: two table rows"
    note = yield_assumption_note(reg)

    assert "declared basis" in note
    assert "1 of 2 rows** use a yield with a **declared basis" in note, (
        "the proxied row's declared basis is not reported"
    )
    assert "1 of 2 rows** use an **authored** yield with **no declared basis" in note, (
        "the authored row is not distinguished from the proxied one"
    )


def test_note_reports_the_spread_and_that_some_are_unmeasured():
    """A declared basis that hides its own sensitivity is the #191 defect wearing a
    proxy ticker, so the note carries the widest spread and the unmeasured count."""
    from src.location_actions import yield_assumption_note
    note = yield_assumption_note(_note_register())
    assert "+85%" in note, "the widest spread is not disclosed"
    assert "unmeasured rather than zero" in note
    assert str(len(PROXY_SPREAD_UNMEASURED)) in note
