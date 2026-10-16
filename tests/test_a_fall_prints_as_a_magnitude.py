"""2022 is stated once, as calendar-year returns, on the page and in the PDF (#425, #604).

#536 (decided by the owner on October 14, 2026): a fall prints as a magnitude, "fell
66.9%", never "fell -66.9%". Three sentences said Bitcoin "fell": the Asset Evaluation
page's two and the PDF's Drawdown Sensitivity note, all reading
asset_evaluation.decline_in_year.

MIGRATED at #425 (decided by the owner on October 15, 2026): the page states 2022 once,
as each series' calendar-year return (asset_evaluation.return_in_year), so its two "fell"
sentences went and the PDF's note was the one left.

MIGRATED again at #604 (decided by the owner on October 15, 2026): the PDF takes #425's
decision. Its note states the same calendar-year returns, through the page's own
sentence, and the case-against bullet drops "Its 2022 drawdown of -66.9% came while
equities and bonds fell together". No sentence prints a figure after "fell" now, and
decline_in_year is gone with its last reader. The census below holds that: a new "fell"
sentence with a figure fails it, and so does a 2022 figure from anything but
return_in_year.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pandas as pd

import src.asset_evaluation as ae
from tests.test_macro_windows_and_drawdown_verb import PORT, _dd_section

ROOT = Path(__file__).resolve().parent.parent
RETURNS = "Over calendar 2022, SPY returned -18.2%, AGG returned -13.0% and BTC returned -64.3%"


def _section_texts(section: dict) -> "list[str]":
    return [v for v in section.values() if isinstance(v, str)] + [
        t for key in ("args_for", "args_against") for t in section[key]]


def test_on_the_demo_book_the_pdf_states_2022_once_as_calendar_year_returns(
        tmp_path, monkeypatch):
    """The section as the report builds it, on a copy of the committed demo book,
    offline. The figures are the page's own on the same book
    (tests/render/test_typed_figures_render.py), and none of the peak-to-trough falls
    the note and the bullet printed is left."""
    section = _dd_section(tmp_path, monkeypatch)
    assert section["dd_2022_note"].startswith(
        f"{RETURNS}: all three fell in the same year. This joint stress scenario"), (
        section["dd_2022_note"])
    texts = " ".join(_section_texts(section))
    assert texts.count("-64.3%") == 1, "bitcoin's 2022 figure is stated once"
    for gone in ("66.9%", "fell -", "Its 2022 drawdown of", "fell together"):
        assert gone not in texts, gone


def test_the_pdf_note_reads_its_figures_from_return_in_year(tmp_path, monkeypatch):
    """With return_in_year answering -43.21% for every series, the note says so. A note
    that read anything else would print the book's own figures and pass the test above;
    it fails this one."""
    monkeypatch.setattr(ae, "return_in_year", lambda returns, year=2022: -0.4321)
    section = _dd_section(tmp_path, monkeypatch)
    assert section["dd_2022_note"].startswith(
        "Over calendar 2022, SPY returned -43.2%, AGG returned -43.2% and BTC returned "
        "-43.2%: all three fell"), section["dd_2022_note"]


def test_the_note_states_no_returns_without_all_three():
    """A missing figure (the frozen book has no 2022) drops the sentence, and the note
    goes straight to what the allocation did."""
    for returns in (None, {}, {"SPY": -0.18, "AGG": float("nan"), "BTC": -0.64}):
        note = ae.drawdown_2022_note(returns, PORT)
        assert note.startswith("Adding BTC deepened the portfolio's 2022 drawdown"), note
        assert "calendar 2022" not in note and "nan" not in note


def _fell_figures(path: Path) -> "list[tuple[str, ast.AST, ast.FunctionDef | None]]":
    """Every figure an f-string prints straight after "fell ", with its enclosing
    function: (the name it formats, the node, the function or None)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []

    def visit(node, func):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func = node
        if isinstance(node, ast.JoinedStr):
            parts = node.values
            for before, value in zip(parts, parts[1:]):
                if (isinstance(before, ast.Constant) and isinstance(before.value, str)
                        and before.value.endswith("fell ")
                        and isinstance(value, ast.FormattedValue)):
                    name = value.value.id if isinstance(value.value, ast.Name) else (
                        ast.unparse(value.value))
                    found.append((name, value, func))
        for child in ast.iter_child_nodes(node):
            visit(child, func)

    visit(tree, None)
    return found


