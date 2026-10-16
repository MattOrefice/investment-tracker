"""The Research page's fund rationales and the Correlations page's readings, as decided by
the owner on October 15, 2026 (#427).

  * VTV's and IWD's index methods are stated as the providers' documents state them,
    cited with their dates; the rationale listed P/CF among CRSP's value ratios and
    called Russell's method "the P/B-heavy single metric".
  * AVDV's "holds no South Korea" cites the dated filing the Factor Profile page reads.
  * The ER-savings note says what the code computes: SAA targets, each sleeve's split
    equally across its holdings.
  * interpret_correlations says "intra-equity" only of pairs whose sleeves are all
    equity, and the bond-equity paragraph states its two figures without a reading.

Items that no longer held at this tip are asserted here too: IEMG's comparison reads both
expense ratios from the book, VGIT's "78%" reads the equity targets, and IEMG's "largest
fee gap" is the book's. The seeded text reaches both committed books through
tools/migrate_markets_copy.py and tools/migrate_operations_copy.py, which
tests/test_markets_copy.py and tests/test_operations_copy.py hold to the seeds.
"""
from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from src import asset_evaluation as ae
from src import prose_figures
from src.factors import _NPORT_ASOF, interpret_correlations

ROOT = Path(__file__).resolve().parent.parent
BOOKS = [ROOT / "data" / "demo.db", ROOT / "tests" / "fixtures" / "frozen_book.db"]
CRSP = "(Morningstar Market Indexes Methodology Guide, July 2026)"
RUSSELL = ("(Russell US Equity Indexes Construction and Methodology, v7.2, August 2026, "
           "section 8.2.1)")


def _ro(book):
    return sqlite3.connect(f"file:{Path(book).as_posix()}?mode=ro", uri=True)


def _vtv_texts(book) -> "list[str]":
    con = _ro(book)
    try:
        fund = con.execute("SELECT holding_rationale FROM securities WHERE ticker = 'VTV'"
                           ).fetchone()[0]
        thesis = con.execute("SELECT macro_view, view_summary, vehicle_rationale FROM theses "
                             "WHERE thesis_id = 15").fetchone()
    finally:
        con.close()
    return [fund, *thesis]


# ── VTV and IWD ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("book", BOOKS, ids=lambda p: p.name)
def test_vtv_states_both_index_methods_with_their_documents(book):
    texts = _vtv_texts(book)
    assert len(texts) == 4
    for text in texts:
        assert ("scores value on five ratios: book-to-price, future and historical "
                f"earnings-to-price, dividend-to-price and sales-to-price {CRSP}") in text
        assert ("tracks the Russell 1000 Value, whose style score uses one value variable, "
                f"book-to-price, beside two growth variables {RUSSELL}") in text
        for gone in ("P/CF", "P/B-heavy", "single metric Russell", "Russell's P/B-heavy"):
            assert gone not in text, gone


# ── AVDV and Korea ────────────────────────────────────────────────────────────

def test_the_nport_token_is_the_factor_profiles_filing_date():
    assert prose_figures.render("ending {{nport}}.") == f"ending {_NPORT_ASOF}."


@pytest.mark.parametrize("book", BOOKS, ids=lambda p: p.name)
def test_avdvs_korea_line_cites_the_filing_by_its_date(book):
    con = _ro(book)
    try:
        text = con.execute("SELECT holding_rationale FROM securities WHERE ticker = 'AVDV'"
                           ).fetchone()[0]
    finally:
        con.close()
    rendered = prose_figures.render(text, targets={})
    assert (f"As of the Avantis N-PORT holdings disclosure for the period ending "
            f"{_NPORT_ASOF}, the filing the Factor Profile page reads, AVDV held no South "
            "Korea position") in rendered
    assert "verified at the position level" not in rendered and "{{" not in rendered


