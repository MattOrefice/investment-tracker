"""The factor prose types no figures and keeps its arguments (#478 I01 to I05).

The quarterly report's Factor section prints two pieces of prose from code:
factors.build_factor_prose (the report's factor.prose, and the Factor Profile page's
paragraphs) and factors.em_disclosure (factor.em_note). Both typed figures no input
holds, so a locked quarter printed them undated and nothing could correct them:

  * VGIT's "~5.5-year" and SCHP's "~6.8-year" durations, beside a duration line that
    reads the dated ones (4.9 and 6.3 when this was written);
  * Korea's "~3-4% of VEA's holdings" (Vanguard's fact sheet as of June 30, 2026 gives
    10.2%) and "approximately 95-98% in calendar 2025", a return with no index named;
  * "Real assets (VNQ 60%, DBC 40%)", for a sleeve that holds VNQ and PDBC;
  * the untilted EM text's "~27% China weight", "approximately 12 observations over
    the current 1-year window" and "3+ years of monthly data".

Pinned here:
  * #452's typed-figure check, widened to spans of years and counts of observations,
    reads the LITERAL text of both functions off the source and finds nothing, with no
    exception listed. A figure the prose prints is a regression result or one of the
    module's own constants;
  * the check fails on each figure this pass removed, typed back;
  * each argument is kept: the FI paragraph points to the duration line (where the
    reader finds it, on the page and in the PDF), the developed paragraph keeps the
    universe mismatch with both classification claims, the EM text keeps its reason.

The check covers these two functions and no other prose in src/factors.py.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from markupsafe import escape

from tests.conftest import pin_today, point_at_frozen_book, unpin_leftovers
from tests.test_theses_read_their_figures import WIDE_FIGURE, typed_figures

ROOT = Path(__file__).resolve().parent.parent
SOURCE = (ROOT / "src" / "factors.py").read_text(encoding="utf-8")
FUNCTIONS = ("build_factor_prose", "em_disclosure")

# #452's wide check, plus what the factor prose typed that it could not see: a span
# of years ("~5.5-year", "1-year") and a count of observations or of years ("12
# observations", "3+ years").
FACTOR_FIGURE = re.compile(
    WIDE_FIGURE.pattern
    + r"|~?\d+(?:\.\d+)?-(?:year|month|day)\b"
    + r"|\b\d+\+? (?:observations|years|months|trading days|days)\b")


def literal_texts(source: str, function: str) -> "list[str]":
    """Every string the function's code spells out, read from the syntax tree: each
    f-string with its formatted values replaced by "{}", and each plain string. The
    docstring and format specs (".2f") are not prose and are left out."""
    tree = ast.parse(source)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == function)
    skip = set()
    first = fn.body[0]
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
        skip.add(id(first.value))                         # the docstring
    for node in ast.walk(fn):
        if isinstance(node, ast.FormattedValue) and node.format_spec is not None:
            skip.add(id(node.format_spec))
            skip.update(id(v) for v in ast.walk(node.format_spec))
    texts = []
    for node in ast.walk(fn):
        if isinstance(node, ast.JoinedStr) and id(node) not in skip:
            parts = []
            for v in node.values:
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    parts.append(v.value)
                    skip.add(id(v))
                else:
                    parts.append("{}")
            texts.append("".join(parts))
    for node in ast.walk(fn):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in skip):
            texts.append(node.value)
    return texts


def _typed(source: str) -> "list[str]":
    """Every figure the two functions type, by #452's own function, with no figure
    held by a book to match and no exception allowed."""
    texts = {fn: dict(enumerate(literal_texts(source, fn))) for fn in FUNCTIONS}
    return typed_figures(texts, {}, {}, figure=FACTOR_FIGURE, label="factor prose")


# ── the check ────────────────────────────────────────────────────────────────

def test_the_scan_reaches_the_prose_it_checks():
    prose = " ".join(literal_texts(SOURCE, "build_factor_prose"))
    for phrase in ("it reflects a universe mismatch, not skill", "TERM factor",
                   "Real assets are excluded", "loads on Mkt-RF at {}"):
        assert phrase in prose, phrase
    em = literal_texts(SOURCE, "em_disclosure")
    assert sum("cap-weighted broad EM exposure" in t for t in em) == 2, (
        "premise: both the tilted and the untilted text are read")
    assert not any("Resolved at CALL time" in t for t in em), "the docstring is not prose"
    assert ".2f" not in prose and ".0f" not in prose, "format specs are not prose"


def test_the_factor_prose_types_no_figure():
    assert _typed(SOURCE) == []


# Each figure this pass removed: (the text now in the source, the text it replaced).
REMOVED = {
    "VGIT's and SCHP's durations": (
        "the fund durations behind the Fixed Income Effective \"\n"
        "            f\"Duration line {duration_line_location}. \"",
        "VGIT's ~5.5-year effective duration and SCHP's ~6.8-year \"\n"
        "            f\"duration. \"", ["~5.5-year", "~6.8-year"]),
    "Korea's weight in VEA": (
        "South Korea as Developed, while \"",
        "South Korea as Developed (~3-4% of VEA's holdings), while \"", ["4%"]),
    "Korea's 2025 return": (
        "f\"The return on Korean equities falls outside",
        "f\"Korean equities returned approximately 95-98% in calendar 2025. \"\n"
        "            f\"That return falls outside", ["98%"]),
    "the Real Assets split": (
        "\"Real assets are excluded: ",
        "\"Real assets (VNQ 60%, DBC 40%) are excluded: ", ["60%", "40%"]),
    "the FI sleeve's mix": (
        "f\"The FI sleeve ({fi_mix}, ",
        "f\"The FI sleeve (VGIT 60% / SCHP 40%, ", ["60%", "40%"]),
    "the China weight": (
        "\"IEMG provides passive cap-weighted broad EM exposure. Factor decomposition \"",
        "\"IEMG provides passive cap-weighted broad EM exposure (~27% China weight at \"\n"
        "            \"current index composition). Factor decomposition \"", ["27%"]),
    "the observation count": (
        "is too short for stable inference. \"",
        "would yield approximately 12 observations over the current 1-year window. \"",
        ["12observations", "1-year"]),
    "the years of history": (
        "accumulated enough \"\n            \"monthly history.\"",
        "accumulated sufficient history (target: 3+ years of \"\n"
        "            \"monthly data).\"", ["3+years"]),
}


@pytest.mark.parametrize("what", sorted(REMOVED))
def test_the_check_fails_on_each_figure_this_pass_removed(what):
    now, before, figures = REMOVED[what]
    assert SOURCE.count(now) == 1, (what, "premise: the text to replace is there, once")
    typed_back = SOURCE.replace(now, before)
    assert typed_back != SOURCE
    found = _typed(typed_back)
    for fig in figures:
        assert any(f"types {fig}" in line for line in found), (what, fig, found)


def test_452s_check_alone_could_not_see_the_durations_or_the_counts():
    """Why the pattern is widened: a span of years and a count are not percentages,
    dollars, multiples or the "duration ... N years" form #462 added."""
    for what in ("VGIT's and SCHP's durations", "the observation count", "the years of history"):
        now, before, _ = REMOVED[what]
        texts = {fn: dict(enumerate(literal_texts(SOURCE.replace(now, before), fn)))
                 for fn in FUNCTIONS}
        assert typed_figures(texts, {}, {}, figure=WIDE_FIGURE) == [], what


