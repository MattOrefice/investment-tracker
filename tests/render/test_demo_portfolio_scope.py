"""The public demo presents the paper-trade portfolio, not the author's account.

Three pages showed portfolio data with no "Demo mode" banner (SAA, Capital Deployment,
Tax Lots), and six scope lines called the demo book "Personal Fidelity — the
self-directed taxable book", pointing at retirement accounts and a Household View the
demo does not have. The account's label came from demo.db (accounts.display_name);
tools/migrate_demo_account_display_name.py relabels it.

Pinned here: the banner on every page that reads portfolio data (statically, so a new
page cannot miss it), the demo scope wording on the six pages that had the personal
one, and, as the contrast, the personal wording still rendered in personal mode.
"""
from __future__ import annotations

import os
import re
import shutil
import sqlite3
from pathlib import Path

import pytest

import src.config as config
import src.db as db
import src.prices as prices

ROOT = Path(__file__).resolve().parent.parent.parent
DEMO_DB = ROOT / "data" / "demo.db"

# Pages the demo routes (app.py hides 13-15 in demo mode).
DEMO_PAGES = ["app.py"] + sorted(
    str(p.relative_to(ROOT)).replace("\\", "/") for p in (ROOT / "pages").glob("*.py")
    if int(p.name.split("_")[0]) <= 12)

# A page reads portfolio data if it imports a module that reads the ledger or the
# holdings, or queries the trades table.
_PORTFOLIO_SOURCE = re.compile(
    r"from src\.(holdings|tax_lots|performance|attribution|risk|trade_writer|rebalance|"
    r"harvest|reports) import|FROM trades")

PERSONAL_WORDING = ("Personal Fidelity", "self-directed", "Household View",
                    "externally-managed", "IRAs, workplace plans")
DEMO_SCOPE = "a simulated taxable account traded from May 2025"

SCOPE_PAGES = ["1_SAA.py", "2_Performance.py", "6_Benchmark_Attribution.py",
               "7_Risk.py", "11_Capital_Deployment.py", "12_Tax_Lots.py"]


def test_the_demo_account_is_labelled_as_the_paper_trade_portfolio():
    con = sqlite3.connect(f"file:{DEMO_DB.as_posix()}?mode=ro", uri=True)
    try:
        row = con.execute("SELECT display_name, name FROM accounts "
                          "WHERE pseudonym = 'acct_01'").fetchone()
    finally:
        con.close()
    assert row[0] == "Paper-trade portfolio"
    assert row[1] == "Personal Fidelity", "name is initialize_db's key and must not move"


def test_every_page_that_reads_portfolio_data_carries_the_demo_banner():
    readers = [p for p in DEMO_PAGES
               if _PORTFOLIO_SOURCE.search((ROOT / p).read_text(encoding="utf-8"))]
    assert {"pages/1_SAA.py", "pages/11_Capital_Deployment.py",
            "pages/12_Tax_Lots.py"} <= set(readers), "the scan must see the three it missed"
    missing = [p for p in readers
               if "st.info(get_demo_banner_text())" not in (ROOT / p).read_text(encoding="utf-8")]
    assert not missing, f"pages showing portfolio data with no demo banner: {missing}"


@pytest.fixture
def render(tmp_path, monkeypatch):
    import streamlit as st
    copy = tmp_path / "demo.db"
    shutil.copyfile(DEMO_DB, copy)
    os.chmod(copy, 0o644)
    monkeypatch.setattr(db, "DB_PATH", copy)
    monkeypatch.setattr(db, "_migrated_paths", set())
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    monkeypatch.setattr(prices._SESSION, "get",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))

    def _render(page, demo=True):
        from streamlit.testing.v1 import AppTest
        monkeypatch.setattr(config, "IS_DEMO", demo)
        st.cache_data.clear()
        at = AppTest.from_file(str(ROOT / "pages" / page), default_timeout=300).run()
        assert not at.exception, [str(e.value) for e in at.exception]
        return at

    yield _render
    st.cache_data.clear()


def _texts(at):
    out = []
    for kind in ("info", "caption", "markdown", "warning", "success", "subheader", "title"):
        out += [e.value for e in getattr(at, kind) if isinstance(e.value, str)]
    out += [m.label for m in at.metric]
    out += [str(s.value) for s in at.selectbox]
    for df in at.dataframe:
        out += [" ".join(map(str, df.value.astype(str).values.ravel()))]
    return out


@pytest.mark.parametrize("page", SCOPE_PAGES)
def test_the_demo_names_the_paper_trade_portfolio_and_nothing_personal(render, page):
    texts = _texts(render(page))
    assert any(t.startswith("**Demo mode**") for t in texts), "no demo banner"
    assert any(DEMO_SCOPE in t and "Paper-trade portfolio" in t for t in texts), (
        "no demo scope line")
    leaks = [(w, t[:120]) for t in texts for w in PERSONAL_WORDING if w in t]
    assert not leaks, leaks


def test_the_demo_labels_follow_the_account(render):
    perf = render("2_Performance.py")
    assert "Paper-trade portfolio value" in [m.label for m in perf.metric]
    cap = render("11_Capital_Deployment.py")
    assert [s.value for s in cap.selectbox if s.label == "Deploy into account"] == [
        "Paper-trade portfolio"]
    lots = render("12_Tax_Lots.py")
    table = next(df.value for df in lots.dataframe if "Account" in df.value.columns)
    assert set(table["Account"]) == {"Paper-trade portfolio"}


@pytest.mark.parametrize("page, personal", [
    ("1_SAA.py", "Retirement and externally-managed accounts are on the Household View."),
    ("2_Performance.py", "see the Household View for the whole household."),
    ("7_Risk.py", "are excluded (see Household View)."),
    ("12_Tax_Lots.py", "IRAs, workplace plans and HSAs are not shown"),
])
def test_personal_mode_keeps_its_own_scope_lines(render, page, personal):
    """The contrast: the wording moved only in demo mode."""
    texts = _texts(render(page, demo=False))
    assert any(personal in t for t in texts)
    assert not any(DEMO_SCOPE in t for t in texts)