def test_the_factor_profile_reads_the_same_filing():
    """The page side of the citation: the Avantis paragraph names _NPORT_ASOF."""
    source = (ROOT / "src" / "factors.py").read_text(encoding="utf-8")
    assert ("Avantis N-PORT holdings disclosure for the period ending {_NPORT_ASOF}"
            in source)


# ── the ER-savings note ───────────────────────────────────────────────────────

def test_the_er_savings_note_says_target_weights_split_by_holding():
    tree = ast.parse((ROOT / "pages" / "8_Research.py").read_text(encoding="utf-8"))
    strings = " ".join(n.value for n in ast.walk(tree)
                       if isinstance(n, ast.Constant) and isinstance(n.value, str))
    assert strings.count("each holding's weight is its sleeve's SAA target split equally "
                         "across the sleeve's holdings") == 2
    for gone in ("reflect actual portfolio allocations", "reflect actual portfolio exposure",
                 "Σ(sleeve_weight"):
        assert gone not in strings, gone


def test_the_loop_the_note_describes_splits_the_target_by_holding():
    """The note's premise, read off the code: each pair's weight is the sleeve's
    target_weight over the sleeve's pair count."""
    source = (ROOT / "pages" / "8_Research.py").read_text(encoding="utf-8")
    assert 'w_per = d["sleeve"]["target_weight"] / n' in source
    assert 'portfolio_savings_bps += w_per * (bm_er - (h_er or 0.0)) * 10_000' in source


# ── correlations ──────────────────────────────────────────────────────────────

def _corr(names, values) -> pd.DataFrame:
    return pd.DataFrame(values, index=names, columns=names)


NAMES = ["US Large Core", "US Small Cap", "Core Fixed Income", "TIPS"]
EQUITY = {"US Large Core", "US Small Cap"}


def test_intra_equity_is_said_when_every_top_pair_is_equity():
    m = _corr(NAMES[:3] + ["International Core"],
              [[1.0, 0.90, 0.10, 0.85], [0.90, 1.0, 0.05, 0.80],
               [0.10, 0.05, 1.0, 0.02], [0.85, 0.80, 0.02, 1.0]])
    text = interpret_correlations(m, EQUITY | {"International Core"})
    assert text.startswith("High intra-equity co-movement means the equity sleeves")


def test_a_top_pair_with_a_bond_sleeve_is_not_called_intra_equity():
    m = _corr(NAMES, [[1.0, 0.90, 0.20, 0.10], [0.90, 1.0, 0.10, 0.05],
                      [0.20, 0.10, 1.0, 0.85], [0.10, 0.05, 0.85, 1.0]])
    text = interpret_correlations(m, EQUITY)
    assert text.startswith("The top pairs are US Large Core × US Small Cap (ρ = 0.90), "
                           "Core Fixed Income × TIPS (ρ = 0.85)"), text
    assert "intra-equity" not in text and "global market beta" not in text


def test_without_an_equity_set_nothing_is_called_equity():
    """The same all-equity top pairs as above: without the set, no claim; with it, the
    claim. The contrast shows the set, not the matrix, decides."""
    m = _corr(NAMES[:3] + ["International Core"],
              [[1.0, 0.90, 0.10, 0.85], [0.90, 1.0, 0.05, 0.80],
               [0.10, 0.05, 1.0, 0.02], [0.85, 0.80, 0.02, 1.0]])
    assert "intra-equity" not in interpret_correlations(m)
    assert "intra-equity" in interpret_correlations(m, EQUITY | {"International Core"})


@pytest.mark.parametrize("pre, reading", [(-0.35, True), (0.12, False), (-0.004, False),
                                          (0.0, False)])
def test_the_bond_equity_paragraph_cites_60_40_only_below_zero(pre, reading):
    text = ae.bond_equity_paragraph(pre, 0.56, 60)
    assert text.startswith(f"Core Fixed Income's average pairwise correlation with the "
                           f"equity sleeves was ρ ≈ {ae.fmt_corr(pre)} before 2020")
    assert ("consistent with the negative-correlation assumption" in text) is reading
    assert "The trailing 60-day estimate is ρ ≈ 0.56." in text
    for gone in ("shifted materially", "This is a regime", "-0.00"):
        assert gone not in text, gone