def test_no_sentence_prints_a_figure_after_fell(tmp_path):
    """MIGRATED at #604: the census was the PDF note's site alone; it is empty now. The
    census is checked against a positive: _fell_figures finds the old note's site."""
    files = sorted([ROOT / "app.py", *(ROOT / "src").rglob("*.py"),
                    *(ROOT / "pages").rglob("*.py")])
    sites = {p.relative_to(ROOT).as_posix(): _fell_figures(p) for p in files}
    assert {k: v for k, v in sites.items() if v} == {}
    assert len(files) > 50, "premise: the census read the code"
    old = tmp_path / "old_note.py"
    old.write_text('def note(btc_fall):\n    return f"Bitcoin fell {btc_fall:.1%}"\n',
                   encoding="utf-8")
    [(name, _, func)] = _fell_figures(old)
    assert (name, func.name) == ("btc_fall", "note")


def _year_values(tree: ast.AST, call: str) -> "list[ast.AST]":
    """The values of the dict handed to ``call``'s first argument, by name."""
    [site] = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
              and isinstance(n.func, ast.Attribute) and n.func.attr == call]
    arg = site.args[0]
    [bound] = [n.value for n in ast.walk(tree) if isinstance(n, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == arg.id for t in n.targets)]
    assert isinstance(bound, ast.Dict) and len(bound.values) == 3, ast.unparse(bound)
    return bound.values


def test_the_pages_2022_figures_are_each_a_calendar_year_return():
    """#425: the page states 2022 once, from one dict whose every value is a
    return_in_year call, handed to stress_2022_sentences."""
    page = ast.parse((ROOT / "pages" / "5_Asset_Evaluation.py").read_text(encoding="utf-8"))
    values = _year_values(page, "stress_2022_sentences")
    assert all(isinstance(v, ast.Call) and isinstance(v.func, ast.Attribute)
               and v.func.attr == "return_in_year" for v in values), [
        ast.unparse(v) for v in values]


def test_the_pdfs_2022_figures_are_each_a_calendar_year_return():
    """#604: the PDF's note takes one dict whose values are return_in_year's, bitcoin's
    directly and the two legs' through a loader that answers NaN when a series cannot
    be read."""
    reports = ast.parse((ROOT / "src" / "reports.py").read_text(encoding="utf-8"))
    values = _year_values(reports, "drawdown_2022_note")
    calls = [ast.unparse(v) for v in values]
    assert calls == ["_year_return(_eq_t)", "_year_return(_bd_t)", "ae.return_in_year(btc_ret)"]
    [helper] = [n for n in ast.walk(reports) if isinstance(n, ast.FunctionDef)
                and n.name == "_year_return"]
    assert "ae.return_in_year(ae.get_candidate_returns(ticker, ae.SAMPLE_START))" in (
        ast.unparse(helper))


def test_decline_in_year_is_gone():
    assert not hasattr(ae, "decline_in_year")
    r = pd.Series([0.50, 0.10, -0.50, 0.20], index=pd.to_datetime(
        ["2021-12-31", "2022-01-03", "2022-06-01", "2022-12-30"]))
    assert ae.return_in_year(r) != -ae.max_drawdown(r.loc["2022"]), (
        "premise: the two measures differ on this series")


def test_a_leg_that_cannot_be_read_drops_the_sentence_not_the_section(tmp_path, monkeypatch):
    """The PDF's loader answers NaN when a series cannot be read, so the note keeps what
    the allocation did and states no returns, rather than marking the drawdown section
    unavailable."""
    real = ae.get_candidate_returns

    def failing(ticker, *a, **k):
        if ticker == "AGG":
            raise OSError("AGG unavailable")
        return real(ticker, *a, **k)

    monkeypatch.setattr(ae, "get_candidate_returns", failing)
    section = _dd_section(tmp_path, monkeypatch)
    assert "drawdown" not in section["unavailable"]
    assert section["dd_2022_note"].startswith("Adding BTC deepened"), section["dd_2022_note"]
