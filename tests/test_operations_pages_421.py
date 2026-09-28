"""The Operations pages say what their code and data do (#421, the rest of it).

  * Capital Deployment named lot_source='inception', a value no book holds, and said
    above-band sleeves "receive no new cash" and cash goes to "below-band sleeves
    first". suggest_contributions fills each shortfall to TARGET, and spreads what is
    left across every sleeve by target weight, above-target ones included.
  * The Trade Log said "All current theses dated 2025-05-01 inception"; theses.created_at
    holds no such date, and days held count from each thesis's own.
  * Tax Lots gave the pool's threshold as the action threshold ($100, 5%). The pool uses
    HARVEST_MATERIALITY_THRESHOLD per lot, with no percentage; and an unescaped $ pair in
    one markdown call can render as LaTeX.
  * harvest.py called IUSV the Russell 1000 Value fund, and typed SCHP's sleeve as 6%.
"""
from __future__ import annotations

import ast
import re
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DEMO_DB = ROOT / "data" / "demo.db"


def _source(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _strings(rel: str) -> "list[str]":
    """Every string literal in a page, joined as Python joins adjacent pieces."""
    tree = ast.parse(_source(rel))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.append(node.value)
        elif isinstance(node, ast.JoinedStr):
            out.append("".join(v.value if isinstance(v, ast.Constant) else "{}"
                               for v in node.values))
    return out


def _book(sql: str):
    con = sqlite3.connect(f"file:{DEMO_DB.as_posix()}?mode=ro", uri=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


# ── Capital Deployment ─────────────────────────────────────────────────────────

def test_capital_deployment_names_only_lot_sources_the_book_holds():
    named = set(re.findall(r"lot_source='(\w+)'", " ".join(_strings("pages/11_Capital_Deployment.py"))))
    held = {r[0] for r in _book("SELECT DISTINCT lot_source FROM trades")}
    assert named and named <= held, (named, held)


def test_the_allocation_text_says_what_suggest_contributions_does():
    import sys
    sys.path.insert(0, str(ROOT / "tests"))
    from test_rebalance import _cov
    from src.rebalance import suggest_contributions
    prices = {"CORE_ETF": 100.0, "BOND_ETF": 100.0}
    r = suggest_contributions(10_000, 2_000, {"Core": 0.60, "Bonds": 0.40},
                              {"Core": 0.50, "Bonds": 0.50},
                              {"CORE_ETF": "Core", "BOND_ETF": "Bonds"}, prices,
                              coverage=_cov(prices))
    core = r[r["Sleeve"] == "Core"].iloc[0]
    # The code: a sleeve 10 points above target still gets a share of what is left.
    assert core["Rationale"] == "above target" and core["Suggested $"] == pytest.approx(500.0)
    text = " ".join(_strings("pages/11_Capital_Deployment.py"))
    assert "including sleeves above target" in text
    assert "shortfall to its SAA target" in text
    for claim in ("receive no new cash", "receives no new cash", "below-band sleeves first"):
        assert claim not in text, claim


# ── Trade Log ──────────────────────────────────────────────────────────────────

def test_the_days_held_note_names_no_date_the_theses_do_not_have():
    text = " ".join(_strings("pages/10_Trade_Log.py"))
    note = text[text.index("**Days held**"):].split("\n\n")[0]
    created = {r[0][:10] for r in _book("SELECT created_at FROM theses")}
    assert set(re.findall(r"\d{4}-\d{2}-\d{2}", note)) <= created, note
    assert "days since the thesis was created" in note


# ── Tax Lots ───────────────────────────────────────────────────────────────────

@pytest.mark.usefixtures("frozen_book_module")
def test_the_pool_sentence_states_its_own_threshold_escaped():
    from streamlit.testing.v1 import AppTest
    from src.tax_lots import HARVEST_MATERIALITY_THRESHOLD
    at = AppTest.from_file(str(ROOT / "pages" / "12_Tax_Lots.py"), default_timeout=300).run()
    assert not at.exception, [str(e.value) for e in at.exception]
    how = next(m.value for m in at.markdown if "**Harvest Candidate Pool**" in m.value)
    pool = how[how.index("**Harvest Candidate Pool**"):].split("\n- ")[0]
    assert f"\\${HARVEST_MATERIALITY_THRESHOLD:.0f} each" in pool, pool
    assert "$100" not in pool and "5%" not in pool
    # Every $ in the whole markdown call is escaped: a bare pair renders as LaTeX.
    assert not re.search(r"(?<!\\)\$", how), how


def test_the_tax_lots_page_renders_the_replacement_note():
    """The note carries a placeholder (SCHP's sleeve weight): the page renders it."""
    calls = [n for n in ast.walk(ast.parse(_source("pages/12_Tax_Lots.py")))
             if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "render"
             and ast.unparse(n.args[0]) == "c['replacement_rationale']"]
    assert len(calls) == 1


# ── harvest.py ─────────────────────────────────────────────────────────────────

def test_iusv_is_the_sp_value_fund():
    from src.harvest import REPLACEMENT_MAP
    ticker, text, _risk = REPLACEMENT_MAP["VTV"]
    assert ticker == "IUSV" and "iShares Core S&P U.S. Value" in text
    assert "Russell 1000" not in text


def test_no_replacement_note_types_a_sleeve_weight(monkeypatch):
    import src.db as db
    from src.harvest import REPLACEMENT_MAP
    from src.prose_figures import render
    typed = [(t, m.group(0)) for t, (_r, text, _k) in REPLACEMENT_MAP.items()
             for m in re.finditer(r"\d+(?:\.\d+)?% sleeve", text)]
    assert typed == []
    monkeypatch.setattr(db, "DB_PATH", DEMO_DB)
    monkeypatch.setattr(db, "_migrated_paths", set())
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    (tips,), = _book("SELECT target_weight FROM asset_classes WHERE name = 'TIPS' AND "
                     "parent_id IS NOT NULL")
    assert render(REPLACEMENT_MAP["SCHP"][1]).endswith(f"for a {tips * 100:.1f}% sleeve.")
