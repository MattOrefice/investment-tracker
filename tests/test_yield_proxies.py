"""#210 PR 2 — every proxied sleeve entry declares its proxy and its candidates.

PR 1 gave a blend a look-through and refused where no basis exists. This gives the
thirteen proxy-backed equity sleeves entries whose basis is DECLARED: a benchmark
ticker in the cache, its trailing-twelve-month distribution yield, and the other
cached candidates for the same sleeve that it is compared against.

WHY THE SPREAD IS COMPUTED HERE (#520). A declared basis that hides its own
sensitivity is the #191 defect wearing a proxy ticker, so each entry's spread against
its candidates matters. The config's comments used to type each spread, each
candidate's yield and a "held cached" coverage figure. The spreads and yields were
re-typed at every re-measurement; the coverage figures came from a holdings file that
is not committed, and nothing could recompute them. The comments now give each proxy's
method and reason, and this file computes the spreads from the committed demo book
and prints them on request. Four sleeves have exactly ONE cached candidate, so their
spread is UNMEASURED rather than zero — and silence there would read as the most
certain when it is merely the least checked, which is why PROXY_SPREAD_UNMEASURED
exists as an explicit set.

BENCHMARK THROUGHOUT, including where a held ticker looks more apt. intl_large_value
takes EFV (a benchmark) and not AVIV (the sleeve's SAA carrier), because departing
from the framing on one row is held-weighted reasoning applied selectively — thirteen
entries meaning "benchmark" and one meaning "the ticker I plan to buy" is an
undeclared basis again.

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
    PROXY_WIDEST_SPREAD_PCT,
    SLEEVE_ASSUMED_YIELD,
    SLEEVE_PRIORITY_BY_ACCOUNT_TYPE,
    SLEEVE_YIELD_PROXY,
    SLEEVE_YIELD_PROXY_AS_OF,
    SLEEVE_YIELD_PROXY_CANDIDATES,
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


def test_every_proxied_sleeve_declares_its_candidates_BOTH_WAYS():
    """The candidates map is what the spreads are computed from, so it covers exactly
    the proxied sleeves, and a sleeve with none is exactly a sleeve whose spread is
    declared unmeasured. A candidate is never the proxy itself: that spread is zero by
    construction and would read as a measurement."""
    assert set(SLEEVE_YIELD_PROXY_CANDIDATES) == set(SLEEVE_YIELD_PROXY), (
        f"only candidates: {sorted(set(SLEEVE_YIELD_PROXY_CANDIDATES) - set(SLEEVE_YIELD_PROXY))}; "
        f"only proxies: {sorted(set(SLEEVE_YIELD_PROXY) - set(SLEEVE_YIELD_PROXY_CANDIDATES))}")
    without = {s for s, c in SLEEVE_YIELD_PROXY_CANDIDATES.items() if not c}
    assert without == set(PROXY_SPREAD_UNMEASURED), (
        "a sleeve with no candidate must be declared spread-unmeasured, and one with a "
        f"candidate must not: no candidates {sorted(without)}, "
        f"declared unmeasured {sorted(PROXY_SPREAD_UNMEASURED)}")
    for sleeve, candidates in SLEEVE_YIELD_PROXY_CANDIDATES.items():
        assert SLEEVE_YIELD_PROXY[sleeve] not in candidates, sleeve
        assert len(set(candidates)) == len(candidates), sleeve


# ── the comments give method and reason, and type no figure (#520) ───────────

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


BENCHMARK = "a benchmark for the sleeve"
ONLY_CACHED = "the only fund cached for the sleeve"


def test_each_proxy_entry_comment_gives_its_reason():
    """MIGRATED at #520 from "each comment states its spread". What that protected: an
    entry added with a bare ticker fails rather than merely looking thin in review. It
    still does. Each comment says why that fund stands for the sleeve: it is a
    benchmark for it, or it is the only fund the cache holds for it, and the second is
    said exactly where the spread is unmeasured."""
    comments = _entry_comments()
    missing = [s for s in EXPECTED_PROXIES if s not in comments]
    assert not missing, f"no inline comment on the proxy entry for: {missing}"

    for sleeve, comment in comments.items():
        if sleeve in PROXY_SPREAD_UNMEASURED:
            assert ONLY_CACHED in comment and BENCHMARK not in comment, (
                f"{sleeve} has one cached candidate; its comment must say so, "
                f"got: {comment!r}")
        else:
            assert BENCHMARK in comment and ONLY_CACHED not in comment, (
                f"{sleeve}'s comment gives no reason for its proxy: {comment!r}")


def _proxy_map_block() -> str:
    """The proxy map as the file writes it: its header comment, its entries and the
    candidates map."""
    text = CONFIG.read_text(encoding="utf-8")
    start = text.index("# Where each proxied entry above came from.")
    end = text.index("# The one date every proxied entry above is measured on")
    block = text[start:end]
    assert "SLEEVE_YIELD_PROXY: dict[str, str] = {" in block
    assert "SLEEVE_YIELD_PROXY_CANDIDATES: dict[str, tuple[str, ...]] = {" in block
    return block


def test_the_proxy_map_types_no_measured_figure():
    """MIGRATED at #520 from "each comment states its held coverage", which checked
    that a figure was PRESENT beside each entry and never what it was: 14 coverage
    percentages typed on 2026-08-11 from a holdings file that is not committed. A
    figure a reader needs is computed below, from the committed demo book; none is
    typed in the map. A percentage in the map's comments is a spread, a yield or a
    coverage figure coming back."""
    block = _proxy_map_block()
    typed = re.findall(r"[^\n]*\d(?:\.\d+)?\s?%[^\n]*", block)
    assert not typed, f"the proxy map types a percentage again: {typed}"
    assert "held cached" not in block and "% held" not in block
    for sleeve, comment in _entry_comments().items():
        assert not re.search(r"\bvs\b", comment), (
            f"{sleeve}'s comment compares against a candidate inline; candidates live "
            f"in SLEEVE_YIELD_PROXY_CANDIDATES: {comment!r}")


PAYMENTS = {"pays quarterly": 4,
            "pays semi-annually, so the TTM is 2 payments": 2,
            "pays monthly, so the TTM is 12 payments": 12}


def test_semiannual_proxies_say_so():
    """The five international proxies pay semi-annually, so a trailing-twelve-month
    figure is TWO payments. That is part of the sensitivity, not a footnote."""
    comments = _entry_comments()
    for sleeve in ("intl_developed", "emerging_markets", "intl_quality",
                   "intl_large_value", "intl_small_value"):
        assert "2 payments" in comments[sleeve], (
            f"{sleeve}'s proxy is semi-annual; its comment must say the TTM is two "
            f"payments, got: {comments[sleeve]!r}"
        )


def test_each_comments_payment_frequency_is_the_demo_books():
    """The method each comment states, checked: the number of distributions the demo
    book holds for the proxy in the year to the shared as-of is the number its comment
    says a trailing twelve months holds."""
    for sleeve, comment in _entry_comments().items():
        said = [n for phrase, n in PAYMENTS.items() if comment.endswith(phrase)]
        assert len(said) == 1, (
            f"{sleeve}'s comment does not end with how often its proxy pays: {comment!r}")
        held = _distributions_on(DEMO_BOOK, SLEEVE_YIELD_PROXY[sleeve])
        assert held == said[0], (
            f"{sleeve}: the comment says {said[0]} payments a year, the demo book holds "
            f"{held} for {SLEEVE_YIELD_PROXY[sleeve]} in the year to "
            f"{SLEEVE_YIELD_PROXY_AS_OF}")


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
    assert r["yield_basis"] == "proxy", (
        "a proxied sleeve must resolve as `proxy`, not the old storage-named `table` "
        "that an authored entry shared (#289)")
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

DEMO_BOOK = CONFIG.parent.parent / "data" / "demo.db"


def _ttm_on(book, ticker: str) -> float:
    """A ticker's trailing-twelve-month distribution yield at the shared as-of, read
    from ``book``: the distributions with an ex-date in the 365 days to the as-of, over
    the close DATED ON the as-of.

    THE CLOSE MUST BE THE AS-OF'S OWN (#513). This took "the latest close on or before
    the as-of". On a cache whose closes for IWB, IWF, XLK and XLV stopped on July 21 that
    was a three-week-old close: four entries had been measured over it, this check took
    the same close and agreed, and it went red only when the cache was refreshed. A book
    without the day's close now fails here instead of agreeing."""
    import sqlite3
    from datetime import date, timedelta

    as_of = date.fromisoformat(SLEEVE_YIELD_PROXY_AS_OF)
    w0 = (as_of - timedelta(days=365)).isoformat()
    conn = sqlite3.connect(f"file:{Path(book).as_posix()}?mode=ro", uri=True)
    try:
        px = conn.execute(
            "SELECT price_date, close FROM prices WHERE ticker = ? AND price_date <= ? "
            "ORDER BY price_date DESC LIMIT 1", (ticker, as_of.isoformat())).fetchone()
        assert px is not None and px[0] == as_of.isoformat(), (
            f"{ticker}: {Path(book).name} holds no close dated {as_of}; its latest on or "
            f"before it is {px[0] if px else 'none'}. A yield over an older close is not "
            "the yield at the as-of.")
        total, n = conn.execute(
            "SELECT COALESCE(SUM(amount), 0), COUNT(*) FROM dividends WHERE ticker = ? "
            "AND ex_date > ? AND ex_date <= ?", (ticker, w0, as_of.isoformat())).fetchone()
        assert n, f"{ticker}: {Path(book).name} holds no distribution in the year to {as_of}"
        return float(total) / float(px[1])
    finally:
        conn.close()