# ── the arguments are kept ───────────────────────────────────────────────────

_FI = {"betas": {"TERM": 0.654, "CREDIT": -0.010}, "t_stats": {"TERM": 35.23, "CREDIT": -0.56},
       "alpha_annual_bps": -64.0, "t_alpha": -1.02, "T": 290}
_DEV = {"betas": {"Mkt-RF": 0.98}, "t_stats": {"Mkt-RF": 40.0}, "alpha_annual_bps": 320.0,
        "t_alpha": 1.4, "T": 290, "sample_start": "2025-05-01", "sample_end": "2026-06-30"}


def test_the_fi_paragraph_points_to_the_duration_line_where_the_reader_finds_it():
    from src.factors import build_factor_prose
    page = build_factor_prose({}, fi_result=_FI)[0]
    pdf = build_factor_prose({}, fi_result=_FI, duration_line_location="in this report")[0]
    pointer = ("The positive TERM loading confirms the sleeve carries meaningful "
               "interest-rate duration, consistent with the fund durations behind the Fixed "
               "Income Effective Duration line ")
    assert pointer + "on the Performance page. " in page
    assert pointer + "in this report. " in pdf
    for text in (page, pdf):
        assert "5.5" not in text and "6.8" not in text and "-year" not in text
    # Each pointer resolves: the line is where it says.
    assert 'st.subheader("Fixed Income Effective Duration")' in (
        ROOT / "pages" / "2_Performance.py").read_text(encoding="utf-8")
    assert "<h3>Fixed Income Effective Duration</h3>" in (
        ROOT / "templates" / "quarterly_report.html").read_text(encoding="utf-8")


