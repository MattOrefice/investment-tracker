"""A fall prints as a magnitude, "fell 66.9%", never "fell -66.9%" (#536).

Three sentences say Bitcoin "fell": the Asset Evaluation page's two and the PDF's
Drawdown Sensitivity note. The page's read asset_evaluation.decline_in_year, which
returns a fall as a positive fraction. The PDF's printed the signed drawdown it was
handed, so on the committed demo book every report said "Bitcoin fell -66.9%". Decided by
the owner on October 14, 2026: the PDF's sentence prints the size of the fall as the
page's do, and the sign is handled in one place that all three read. That place is
decline_in_year.

A signed drawdown with a noun stays signed: "Its 2022 drawdown of -66.9%" in the case
against is the table's convention (max_drawdown's docstring), not a fall.
"""
from __future__ import annotations

import ast
from pathlib import Path

import src.asset_evaluation as ae
from tests.test_macro_windows_and_drawdown_verb import PORT, _dd_section

ROOT = Path(__file__).resolve().parent.parent


def _section_texts(section: dict) -> "list[str]":
    return [v for v in section.values() if isinstance(v, str)] + [
        t for key in ("args_for", "args_against") for t in section[key]]


def test_on_the_demo_book_the_pdf_says_bitcoin_fell_66_9_percent(tmp_path, monkeypatch):
    """The section as the report builds it, on a copy of the committed demo book,
    offline. The figure is the page's own: tests/render/test_typed_figures_render.py
    pins "BTC fell 66.9% from its peak" and "BTC fell 66.9%" on the same book."""
    section = _dd_section(tmp_path, monkeypatch)
    fall = ae.decline_in_year(ae.get_candidate_returns("BTC-USD", ae.SAMPLE_START))
    assert f"{fall:.1%}" == "66.9%", "premise: the committed book's 2022 fall"
    assert "while Bitcoin fell 66.9%, so adding BTC deepened" in section["dd_2022_note"]
    for text in _section_texts(section):
        assert "fell -" not in text, text
    # The drawdown keeps its sign where a noun carries it.
    argument = next(a for a in section["args_against"] if a.startswith("Bitcoin's own"))
    assert "Its 2022 drawdown of -66.9% came while equities and bonds fell together" in (
        argument)


def test_the_pdf_note_reads_the_fall_from_decline_in_year(tmp_path, monkeypatch):
    """The one place: with decline_in_year answering 43.21%, the note says so. A note
    that took max_drawdown's figure and dropped its sign itself would print 66.9% and
    pass the test above; it fails this one."""
    monkeypatch.setattr(ae, "decline_in_year", lambda returns, year=2022: 0.4321)
    section = _dd_section(tmp_path, monkeypatch)
    assert "while Bitcoin fell 43.2%, so adding BTC deepened" in section["dd_2022_note"]


def test_the_note_states_no_fall_without_a_2022():
    """decline_in_year answers NaN for a series with no 2022, as on the frozen book."""
    for fall in (None, float("nan")):
        note = ae.drawdown_2022_note(fall, PORT)
        assert note.startswith("That year equities and bonds fell together, so adding BTC")
        assert "Bitcoin fell" not in note and "nan" not in note


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


def _is_decline_in_year(node) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "decline_in_year")


def test_every_fell_sentence_prints_a_figure_decline_in_year_returned():
    """Every f-string in the app's code that prints a figure after "fell ": the page's
    names are each bound to a decline_in_year call, and the PDF note's figure is its
    btc_fall parameter, which the report hands it from decline_in_year. A new "fell"
    sentence with a figure from anywhere else fails here."""
    files = sorted([ROOT / "app.py", *(ROOT / "src").rglob("*.py"),
                    *(ROOT / "pages").rglob("*.py")])
    sites = {p.relative_to(ROOT).as_posix(): _fell_figures(p) for p in files}
    sites = {k: v for k, v in sites.items() if v}
    assert set(sites) == {"pages/5_Asset_Evaluation.py", "src/asset_evaluation.py"}, sites

    page = ast.parse((ROOT / "pages" / "5_Asset_Evaluation.py").read_text(encoding="utf-8"))
    bound = {t.id for node in ast.walk(page) if isinstance(node, ast.Assign)
             and _is_decline_in_year(node.value)
             for t in node.targets if isinstance(t, ast.Name)}
    page_names = [name for name, _, _ in sites["pages/5_Asset_Evaluation.py"]]
    assert page_names == ["_btc22", "_eq22", "_bd22", "_btc22"], page_names
    assert set(page_names) <= bound, (page_names, bound)
    other = {t.id for node in ast.walk(page) if isinstance(node, ast.Assign)
             and not _is_decline_in_year(node.value)
             for t in node.targets if isinstance(t, ast.Name)}
    assert not set(page_names) & other, "a 'fell' figure is bound to something else too"

    [(name, _, func)] = sites["src/asset_evaluation.py"]
    assert func.name == "drawdown_2022_note" and name == func.args.args[0].arg == "btc_fall"

    reports = ast.parse((ROOT / "src" / "reports.py").read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(reports) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "drawdown_2022_note"]
    assert len(calls) == 1 and _is_decline_in_year(calls[0].args[0]), [
        ast.unparse(c) for c in calls]