def _distributions_on(book, ticker: str) -> int:
    """How many distributions ``book`` holds for a ticker in the year to the as-of."""
    import sqlite3
    from datetime import date, timedelta

    as_of = date.fromisoformat(SLEEVE_YIELD_PROXY_AS_OF)
    w0 = (as_of - timedelta(days=365)).isoformat()
    conn = sqlite3.connect(f"file:{Path(book).as_posix()}?mode=ro", uri=True)
    try:
        return int(conn.execute(
            "SELECT COUNT(*) FROM dividends WHERE ticker = ? AND ex_date > ? "
            "AND ex_date <= ?", (ticker, w0, as_of.isoformat())).fetchone()[0])
    finally:
        conn.close()


def _check_entries(book) -> int:
    """Every proxied entry against its proxy's yield on ``book``; returns how many."""
    for sleeve, proxy in SLEEVE_YIELD_PROXY.items():
        recomputed = round(_ttm_on(book, proxy), 4)
        assert SLEEVE_ASSUMED_YIELD[sleeve] == pytest.approx(recomputed, abs=1e-9), (
            f"{sleeve}: entry {SLEEVE_ASSUMED_YIELD[sleeve]} does not match "
            f"{proxy}'s recomputed TTM {recomputed}"
        )
    return len(SLEEVE_YIELD_PROXY)


