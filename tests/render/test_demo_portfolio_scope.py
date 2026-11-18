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

import ast
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

# Every page file and the router. Decided by the owner on October 19, 2026 (#673): the
# scan reads every file in pages/, not a numbered range. The range (12 and below, since
# app.py hides 13-15 in demo mode) also dropped page 16, which the demo serves.
DEMO_PAGES = ["app.py"] + sorted(
    str(p.relative_to(ROOT)).replace("\\", "/") for p in (ROOT / "pages").glob("*.py"))

# Pages exempt from the banner by name, each with the owner's reason (#673). Every
# reason is one a test below checks, and each exempt page is held to its own.
NO_LEDGER = "it reads no portfolio ledger"
# Decided by the owner on November 11, 2026 (#711). Page 15 names the tracked portfolio
# account so its sell plan can leave that account out, which takes an import from
# src.holdings, and the scan reads every such page as showing portfolio data. In demo
# mode the page shows none: the render test below holds it to that.
RENDERS_NOTHING_IN_DEMO = ("it renders nothing in demo mode: it stops before any import, "
                           "and app.py hides it.")
BANNER_EXEMPT = {
    "pages/15_Liquidity.py": RENDERS_NOTHING_IN_DEMO,
    "pages/16_Policy_Backtest.py": NO_LEDGER,
}

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
    readers = [p for p in DEMO_PAGES if p not in BANNER_EXEMPT
               and _PORTFOLIO_SOURCE.search((ROOT / p).read_text(encoding="utf-8"))]
    assert {"pages/1_SAA.py", "pages/11_Capital_Deployment.py",
            "pages/12_Tax_Lots.py"} <= set(readers), "the scan must see the three it missed"
    missing = [p for p in readers
               if "st.info(get_demo_banner_text())" not in (ROOT / p).read_text(encoding="utf-8")]
    assert not missing, f"pages showing portfolio data with no demo banner: {missing}"


def test_the_scan_reads_every_page_file():
    """#673: no page escapes the scan by its number, page 16 among them."""
    on_disk = {p.relative_to(ROOT).as_posix() for p in (ROOT / "pages").glob("*.py")}
    assert set(DEMO_PAGES) == on_disk | {"app.py"}
    assert "pages/16_Policy_Backtest.py" in DEMO_PAGES


def test_every_exemption_names_a_scanned_page_and_a_reason_a_test_checks():
    """A new reason is a new decision, and it needs a test of its own before it lands."""
    for page, reason in BANNER_EXEMPT.items():
        assert page in DEMO_PAGES, page
        assert reason in (NO_LEDGER, RENDERS_NOTHING_IN_DEMO), (page, reason)


@pytest.mark.parametrize("page", [p for p, r in BANNER_EXEMPT.items() if r == NO_LEDGER])
def test_an_exempt_page_is_one_the_scan_reads_and_its_reason_still_holds(page):
    """An exemption names a page that exists and is scanned, and its reason, "it reads no
    portfolio ledger", is held by the scan's own definition: the page imports none of
    the modules that read the ledger or the holdings and queries no trades. A page that
    starts reading the ledger fails here, and its exemption needs a new decision."""
    assert page in DEMO_PAGES
    assert BANNER_EXEMPT[page] == NO_LEDGER
    match = _PORTFOLIO_SOURCE.search((ROOT / page).read_text(encoding="utf-8"))
    assert match is None, f"{page} reads portfolio data now ({match.group(0)!r})"


_DEMO_EMPTY = [p for p, r in BANNER_EXEMPT.items() if r == RENDERS_NOTHING_IN_DEMO]


def _demo_stop_index(tree: ast.Module) -> int:
    """The index of the module-level `if IS_DEMO:` whose body ends in st.stop()."""
    for i, node in enumerate(tree.body):
        if (isinstance(node, ast.If) and isinstance(node.test, ast.Name)
                and node.test.id == "IS_DEMO" and not node.orelse):
            last = node.body[-1]
            if (isinstance(last, ast.Expr) and isinstance(last.value, ast.Call)
                    and ast.unparse(last.value.func) == "st.stop"):
                return i
    raise AssertionError("no module-level `if IS_DEMO: ... st.stop()`")


@pytest.mark.parametrize("page", _DEMO_EMPTY)
def test_a_demo_empty_page_stops_before_it_imports_anything_that_reads_the_book(page):
    """'It stops before any import': before its demo stop the page imports only what
    decides the mode and draws the header, and every portfolio import comes after."""
    tree = ast.parse((ROOT / page).read_text(encoding="utf-8"))
    stop = _demo_stop_index(tree)
    before = set()
    for node in tree.body[:stop]:
        if isinstance(node, ast.Import):
            before |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            before.add(node.module)
    assert before == {"logging", "streamlit", "pandas", "src.config", "src.ui_helpers"}, before
    after = "\n".join(ast.unparse(n) for n in tree.body[stop + 1:])
    assert _PORTFOLIO_SOURCE.search(after), "the exemption is for a page that reads the book"