def test_the_correlations_page_passes_the_books_equity_sleeves():
    tree = ast.parse((ROOT / "pages" / "9_Correlations.py").read_text(encoding="utf-8"))
    calls = [ast.unparse(n) for n in ast.walk(tree) if isinstance(n, ast.Call)
             and getattr(n.func, "id", None) == "interpret_correlations"]
    assert calls == ["interpret_correlations(corr, set(ae.equity_sleeve_names()))"]
    para = [ast.unparse(n) for n in ast.walk(tree) if isinstance(n, ast.Call)
            and getattr(n.func, "attr", None) == "bond_equity_paragraph"]
    assert para == ["ae.bond_equity_paragraph(pre2020_be, avg_be, window)"]
    # Its caption pointed at Asset Evaluation's "post-2020 correlation shift", the
    # "central analytical finding" that page no longer reads into its averages (#425).
    strings = " ".join(n.value for n in ast.walk(tree)
                       if isinstance(n, ast.Constant) and isinstance(n.value, str))
    assert "central analytical finding" not in strings
    assert ("The same rolling-window methodology underlies the rolling BTC–SPY "
            "correlation on the Asset Evaluation page.") in strings


# ── items that no longer held, asserted ───────────────────────────────────────

def _gaps(book) -> "dict[str, float]":
    """Each held fund's expense-ratio gap to its sleeve's benchmark, where the benchmark
    is a single ticker the book prices a fee for."""
    con = _ro(book)
    try:
        er = dict(con.execute("SELECT ticker, expense_ratio FROM securities"))
        rows = con.execute(
            "SELECT s.ticker, s.expense_ratio, a.benchmark_ticker FROM securities s "
            "JOIN asset_classes a ON a.asset_class_id = s.asset_class_id "
            "WHERE s.security_type != 'benchmark'").fetchall()
    finally:
        con.close()
    return {t: er[bm] - e for t, e, bm in rows
            if e is not None and er.get(bm) is not None and t != bm}


@pytest.mark.parametrize("book", BOOKS, ids=lambda p: p.name)
def test_iemgs_fee_gap_is_the_largest_its_rationale_says_it_is(book):
    gaps = _gaps(book)
    assert len(gaps) >= 10, gaps                   # premise: the pairs were found
    assert max(gaps, key=gaps.get) == "IEMG", gaps
    con = _ro(book)
    try:
        text = con.execute("SELECT holding_rationale FROM securities WHERE ticker = 'IEMG'"
                           ).fetchone()[0]
    finally:
        con.close()
    assert "EEM costs {{er:EEM}} against IEMG's {{er:IEMG}}, the largest fee gap" in text
    assert "times" not in text


@pytest.mark.parametrize("book", BOOKS, ids=lambda p: p.name)
def test_the_growth_portfolios_weight_is_the_sum_of_the_equity_targets(book):
    """VGIT's and Core Fixed Income's "{{w:Equity}} growth portfolio" reads the Equity
    parent's target, which is the sum of its sleeves' targets on both books."""
    con = _ro(book)
    try:
        parent = con.execute("SELECT asset_class_id, target_weight FROM asset_classes "
                             "WHERE name = 'Equity' AND parent_id IS NULL").fetchone()
        children = con.execute("SELECT SUM(target_weight) FROM asset_classes "
                               "WHERE parent_id = ?", (parent[0],)).fetchone()[0]
        texts = [r[0] for r in con.execute(
            "SELECT holding_rationale FROM securities WHERE ticker = 'VGIT' UNION ALL "
            "SELECT rationale FROM asset_classes WHERE name = 'Core Fixed Income'")]
    finally:
        con.close()
    assert parent[1] == pytest.approx(children, abs=1e-12)
    for text in texts:
        assert "{{w:Equity}} growth portfolio" in text and "78%" not in text