def test_entry_values_match_their_proxies_at_the_shared_as_of():
    """Each proxied value equals its proxy's TTM yield recomputed from the COMMITTED
    DEMO BOOK at the one shared as-of, never read back from the config. The demo book
    holds every proxy's closes and distributions, so this runs in CI; it read the
    personal cache, and skipped there (#513).

    WHAT THIS CHECKS, AND WHAT IT DOES NOT (#234). It verifies the value was
    TRANSCRIBED correctly from the measurement at the recorded date. It says nothing
    about whether that value is still CURRENT. The as-of is frozen, and deliberately
    so: a moving window would make this test's outcome depend on the day it runs.
    It was named "..._recomputed_from_the_cache", which read as a currency check.

    ONE AS-OF FOR THE WHOLE MAP (location_config.SLEEVE_YIELD_PROXY_AS_OF), and this is
    a CONSTRAINT ON THE MAP, not a convenience here. Every proxy entry is recomputed at
    this single date, so an entry carrying its OWN as-of does not merely go
    undocumented: it recomputes against the wrong day and fails, or worse, passes at a
    value nobody chose. #286 hit it. Its first draft gave core_fi_treasury a per-entry
    as-of of 2026-08-19 and wrote the value measured there; this test recomputed at the
    shared date and went red. The harness found the constraint that reasoning had not.

    SO IF YOU ARE HERE TO ADD PER-ENTRY DATING, the natural next request, since a
    shared date ages every entry at once: it is this that decides it. Moving to
    per-entry as-ofs means the date becomes part of each entry (a dict value, not a
    bare float) and this test resolves each at its own; a half-migration where the map
    claims per-entry dates and the recomputation keeps one is the state that passes
    while measuring the wrong thing."""
    assert SLEEVE_YIELD_PROXY_AS_OF == "2026-09-30"
    assert _check_entries(DEMO_BOOK) == len(EXPECTED_PROXIES) == 14


