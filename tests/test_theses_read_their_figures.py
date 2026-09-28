"""The Trade Log's theses read their weights and fees from the data (#421, #447).

The seeded theses were copies of older rationale text, and the figures they typed went
stale as the rationales were corrected: 16/14/8/38/7/8/9-in-72/6/3% against targets of
17.3/15.3/9.2/41.8/8.2/9.2/6.1-in-79.6/4.1/0%, and fees typed where the Research page
reads them from the securities table. They were corrected item by item, keeping each
thesis's own argument: every weight a thesis states is a placeholder over the targets,
and every expense ratio one over the securities table (src/prose_figures.py, {{er:}}).

Pinned here, on data/demo.db, the frozen book, and the table that writes them
(tools/migrate_operations_copy.py):
  * no thesis types a weight or a fee: a percentage equal to a target or an expense
    ratio the book holds fails, and so does any other percentage not listed below as
    a figure that is not the book's (a CPI level, a return assumption, a hypothetical);
  * every placeholder renders;
  * the items #421 and #447 named: thesis 3's exit condition, thesis 12's cash text,
    thesis 16's "highest in the portfolio", thesis 19's jurisdiction, thesis 23's
    target_weight, and what #449 left in theses 18 and 21.

Extended to the fund and SAA rationales the theses were copied from (#456), on both
books and the seeds that write them (src/seed_securities.py, src/seed_saa.py): weights
read the targets, fees {{er:}} and durations {{dur:}}. The check there also catches a
dollar figure, a multiple, a typed duration and an "N of M" weight; the one dollar
figure left, SPHQ against QUAL, is dated and read from each fund's N-PORT filing.
The books' parent rows are left to #461.

Widened to the theses (#462): their check catches a dollar figure, a multiple, a typed
duration and an "N of M" weight too, which found thesis 26's "8 of 49" (it now reads
the share its rationale does). Tax Lots' harvest replacement rationales are checked the
same way.
"""
from __future__ import annotations

import importlib.util
import os
import re
import shutil
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BOOKS = [ROOT / "data" / "demo.db", ROOT / "tests" / "fixtures" / "frozen_book.db"]
COLUMNS = ("macro_view", "view_summary", "vehicle_rationale", "exit_conditions",
           "invalidation_conditions", "expected_return_scenario")
TOKEN = re.compile(r"\{\{[^}]*\}\}")
FIGURE = re.compile(r"\d+(?:\.\d+)?\s?%|\d+\s?bps?\b")
# The wide check (#456 for the rationales, #462 for the theses): a percentage or basis
# points, a dollar figure, a multiple, a weight written "N of M", a decimal number of
# years, and any number of years in the clause after "duration" ("duration (~4-5 years").
WIDE_FIGURE = re.compile(
    FIGURE.pattern + r"|\$\d[\d.,]*\s?[BMKbmk]?|~?\d+(?:\.\d+)?x\b|\b\d+ of \d+\b"
    r"|~?\d+\.\d+ years|(?i:duration)[^.;]{0,40}?~?\d+(?:\.\d+)?(?:-\d+(?:\.\d+)?)?\s?"
    r"(?:years|yrs)\b")

# Percentages a thesis may type because no table holds them: return assumptions, CPI
# and yield thresholds, drawdown sizes, hypotheticals. Each is an exact phrase in that
# thesis. A weight or a fee typed back into a thesis is in none of these.
NOT_THE_BOOKS = {
    3: [">3% per year", "5-7% annually"],
    4: ["~1-2% over market", "over 60% of quality"],
    5: ["~3-4% incremental"],
    6: ["~1-2% premium", "~1% premium"],
    8: ["50%+ drawdowns", "1-2% long-run"],
    9: ["exceed 3%", "4% CPI", "~5-10% portfolio"],
    10: ["below 0%", "below 2%, making", "(~2%)", "above 2.5%", "usually 20-30%"],
    11: ["below -1%", "which 2-3% would not", "30%+ drawdowns"],
    12: ["collapse below 2%"],
    # SPHQ against QUAL, dated and read from each fund's N-PORT filing with the SEC (#456).
    14: ["$19.4B against QUAL's $46.5B on July 31, 2026"],
    # SPAXX's own text, kept equal to the Research page's by tools/migrate_markets_copy.py:
    # its yield trigger. The cash level it named ("toward 1%") was dropped.
    23: ["materially below 2%"],
}