def test_the_fi_sleeves_mix_is_the_regressions_own_weights(monkeypatch):
    import src.factors as factors
    assert "The FI sleeve (VGIT 60% / SCHP 40%, 290 trading days)" in (
        factors.build_factor_prose({}, fi_result=_FI)[0])
    monkeypatch.setattr(factors, "_FI_WEIGHTS", {"VGIT": 0.7, "SCHP": 0.3})
    assert "The FI sleeve (VGIT 70% / SCHP 30%, 290 trading days)" in (
        factors.build_factor_prose({}, fi_result=_FI)[0])


def test_the_developed_paragraph_keeps_the_universe_mismatch_without_the_figures():
    from src.factors import build_factor_prose
    text = build_factor_prose({"developed_exus": _DEV})[0]
    assert ("and it reflects a universe mismatch, not skill. VEA tracks the FTSE Developed "
            "All Cap ex US Index, which classifies South Korea as Developed, while Ken "
            "French's Developed ex-US factor universe excludes Korea entirely. The return on "
            "Korean equities falls outside the FF Developed ex-US factor span and accumulates "
            "in the alpha term. The reported alpha is therefore return the Developed ex-US "
            "factors leave unexplained because of the universe mismatch, not risk-adjusted "
            "excess return.") in text
    for gone in ("3-4%", "of VEA's holdings", "95-98%", "calendar 2025", "Samsung", "SK Hynix",
                 "capex cycle"):
        assert gone not in text, gone
    # What is left with a digit is the regression's own output and its window.
    assert re.sub(r"[\d.,+-]+", "", text).count("%") == 0


def test_the_real_assets_sentence_names_no_split():
    from src.factors import build_factor_prose
    last = build_factor_prose({})[-1]
    assert last.endswith("Real assets are excluded: no liquid daily factor proxy set spans "
                         "REIT and commodity exposure simultaneously.")
    assert not re.search(r"\d", last), last


@pytest.mark.parametrize("sleeves", [("International Developed",),
                                     ("International Core", "International Quality",
                                      "International Large Value", "International Small Value")],
                         ids=["untilted", "tilted"])
def test_the_em_disclosure_keeps_its_reason_and_prints_no_number(monkeypatch, sleeves):
    import src.sleeve_config as sleeve_config
    from src.factors import em_disclosure
    monkeypatch.setattr(sleeve_config, "international_sleeves", lambda: sleeves)
    text = em_disclosure()
    assert not re.search(r"\d", text), text
    assert "China" not in text
    assert "cap-weighted broad EM exposure" in text and "IEMG" in text
    if len(sleeves) == 1:
        assert text == (
            "Ken French does not publish daily EM factor data, and the monthly history since "
            "this portfolio's inception is too short for stable inference. IEMG provides "
            "passive cap-weighted broad EM exposure. Factor decomposition for this sleeve "
            "will be added when the portfolio has accumulated enough monthly history.")


# ── the locked report ────────────────────────────────────────────────────────

def test_the_q2_report_prints_the_pointer_and_none_of_the_typed_figures(tmp_path, monkeypatch):
    from src import reports
    monkeypatch.setattr(reports, "_render_chart_to_png", lambda *a, **k: None)
    monkeypatch.setattr(reports, "_render_pdf", lambda html: html.encode("utf-8"))
    pin_today(monkeypatch)
    point_at_frozen_book(monkeypatch, tmp_path)
    try:
        html = reports.generate_quarterly_report_bytes("2026-03-31", "2026-06-30",
                                                       is_demo=True).decode("utf-8")
    finally:
        unpin_leftovers()
    flat = " ".join(html.split())
    assert "Fixed Income Effective Duration line in this report." in flat
    assert "<h3>Fixed Income Effective Duration</h3>" in flat, "the line it points to"
    assert str(escape("which classifies South Korea as Developed, while Ken French's "
                      "Developed ex-US factor universe excludes Korea entirely.")) in flat
    assert "Real assets are excluded:" in flat
    assert "The FI sleeve (VGIT 60% / SCHP 40%, " in flat
    for gone in ("5.5-year", "6.8-year", "3-4% of VEA", "95-98%", "Samsung",
                 "VNQ 60%, DBC 40%", "27% China"):
        assert gone not in flat, gone