def _demo_copy(tmp_path, *statements):
    """A scratch copy of the demo book with ``statements`` (sql, args) run on it."""
    import os
    import shutil
    import sqlite3
    copy = tmp_path / "demo_copy.db"
    shutil.copyfile(DEMO_BOOK, copy)
    os.chmod(copy, 0o644)
    con = sqlite3.connect(copy)
    try:
        with con:
            for sql, args in statements:
                assert con.execute(sql, args).rowcount >= 1, (sql, args)
    finally:
        con.close()
    return copy


def test_a_book_whose_closes_stop_short_of_the_as_of_fails_the_check(tmp_path):
    """#513's cache, rebuilt on a copy: IWB's closes after July 21 gone. The old check
    divided by the July 21 close and compared; this one refuses the book."""
    short = _demo_copy(tmp_path, ("DELETE FROM prices WHERE ticker = 'IWB' AND "
                                  "price_date > ?", ("2026-07-21",)))
    with pytest.raises(AssertionError, match="IWB: demo_copy.db holds no close dated "
                                             "2026-09-30; its latest on or before it is "
                                             "2026-07-21"):
        _check_entries(short)


def test_a_book_without_the_as_ofs_own_close_fails_even_one_session_short(tmp_path):
    one_short = _demo_copy(tmp_path, ("DELETE FROM prices WHERE ticker = 'IEF' AND "
                                      "price_date = ?", ("2026-09-30",)))
    with pytest.raises(AssertionError, match="IEF: demo_copy.db holds no close dated "
                                             "2026-09-30; its latest on or before it is "
                                             "2026-09-29"):
        _check_entries(one_short)


def test_a_distribution_the_book_loses_moves_the_recomputed_yield(tmp_path):
    """The other input. With IWB's September distribution gone the recomputation no
    longer equals the entry, so the check reads dividends and not only closes."""
    fewer = _demo_copy(tmp_path, ("DELETE FROM dividends WHERE ticker = 'IWB' AND "
                                  "ex_date = ?", ("2026-09-15",)))
    with pytest.raises(AssertionError, match="us_large_core: entry 0.0092 does not match"):
        _check_entries(fewer)


def _measured_spreads(book=None) -> "dict[str, tuple[float, float, dict[str, float]]]":
    """{sleeve: (spread, the proxy's yield, {candidate: its yield})} for every entry
    with a candidate, computed from ``book`` (the committed demo book) at the shared
    as-of. Nothing here is read from a comment. The spread is the widest ratio between
    the proxy's yield and a candidate's, larger over smaller, minus one."""
    book = book or DEMO_BOOK
    out = {}
    for sleeve, candidates in SLEEVE_YIELD_PROXY_CANDIDATES.items():
        if not candidates:
            continue
        proxy = _ttm_on(book, SLEEVE_YIELD_PROXY[sleeve])
        yields = {ticker: _ttm_on(book, ticker) for ticker in candidates}
        widest = max(max(proxy, y) / min(proxy, y) - 1 for y in yields.values())
        out[sleeve] = (widest, proxy, yields)
    return out


def test_each_spread_is_computed_from_the_demo_book():
    """MIGRATED at #520 from "each stated spread and candidate yield is the demo
    book's", which compared figures typed in the comments with the book. Nothing is
    typed now, so this is the computation itself: every candidate has a close dated on
    the as-of and a distribution in the year to it (``_ttm_on`` refuses otherwise), so
    every declared spread is measurable, and each one is a positive size."""
    spreads = _measured_spreads()
    assert set(spreads) == set(SLEEVE_YIELD_PROXY) - PROXY_SPREAD_UNMEASURED
    assert len(spreads) == 10
    for sleeve, (spread, proxy, yields) in spreads.items():
        assert proxy > 0 and all(y > 0 for y in yields.values()), sleeve
        assert spread > 0, f"{sleeve}: a proxy and its candidates cannot yield the same"


def test_a_spread_moves_with_the_book_it_is_computed_from(tmp_path):
    """The contrast: with AVIV's latest distribution gone from a copy, the spread
    between EFV and AVIV is wider there than on the committed book. So the figure is
    read from the book's rows and not from anything typed."""
    committed = _measured_spreads()["intl_large_value"][0]
    fewer = _demo_copy(tmp_path, (
        "DELETE FROM dividends WHERE ticker = 'AVIV' AND ex_date = (SELECT MAX(ex_date) "
        "FROM dividends WHERE ticker = 'AVIV' AND ex_date <= ?)",
        (SLEEVE_YIELD_PROXY_AS_OF,)))
    assert _measured_spreads(fewer)["intl_large_value"][0] > committed