@pytest.mark.parametrize("page", _DEMO_EMPTY)
def test_app_registers_a_demo_empty_page_only_outside_demo_mode(page):
    """'app.py hides it': its st.Page sits inside `if not IS_DEMO:` and nowhere else."""
    tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
    parent = {c: p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and ast.unparse(n.func) == "st.Page"
             and any(isinstance(a, ast.Constant) and a.value == page for a in n.args)]
    assert len(calls) == 1, f"app.py registers {page} {len(calls)} times"
    node, guards = calls[0], []
    while node in parent:
        up = parent[node]
        if isinstance(up, ast.If):
            guards.append((ast.unparse(up.test), any(node is s for s in up.body)))
        node = up
    assert guards == [("not IS_DEMO", True)], guards


@pytest.fixture
def watch_reads(monkeypatch):
    """Every module an import statement names, and every SQL statement run, while a page
    renders. builtins.__import__ is called by every import statement, cached module or
    not, so a module another test imported first is still seen."""
    import builtins
    seen = {"imports": set(), "sql": []}
    real_import = builtins.__import__

    def _import(name, globals=None, locals=None, fromlist=(), level=0):
        if level == 0:
            seen["imports"].add(name)
            seen["imports"].update(f"{name}.{f}" for f in (fromlist or ()))
        return real_import(name, globals, locals, fromlist, level)

    real_connect = sqlite3.connect

    def _connect(*a, **k):
        conn = real_connect(*a, **k)
        conn.set_trace_callback(seen["sql"].append)
        return conn

    monkeypatch.setattr(builtins, "__import__", _import)
    monkeypatch.setattr(sqlite3, "connect", _connect)
    return seen


def _reads_the_book(seen) -> list:
    hits = sorted(m for m in seen["imports"] if re.fullmatch(
        r"src\.(holdings|tax_lots|performance|attribution|risk|trade_writer|rebalance|"
        r"harvest|reports)", m))
    return hits + [s for s in seen["sql"] if re.search(r"\btrades\b", s, re.I)][:3]


def test_the_import_and_sql_watch_sees_a_page_that_reads_the_book(render, watch_reads):
    """The positive control: on a page that does read the ledger, the watch below sees
    the import and the trades query, so its silence on page 15 is a finding."""
    render("12_Tax_Lots.py")
    found = _reads_the_book(watch_reads)
    assert "src.holdings" in found or "src.tax_lots" in found, sorted(watch_reads["imports"])[:40]
    assert any(re.search(r"\btrades\b", s, re.I) for s in watch_reads["sql"])


@pytest.mark.parametrize("page", _DEMO_EMPTY)
def test_a_demo_empty_page_renders_only_the_personal_mode_message(render, watch_reads, page):
    """'It renders nothing in demo mode', rendered: the header, the page's title and the
    one personal-mode message, and nothing that reads the book on the way."""
    at = render(page.removeprefix("pages/"))
    assert [e.value for e in at.title] == ["Liquidity"]
    assert [e.value for e in at.info] == [
        "The liquidity hierarchy is available in personal mode only."]
    markdown = [e.value for e in at.markdown]
    assert len(markdown) == 1 and "endow-page-header" in markdown[0], markdown
    for kind in ("warning", "error", "success", "caption", "header", "subheader",
                 "metric", "dataframe", "table", "toggle", "button", "number_input",
                 "selectbox", "expander", "exception"):
        assert not list(getattr(at, kind)), (kind, [str(e) for e in getattr(at, kind)][:3])
    assert _reads_the_book(watch_reads) == [], _reads_the_book(watch_reads)


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
    # Performance's and Risk's lines were restated by the writing sweep (item 14b).
    ("2_Performance.py", "the Household View covers the whole household."),
    ("7_Risk.py", "are excluded and appear on the Household View."),
    # The writing sweep (item 14b) gave the list its serial comma.
    ("12_Tax_Lots.py", "IRAs, workplace plans, and HSAs are not shown"),
])
def test_personal_mode_keeps_its_own_scope_lines(render, page, personal):
    """The contrast: the wording moved only in demo mode."""
    texts = _texts(render(page, demo=False))
    assert any(personal in t for t in texts)
    assert not any(DEMO_SCOPE in t for t in texts)
