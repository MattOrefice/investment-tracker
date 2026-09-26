"""Research page accuracy (2026-09-25 audit, item 8), rendered on the demo book.

The audit found: a PDBC card calling it the only broad commodity ETF without a K-1 and
a C-corp, against DJP, an ETN that issues no K-1 either; IEMG called "identical EM
exposure" to EEM, when IEMG's index adds small caps; VNQ shown saving 73 bps against
DBC's expense ratio, while VNQ is itself the REIT benchmark and PDBC had no comparison;
SPAXX shown saving 14 bps against BIL with its 0.42% expense ratio read as zero; and
"13 sleeves" where every other page says 12. The fund facts are from PDBC's and IEMG's
prospectuses and SPAXX's prospectus dated 2026-06-26.
"""
from __future__ import annotations

import os
import re
import shutil
import sqlite3
from pathlib import Path

import pytest

import src.db as db
import src.prices as prices

ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.fixture(scope="module")
def research(tmp_path_factory):
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    import src.config as config
    mp = pytest.MonkeyPatch()
    copy = tmp_path_factory.mktemp("research") / "demo.db"
    shutil.copyfile(ROOT / "data" / "demo.db", copy)
    os.chmod(copy, 0o644)
    mp.setattr(db, "DB_PATH", copy)
    mp.setattr(db, "_migrated_paths", set())
    mp.setattr(db, "_RUNTIME_CACHE", None)
    mp.setattr(prices._SESSION, "get", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    mp.setattr(config, "IS_DEMO", True)
    st.cache_data.clear()
    at = AppTest.from_file(str(ROOT / "pages" / "8_Research.py"), default_timeout=600).run()
    assert not at.exception, [str(e.value) for e in at.exception]
    yield at
    st.cache_data.clear()
    mp.undo()


def _texts(at):
    return [str(e.value) for e in list(at.caption) + list(at.markdown)]


def _er(ticker):
    con = sqlite3.connect(f"file:{(ROOT / 'data' / 'demo.db').as_posix()}?mode=ro", uri=True)
    (er,) = con.execute("SELECT expense_ratio FROM securities WHERE ticker = ?", (ticker,)).fetchone()
    con.close()
    return er


def test_the_sleeve_count_keeps_cash_apart(research):
    head = next(t for t in _texts(research) if "holdings  ·" in t)
    assert head.startswith("12 sleeves plus cash  ·  "), head


def test_the_pdbc_card_states_what_its_prospectus_says(research):
    text = " ".join(_texts(research))
    card = next(t for t in _texts(research) if t.startswith("The index DBC tracks"))
    assert "regulated investment company" in card and "Form 1099 instead of DBC's K-1" in card
    assert f"at {_er('PDBC') * 100:.2f}% against {_er('DBC') * 100:.2f}%" in card, card
    assert "vs DBC" in text
    for wrong in ("C-corp", "DJP", "Only broad commodity ETF", "iPath"):
        assert wrong not in text, wrong


def test_iemg_is_not_called_identical_to_eem(research):
    # Every text about IEMG or EEM. (SCHP's rationale also says "identical exposure", of
    # TIP; that claim is not this item's.)
    about = [t for t in _texts(research) if "IEMG" in t or "EEM" in t]
    assert len(about) >= 3, about
    assert not [t for t in about if "identical" in t.lower() or "same-index" in t]
    card = next(t for t in _texts(research) if t.startswith("Broader EM exposure"))
    assert "MSCI Emerging Markets IMI adds the small caps" in card
    assert f"at {_er('IEMG') * 100:.2f}% against EEM's {_er('EEM') * 100:.2f}%" in card


def _savings_after(at, ticker):
    """The savings caption that follows a holding's comparison table."""
    seq = []

    def walk(node):
        t = getattr(node, "type", "")
        if t == "dataframe":
            seq.append(("df", node.value))
        elif t == "caption":
            seq.append(("cap", str(node.value)))
        for c in (node.children.values() if isinstance(getattr(node, "children", None), dict) else []):
            walk(c)
    walk(at._tree)
    for i, (kind, v) in enumerate(seq):
        if kind == "df" and "Selected Holding" in v.columns and v.loc["Ticker", "Selected Holding"] == ticker:
            nxt = next(c for k, c in seq[i + 1:] if k == "cap")
            return v, nxt
    raise AssertionError(f"no comparison table for {ticker}")


def test_vnq_is_its_own_benchmark_and_pdbc_is_compared_with_dbc(research):
    vnq, cap = _savings_after(research, "VNQ")
    assert vnq.loc["Ticker", "Benchmark"] == "VNQ" and cap == "· Holding matches benchmark", cap
    pdbc, cap = _savings_after(research, "PDBC")
    assert pdbc.loc["Ticker", "Benchmark"] == "DBC"
    assert pdbc.loc["Name", "Benchmark"] == "Invesco DB Commodity Index Tracking Fund"
    bps = round((_er("DBC") - _er("PDBC")) * 10_000)
    assert cap == f"· Holding saves {bps} bps annually vs. benchmark", cap


def test_spaxx_carries_its_expense_ratio(research):
    spaxx, cap = _savings_after(research, "SPAXX")
    assert spaxx.loc["Expense Ratio", "Selected Holding"] == "0.42%"
    bps = round((0.0042 - _er("BIL")) * 10_000)
    assert cap == f"· Holding costs {bps} bps more than benchmark", cap


def test_the_seed_source_and_both_committed_books_agree():
    """demo.db, the frozen book built from it, and src/seed_securities.py carry the same
    three corrected rows, so a reseed or a rebuild cannot bring the old text back."""
    from src.seed_securities import BENCHMARKS, HOLDINGS
    want = {("DBC", "name"): next(b["name"] for b in BENCHMARKS if b["ticker"] == "DBC")}
    for tk in ("IEMG", "PDBC"):
        want[(tk, "holding_rationale")] = next(h["holding_rationale"] for h in HOLDINGS
                                               if h["ticker"] == tk)
    for book in (ROOT / "data" / "demo.db", ROOT / "tests" / "fixtures" / "frozen_book.db"):
        con = sqlite3.connect(f"file:{book.as_posix()}?mode=ro", uri=True)
        for (tk, col), text in want.items():
            (got,) = con.execute(f"SELECT {col} FROM securities WHERE ticker = ?", (tk,)).fetchone()
            assert got == text, (book.name, tk, col)
        con.close()
    assert not re.search(r"C-corp|identical exposure|DJP \(the", " ".join(want.values()))