def _ro(book):
    return sqlite3.connect(f"file:{Path(book).as_posix()}?mode=ro", uri=True)


def _book_figures(book) -> "dict[str, str]":
    """Every weight and fee the book holds, as a thesis would print it."""
    con = _ro(book)
    try:
        held: "dict[str, list[str]]" = {}
        for n, w in con.execute("SELECT name, target_weight FROM asset_classes"):
            if w:
                held.setdefault(f"{w * 100:.1f}%", []).append(f"the {n} target")
        for t, e in con.execute("SELECT ticker, expense_ratio FROM securities ORDER BY ticker"):
            if e:
                held.setdefault(f"{e * 100:.2f}%", []).append(f"{t}'s expense ratio")
        return {fig: ", ".join(names) for fig, names in held.items()}
    finally:
        con.close()


def _theses(book) -> "dict[int, dict[str, str]]":
    con = _ro(book)
    try:
        return {r[0]: dict(zip(COLUMNS, r[1:])) for r in con.execute(
            f"SELECT thesis_id, {', '.join(COLUMNS)} FROM theses ORDER BY thesis_id")}
    finally:
        con.close()


def _table() -> "dict[int, dict[str, str]]":
    spec = importlib.util.spec_from_file_location(
        "migrate_operations_copy", ROOT / "tools" / "migrate_operations_copy.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return {int(k): v for k, v in mod.NEW.items()}


def typed_figures(theses: "dict", held: "dict[str, str]", allowed: "dict | None" = None,
                  figure: "re.Pattern" = WIDE_FIGURE, label: str = "thesis") -> "list[str]":
    """Every figure a text types that it should read: one the book holds, or one not
    listed as a figure that is not the book's. Theses by default; the rationales pass
    their own list and pattern."""
    allowed = NOT_THE_BOOKS if allowed is None else allowed
    out = []
    for tid, cells in theses.items():
        for col, text in cells.items():
            if not isinstance(text, str) or not text:
                continue
            plain = TOKEN.sub("", text)
            spans = [(m.start(), m.end()) for p in allowed.get(tid, [])
                     for m in re.finditer(re.escape(p), plain)]
            for m in figure.finditer(plain):
                fig = m.group(0).replace(" ", "")
                if fig in held:
                    out.append(f"{label} {tid} {col} types {fig}, {held[fig]}")
                elif not any(a <= m.start() and m.end() <= b for a, b in spans):
                    out.append(f"{label} {tid} {col} types {fig}: "
                               f"...{plain[max(0, m.start() - 30):m.end() + 10]}...")
    return out


@pytest.mark.parametrize("book", BOOKS, ids=lambda p: p.name)
def test_no_thesis_types_a_weight_or_a_fee(book):
    assert typed_figures(_theses(book), _book_figures(book)) == []


def test_the_table_that_writes_them_types_none_either():
    assert typed_figures(_table(), _book_figures(BOOKS[0])) == []


def test_the_check_fails_on_a_weight_or_a_fee_typed_back():
    """The check's own contrast: the stale weight and the fee this pass removed."""
    held = _book_figures(BOOKS[0])
    theses = _theses(BOOKS[0])
    weight = {3: dict(theses[3], macro_view=theses[3]["macro_view"].replace(
        "{{w:US Large Core}}", "16%"))}
    fee = {13: dict(theses[13], vehicle_rationale=theses[13]["vehicle_rationale"].replace(
        "{{er:VOO}}", "0.03%"))}
    assert typed_figures(weight, held) and "16%" in typed_figures(weight, held)[0]
    assert typed_figures(fee, held) and "VOO's expense ratio" in typed_figures(fee, held)[0]
    # #462: the widened check, on the weight this pass removed from thesis 26.
    theses_26 = _theses(BOOKS[0])[26]
    share = "{{share:US Small Cap|US Large Core+US Large Quality+US Large Value+US Small Cap}} of the US"
    assert share in theses_26["macro_view"], "premise: thesis 26 reads the share"
    back = {26: dict(theses_26, macro_view=theses_26["macro_view"].replace(
        share, "8 of 49 in the US"))}
    assert typed_figures(back, held) and "8of49" in typed_figures(back, held)[0]
    assert not typed_figures(back, held, figure=FIGURE), "the old check could not see it"


# ── every placeholder renders ────────────────────────────────────────────────────

@pytest.fixture(params=BOOKS, ids=lambda p: p.name)
def book_copy(request, tmp_path, monkeypatch):
    import socket
    import src.db as db
    import src.prices as prices
    from tests.conftest import pin_today, unpin_leftovers
    copy = tmp_path / "book.db"
    shutil.copyfile(request.param, copy)
    os.chmod(copy, 0o644)

    def _offline(*_a, **_k):
        raise OSError("offline")

    monkeypatch.setattr(socket, "getaddrinfo", _offline)
    monkeypatch.setattr(prices._SESSION, "get", _offline)
    monkeypatch.setattr(prices, "_GAP_FETCH", False)
    monkeypatch.setattr(db, "DB_PATH", copy)
    monkeypatch.setattr(db, "_migrated_paths", set())
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    pin_today(monkeypatch)
    yield copy
    unpin_leftovers()


def _rendered(book) -> "dict[int, dict[str, str]]":
    from src.prose_figures import render
    return {tid: {c: render(t) for c, t in cells.items()}
            for tid, cells in _theses(book).items()}


def test_every_placeholder_renders(book_copy):
    left = [(tid, c) for tid, cells in _rendered(book_copy).items()
            for c, t in cells.items() if t and "{{" in t]
    assert left == []


def test_the_items_421_and_447_named(book_copy):
    r = _rendered(book_copy)
    # thesis 3: the exit condition agreed with its own invalidation condition
    assert "tilts consistently outperform pure cap-weight" in r[3]["exit_conditions"]
    assert "Factor tilts outperform pure cap-weight" in r[3]["invalidation_conditions"]
    assert r[3]["view_summary"].count("Core is sized at 17.3% because") == 1
    # thesis 12 (#447): thesis 23's framing, no "$2.4k", no cash weight, no 1-2% range
    for col in ("macro_view", "view_summary"):
        t = r[12][col]
        assert "The operational cash balance, " in t and "is untargeted liquidity" in t
        assert "$2.4k" not in t and "3% handles" not in t and "1-2%" not in t
    assert r[12]["exit_conditions"].startswith("Would reduce if cash yields collapse below 2%")
    # thesis 16: 0.25% is not the highest expense ratio in the portfolio
    assert "highest in the portfolio" not in r[16]["vehicle_rationale"]
    assert "Its 0.25% expense ratio buys factor exposure." in r[16]["vehicle_rationale"]
    # thesis 19: Pennsylvania, as 776e1e6 corrected VGIT's rationale
    t = r[19]["vehicle_rationale"]
    assert "Pennsylvania's flat rate" in t and "DC" not in t and "high-income-tax" not in t
    assert "at 0.04% versus IEF's 0.15%" in t and "a 6.1% sleeve inside a 79.6% growth" in t
    # theses 18 and 21: #449's corrections hold, with the fees read from the data
    assert ("EEM costs 0.70% against IEMG's 0.09%, the largest fee gap in the portfolio. "
            "The two are not the same exposure: IEMG tracks MSCI Emerging Markets IMI"
            in r[18]["vehicle_rationale"])
    assert "seven times" not in r[18]["vehicle_rationale"] and "61 bps" not in r[18]["vehicle_rationale"]
    assert "it costs 0.59% against DBC's 0.85%." in r[21]["vehicle_rationale"]
    assert "SPY's 0.09% is not worth paying" in r[13]["vehicle_rationale"]


@pytest.mark.parametrize("book", BOOKS, ids=lambda p: p.name)
def test_thesis_23_holds_the_cash_sleeves_target(book):
    con = _ro(book)
    try:
        (weight,), = con.execute("SELECT target_weight FROM theses WHERE thesis_id = 23")
        (target,), = con.execute("SELECT target_weight FROM asset_classes WHERE "
                                 "name = 'Cash / SPAXX'")
    finally:
        con.close()
    assert weight == target == 0.0


# ── the rationales (#456) ──────────────────────────────────────────────────────────

# The rationales' check is the wide one (WIDE_FIGURE), as the theses' is since #462.
RATIONALE_FIGURE = WIDE_FIGURE

# Figures a rationale may type because no table holds them: thresholds, a return
# assumption, a drawdown size, and SPHQ's and QUAL's net assets, dated and read from each
# fund's N-PORT filing with the SEC (period July 31, 2026). Each is an exact phrase.
RATIONALES_NOT_THE_BOOKS = {
    "SPHQ": ["$19.4B against QUAL's $46.5B on July 31, 2026"],
    "SCHP": ["below 1.5%"],
    "US Small Cap": ["~1-2% premium"],
    "Emerging Markets": ["50%+ drawdowns"],
    "Core Fixed Income": ["exceed 3%"],
    "TIPS": ["usually 20-30%", "above 2.5%"],
    "Cash / SPAXX": ["below 2%"],
    # Thesis 11's own hypothetical and drawdown size, which the sleeve's text shares.
    "Real Assets": ["which 2-3% would not", "30%+ drawdowns"],
}


def _book_rationales(book) -> "dict[str, dict[str, str]]":
    """Every fund and sleeve rationale in the book. Not the parent rows: two carry text
    no seed writes, Real Assets' with a typed weight, and which text they should hold
    is #461's decision. The seeds' parent text is scanned below."""
    con = _ro(book)
    try:
        out = {t: {"holding_rationale": r} for t, r in con.execute(
            "SELECT ticker, holding_rationale FROM securities")}
        for name, r in con.execute("SELECT name, rationale FROM asset_classes "
                                   "WHERE parent_id IS NOT NULL"):
            out.setdefault(name, {})["rationale"] = r
        return out
    finally:
        con.close()


def _seed_rationales() -> "dict[str, dict[str, str]]":
    import src.seed_saa as seed_saa
    from src.seed_securities import HOLDINGS
    out = {h["ticker"]: {"holding_rationale": h.get("holding_rationale")} for h in HOLDINGS}
    for key, rows in vars(seed_saa).items():
        if key.isupper() and isinstance(rows, (list, tuple)):
            for row in rows:
                if isinstance(row, dict) and row.get("rationale"):
                    out.setdefault(row["name"], {})[key] = row["rationale"]
    return out


def _rationale_figures(texts, held):
    return typed_figures(texts, held, RATIONALES_NOT_THE_BOOKS, RATIONALE_FIGURE, "rationale")


@pytest.mark.parametrize("book", BOOKS, ids=lambda p: p.name)
def test_no_rationale_types_a_weight_a_fee_or_an_unsourced_figure(book):
    r = _book_rationales(book)
    assert {"VOO", "SPHQ", "VGIT", "International Core", "Cash / SPAXX"} <= set(r), (
        "premise: the scan reaches the book's fund and sleeve rationales")
    assert _rationale_figures(r, _book_figures(book)) == []


def test_the_seeds_that_write_the_rationales_type_none_either():
    seeds = _seed_rationales()
    assert {"VOO", "SPHQ", "VGIT", "International Core", "Cash / SPAXX"} <= set(seeds), (
        "premise: the scan reaches the fund and sleeve seeds")
    assert _rationale_figures(seeds, _book_figures(BOOKS[0])) == []


def test_the_rationale_check_fails_on_each_figure_this_pass_removed():
    """The check's own contrast: one of each kind, typed back."""
    held = _book_figures(BOOKS[0])
    r = _book_rationales(BOOKS[0])
    back = {
        "a fee": ("VTV", "holding_rationale", "{{er:VTV}}", "0.04%"),
        "a weight": ("VGIT", "holding_rationale", "{{w:Core Fixed Income}}", "6.1%"),
        "a duration": ("VGIT", "holding_rationale", "{{dur:VGIT}}", "~5.5 years"),
        "an N-of-M weight": ("International Small Value", "rationale",
                             "{{share:US Small Cap|US Large Core+US Large Quality+US Large "
                             "Value+US Small Cap}}", "8 of 49"),
        "an undated AUM": ("VNQ", "holding_rationale", "a {{er:VNQ}} ER", "$35B AUM, 0.12% ER"),
        "a multiple": ("IEMG", "holding_rationale", "a valuation discount to global equities",
                       "~10x P/E"),
    }
    for kind, (key, col, token, typed) in back.items():
        assert token in r[key][col], (kind, "premise: the token is there to replace")
        found = _rationale_figures({key: {col: r[key][col].replace(token, typed, 1)}}, held)
        assert found, f"the check misses {kind} typed back"


@pytest.fixture(params=BOOKS, ids=lambda p: p.name)
def rationale_book(request, tmp_path, monkeypatch):
    import src.db as db
    copy = tmp_path / "book.db"
    shutil.copyfile(request.param, copy)
    os.chmod(copy, 0o644)
    monkeypatch.setattr(db, "DB_PATH", copy)
    monkeypatch.setattr(db, "_migrated_paths", set())
    monkeypatch.setattr(db, "_RUNTIME_CACHE", None)
    return request.param


def test_every_rationale_placeholder_renders_and_the_456_items_read_right(rationale_book):
    from src.positioning import live_fund_durations
    from src.prose_figures import render
    r = {k: {c: render(t) for c, t in cells.items() if t}
         for k, cells in _book_rationales(rationale_book).items()}
    assert [(k, c) for k, cells in r.items() for c, t in cells.items() if "{{" in t] == []
    assert ("The tradeoff is smaller net assets ($19.4B against QUAL's $46.5B on July 31, "
            "2026, from each fund's N-PORT filing with the SEC)") in r["SPHQ"]["holding_rationale"]
    dur = live_fund_durations()
    assert (f"Duration is modestly shorter ({dur['VGIT']:.1f} years vs IEF's "
            f"{dur['IEF']:.1f} years), appropriate for a 6.1% sleeve inside a 79.6% growth "
            "portfolio.") in r["VGIT"]["holding_rationale"]
    assert "China trades at a valuation discount to global equities" in r["IEMG"]["holding_rationale"]
    assert r["VNQ"]["holding_rationale"].startswith(
        "VNQ is the standard for US REIT exposure: a 0.12% ER and broad diversification")
    assert "EEM costs 0.70% against IEMG's 0.09%, the largest fee gap in the portfolio. " \
        in r["IEMG"]["holding_rationale"]
    cash = r["Cash / SPAXX"]["rationale"]
    assert "$2.4k" not in cash and "1-2%" not in cash
    assert "holding meaningful cash is performance drag. The operational cash balance" in cash
    assert cash.endswith("**Would reduce** if cash yields collapse below 2%.")
    # "the same share it holds in the US book": the two shares render equal.
    core = r["International Core"]["rationale"]
    share = core.split("Core is ", 1)[1].split(" of international equity", 1)[0]
    assert f"the same share it holds in the US book ({share})" in core
    for gone in ("three times", "ten times", "seven times", "61 bps", "~10x", "$35B", "$6B"):
        assert not any(gone in t for cells in r.values() for t in cells.values()), gone


# ── Tax Lots' harvest rationales (#462) ──────────────────────────────────────────

def test_no_harvest_rationale_types_a_duration_and_each_keeps_its_point():
    from src.harvest import REPLACEMENT_MAP
    texts = {t: {"rationale": why} for t, (_rep, why, _risk) in REPLACEMENT_MAP.items()}
    assert {"VGIT", "SCHP"} <= set(texts), "premise: the scan reaches the harvest map"
    assert typed_figures(texts, {}, {}) == []
    assert "Different issuer; comparable duration." in texts["VGIT"]["rationale"]
    assert "shorter duration, a modest duration shift accepted" in texts["SCHP"]["rationale"]


def test_the_harvest_check_sees_a_duration_typed_back():
    from src.harvest import REPLACEMENT_MAP
    why = REPLACEMENT_MAP["VGIT"][1].replace("comparable duration.",
                                             "comparable duration (~4-5 years).")
    assert typed_figures({"VGIT": {"rationale": why}}, {}, {})
    assert not typed_figures({"VGIT": {"rationale": why}}, {}, {}, figure=FIGURE), (
        "a number of years is not a percentage: only the duration arm sees it")