def test_the_widest_spread_the_note_prints_is_the_widest_measured():
    """The Asset Location note names EFV and AVIV and prints how far apart they are.
    The figure is typed once (PROXY_WIDEST_SPREAD_PCT), because the page prints it; it
    must be the largest spread the demo book gives, and that entry must still be EFV
    against AVIV."""
    spreads = _measured_spreads()
    sleeve = max(spreads, key=lambda s: spreads[s][0])
    assert (sleeve, SLEEVE_YIELD_PROXY[sleeve], list(spreads[sleeve][2])) == (
        "intl_large_value", "EFV", ["AVIV"])
    assert round(spreads[sleeve][0] * 100) == PROXY_WIDEST_SPREAD_PCT == 62


def _spread_table() -> "list[str]":
    """One line per proxied sleeve: the proxy and its yield, each candidate and its
    yield, and the spread. The figures the config's comments used to type."""
    spreads = _measured_spreads()
    lines = []
    for sleeve, proxy in SLEEVE_YIELD_PROXY.items():
        if sleeve in spreads:
            spread, own, yields = spreads[sleeve]
            against = ", ".join(f"{t} {y * 100:.2f}%" for t, y in yields.items())
            lines.append(f"{sleeve:<22}{proxy:<5} {own * 100:5.2f}%  +{spread * 100:.0f}% "
                         f"vs {against}")
        else:
            own = _ttm_on(DEMO_BOOK, proxy)
            lines.append(f"{sleeve:<22}{proxy:<5} {own * 100:5.2f}%  spread unmeasured: "
                         "no other candidate is cached")
    return lines


def test_the_spreads_and_candidate_yields_are_printed_for_a_reader():
    """What a reader of the map needs and the map no longer types. Run with ``-s`` to
    see it: ``python -m pytest tests/test_yield_proxies.py -k printed -s``."""
    lines = _spread_table()
    assert len(lines) == len(SLEEVE_YIELD_PROXY) == 14
    assert sum("spread unmeasured" in line for line in lines) == len(PROXY_SPREAD_UNMEASURED)
    widest = [line for line in lines if f"+{PROXY_WIDEST_SPREAD_PCT}% vs AVIV" in line]
    assert len(widest) == 1 and widest[0].startswith("intl_large_value")
    print(f"\nProxy yields and spreads at {SLEEVE_YIELD_PROXY_AS_OF}, from {DEMO_BOOK.name}:")
    print("\n".join(lines))


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
    assert reg["yield_basis"].isin(("proxy", "constructed", "authored")).sum() == 2, (
        "precondition: two rows resolved from SLEEVE_ASSUMED_YIELD")
    note = yield_assumption_note(reg)

    assert "declared basis" in note
    assert "1 of 2 rows** use a yield with a **declared basis" in note, (
        "the proxied row's declared basis is not reported"
    )
    # MIGRATED at #289, and the old phrase is now FALSE rather than merely reworded.
    # It read "an **authored** yield with **no declared basis** at all" — true when
    # authored meant undeclared. SLEEVE_YIELD_AUTHORED makes an authored entry a
    # DECLARED basis that happens not to be a measured one, so the note says what kind
    # of claim it is instead of asserting an absence that no longer holds.
    assert "no declared basis** at all" not in note, (
        "the note still claims authored rows declare nothing; SLEEVE_YIELD_AUTHORED "
        "declares them (#289)")
    assert "1 of 2 rows** use an **authored** yield" in note, (
        "the authored row is not distinguished from the proxied one"
    )


def test_note_reports_the_spread_and_that_some_are_unmeasured():
    """A declared basis that hides its own sensitivity is the #191 defect wearing a
    proxy ticker, so the note carries the widest spread and the unmeasured count."""
    from src.location_actions import yield_assumption_note
    note = yield_assumption_note(_note_register())
    assert f"up to **+{PROXY_WIDEST_SPREAD_PCT}%** on one sleeve" in note, (
        "the widest spread is not disclosed")
    assert f"those two differ by {PROXY_WIDEST_SPREAD_PCT}%." in note
    assert "85%" not in note, "the note still prints the first measurement's spread"
    # #520: an entry records its candidates; its spread is computed, not recorded.
    assert ("recorded per entry with the other candidates in the price cache it is "
            "compared against. The spreads between them are wide") in note
    assert "recorded per entry with its spread" not in note
    assert "unmeasured rather than zero" in note
    assert str(len(PROXY_SPREAD_UNMEASURED)) in note
