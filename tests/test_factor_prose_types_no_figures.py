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

Since #481 the check also covers factors.build_factor_methodology_notes, which the
Factor Profile page prints, and factors._term_reading, the FI paragraph's sentence
about the TERM loading. The notes typed two sets of sleeve weights that disagreed
with the regression's own, the real-assets split again, two EM counts, and two
classification claims read from no page. One exception is listed, with its reason:
the momentum vintage note's worked example (UMD_EXAMPLE below).

The check covers these four functions and no other prose in src/factors.py.
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
FUNCTIONS = ("build_factor_prose", "em_disclosure", "_term_reading",
             "build_factor_methodology_notes")

# The one figure the scan lets a function type, and why. The momentum vintage note
# cites one refresh as evidence for the order of its two causes: the August 2026
# refresh, where the added quarter moved the loadings far more than a source rewrite
# of most of the history did. Those are dated measurements of that refresh, not
# figures any code holds, and tests/test_factors.py pins them so the order cannot be
# re-inverted. #481's decision listed the notes' other figures and not this one, so
# it stays as written until it is decided.
UMD_EXAMPLE = "revision touching 70% of daily UMD history"
ALLOWED = {"build_factor_methodology_notes": [UMD_EXAMPLE]}

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


def _typed(source: str, allowed: "dict | None" = None) -> "list[str]":
    """Every figure the functions type, by #452's own function, with no figure held
    by a book to match and the one exception ALLOWED lists."""
    texts = {fn: dict(enumerate(literal_texts(source, fn))) for fn in FUNCTIONS}
    return typed_figures(texts, {}, ALLOWED if allowed is None else allowed,
                         figure=FACTOR_FIGURE, label="factor prose")


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
    notes = " ".join(literal_texts(SOURCE, "build_factor_methodology_notes"))
    for phrase in ("Universe mismatch, Developed sleeve", "FI sleeve return: {}",
                   "at the regression's fixed weights: {}", UMD_EXAMPLE,
                   "Real assets remain excluded"):
        assert phrase in notes, phrase
    assert "NO TYPED FIGURES" not in notes, "the docstring is not prose"
    term = " ".join(literal_texts(SOURCE, "_term_reading"))
    assert "The TERM loading is {} and {}" in term


def test_the_factor_prose_types_no_figure():
    assert _typed(SOURCE) == []


# Each figure this pass removed: (the text now in the source, the text it replaced).
REMOVED = {
    # The sentence moved into _term_reading with #481; the figures it replaced are the same.
    "VGIT's and SCHP's durations": (
        "f\"the fund durations behind {line}.\")",
        "\"VGIT's ~5.5-year effective duration and SCHP's ~6.8-year duration.\")",
        ["~5.5-year", "~6.8-year"]),
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
    # #481: the methodology notes.
    "the notes' US weights": (
        "f\"return of its funds at the regression's fixed weights: {us_mix}. \"",
        "\"return of its funds, weighted VOO 35.6%, SPHQ 31.1%, VTV 17.8%, and AVUV \"\n"
        "        \"15.6%, proportional to the sleeve targets of 16/14/8/7%. \"",
        ["35.6%", "15.6%", "7%"]),
    "the notes' FI weights": (
        "f\"FI sleeve return: {fi_mix}, the regression's fixed weights. \"",
        "\"FI sleeve return: value-weighted VGIT (60%) + SCHP (40%), proportional to \"\n"
        "            \"9%/6% SAA targets. \"", ["60%", "40%", "9%", "6%"]),
    "the notes' Real Assets split": (
        "\"Real assets remain excluded: no liquid",
        "\"Real assets (VNQ 60%, DBC 40%) remain excluded: no liquid", ["60%", "40%"]),
    "the notes' EM counts": (
        "\"inference. EM factor decomposition will be added when the portfolio has \"",
        "\"inference: approximately 12 observations. EM factor decomposition will be \"\n"
        "        \"added at 3+ years of history, when the portfolio has \"",
        ["12observations", "3+years"]),
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
        assert typed_figures(texts, {}, ALLOWED, figure=WIDE_FIGURE) == [], what


# ── the arguments are kept ───────────────────────────────────────────────────

_FI = {"betas": {"TERM": 0.654, "CREDIT": -0.010}, "t_stats": {"TERM": 35.23, "CREDIT": -0.56},
       "alpha_annual_bps": -64.0, "t_alpha": -1.02, "T": 290}
_DEV = {"betas": {"Mkt-RF": 0.98}, "t_stats": {"Mkt-RF": 40.0}, "alpha_annual_bps": 320.0,
        "t_alpha": 1.4, "T": 290, "sample_start": "2025-05-01", "sample_end": "2026-06-30"}


def test_the_fi_paragraph_points_to_the_duration_line_where_the_reader_finds_it():
    from src.factors import build_factor_prose
    page = build_factor_prose({}, fi_result=_FI)[0]
    pdf = build_factor_prose({}, fi_result=_FI, duration_line_location="in this report")[0]
    # Since #481 the sentence states the sign and significance the regression found.
    pointer = ("The TERM loading is positive and statistically significant at the 1% "
               "level: the sleeve carries interest-rate duration, consistent with the "
               "fund durations behind the Fixed Income Effective Duration line ")
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
    # What is left with a digit is the regression's own output and its window, and
    # the significance level its own t-statistics earn (prose_helpers).
    from src.prose_helpers import significance_label
    bare = text.replace(significance_label(40.0), "").replace(significance_label(1.4), "")
    assert re.sub(r"[\d.,+-]+", "", bare).count("%") == 0


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


# ── #481: the methodology notes, and the sentences that follow the loading ────

def test_the_one_listed_exception_is_the_momentum_notes_example_and_nothing_else():
    """With no exception allowed the scan finds exactly one figure: the worked example
    in the momentum vintage note. So nothing else in the four functions is typed, and
    the exception cannot widen without this failing."""
    found = _typed(SOURCE, allowed={})
    assert len(found) == 1, found
    assert "build_factor_methodology_notes" in found[0] and "types 70%" in found[0]
    assert UMD_EXAMPLE in " ".join(literal_texts(SOURCE, "build_factor_methodology_notes"))


_NOTES_FI = dict(_FI, nw_lags=5, sample_start="2025-05-01", sample_end="2026-06-30")
_NOTES_DEV = dict(_DEV, nw_lags=5)


def _note(start: str, results=None, fi=_NOTES_FI) -> str:
    from src.factors import build_factor_methodology_notes
    notes = [n for n in build_factor_methodology_notes(results or {}, fi_result=fi)
             if n.startswith(start)]
    assert len(notes) == 1, (start, len(notes))
    return notes[0]


def test_the_notes_us_weights_are_the_regressions_own(monkeypatch):
    import src.factors as factors
    weights = factors._SLEEVES["us"]["weights"]
    assert weights == {t: w / sum(factors._SAA_US.values())
                       for t, w in factors._SAA_US.items()}, (
        "premise: the regression weights the US sleeve by _SAA_US")
    parts = [f"{t} {w * 100:.1f}%" for t, w in weights.items()]
    mix = ", ".join(parts[:-1]) + " and " + parts[-1]
    assert mix == "VOO 34.7%, SPHQ 30.6%, VTV 18.4% and AVUV 16.3%", mix
    assert _note("Sleeve return series:") == (
        "Sleeve return series: the US equity sleeve return is the weighted daily total "
        f"return of its funds at the regression's fixed weights: {mix}. Weights are held "
        "constant. The Developed sleeve is VEA's daily adjusted-close return.")
    # The contrast: the note follows the weights the regression applies.
    monkeypatch.setitem(factors._SLEEVES["us"], "weights", {"VOO": 0.5, "AVUV": 0.5})
    assert "fixed weights: VOO 50.0% and AVUV 50.0%. " in _note("Sleeve return series:")


def test_the_notes_fi_weights_are_the_regressions_own(monkeypatch):
    import src.factors as factors
    assert ("FI sleeve return: VGIT (60%) + SCHP (40%), the regression's fixed weights. "
            in _note("FI sleeve model:"))
    monkeypatch.setattr(factors, "_FI_WEIGHTS", {"VGIT": 0.7, "SCHP": 0.3})
    assert ("FI sleeve return: VGIT (70%) + SCHP (30%), the regression's fixed weights. "
            in _note("FI sleeve model:"))


def test_the_notes_print_none_of_the_figures_that_disagreed_or_that_nothing_held():
    from src.factors import build_factor_methodology_notes
    for fi in (_NOTES_FI, None):
        text = " ".join(build_factor_methodology_notes({"developed_exus": _NOTES_DEV},
                                                       fi_result=fi))
        for gone in ("35.6%", "31.1%", "17.8%", "15.6%", "16/14/8/7", "9%/6%",
                     "VNQ 60%", "DBC 40%", "12 observations", "3+ years",
                     "treated as EM", "different Israel categorization",
                     "SAA target proportions", "Phase 1"):
            assert gone not in text, (gone, fi is None)
        assert "Real assets remain excluded" in text


def test_the_classification_note_cites_the_two_pages_as_they_were_read():
    import src.factors as factors
    assert _note("Universe mismatch, Developed sleeve:", {"developed_exus": _NOTES_DEV}) == (
        "Universe mismatch, Developed sleeve: VEA tracks the FTSE Developed All Cap ex US "
        "Index, and FTSE classifies both South Korea and Israel as Developed (\"Markets "
        "classified under the FTSE Equity Country Classification Scheme\", as of April 7, "
        "2026). Neither is among the countries of Ken French's Developed ex-US factors "
        "(the country table in his \"Description of Fama/French 5 Factors for Developed "
        "Markets\", read October 4, 2026). The universe difference contributes to "
        "unexplained variance and inflates the Developed-sleeve alpha estimate. A future "
        "phase may address this by reweighting VEA's holdings to match Ken French's "
        "universe before regressing, or by using a custom factor set aligned to FTSE "
        "Developed All Cap ex US.")
    assert "April 7, 2026" in factors._FTSE_CLASSIFICATION
    assert "read October 4, 2026" in factors._FRENCH_COUNTRY_TABLE


def test_the_em_note_keeps_its_reason_and_prints_no_count():
    note = _note("EM sleeve exclusion:")
    assert note == (
        "EM sleeve exclusion: Ken French does not publish daily EM factor data, and the "
        "monthly history since this portfolio's inception is too short for stable "
        "inference. EM factor decomposition will be added when the portfolio has "
        "accumulated enough monthly history.")
    assert not re.search(r"\d", note)


def test_the_pages_own_bullet_no_longer_types_a_span_of_years():
    page = " ".join((ROOT / "pages" / "4_Factor_Profile.py").read_text(
        encoding="utf-8").split())
    assert "≤2 years" not in page and "2 years of history" not in page
    assert ('"CIs apply to the residual (regression intercept) only. Wide CIs show that '
            'the " "residual estimate is not yet stable;') in page


@pytest.mark.parametrize("beta, t, expected", [
    (0.654, 35.23,
     "The TERM loading is positive and statistically significant at the 1% level: the "
     "sleeve carries interest-rate duration, consistent with the fund durations behind "
     "the Fixed Income Effective Duration line in this report."),
    (-0.300, -4.00,
     "The TERM loading is negative and statistically significant at the 1% level: this "
     "regression does not show the interest-rate duration that the fund durations behind "
     "the Fixed Income Effective Duration line in this report imply."),
    (0.050, 0.80,
     "The TERM loading is positive and not statistically distinguishable from zero, so "
     "this regression does not by itself show interest-rate duration; the fund durations "
     "are behind the Fixed Income Effective Duration line in this report."),
    (-0.020, -0.50,
     "The TERM loading is negative and not statistically distinguishable from zero, so "
     "this regression does not by itself show interest-rate duration; the fund durations "
     "are behind the Fixed Income Effective Duration line in this report."),
], ids=["positive-significant", "negative-significant", "positive-indistinct",
        "negative-indistinct"])
def test_the_term_sentence_follows_the_loading_the_regression_found(beta, t, expected):
    """It said "The positive TERM loading confirms the sleeve carries meaningful
    interest-rate duration" whatever the loading was."""
    from src.factors import build_factor_prose
    fi = dict(_FI, betas={"TERM": beta, "CREDIT": -0.010},
              t_stats={"TERM": t, "CREDIT": -0.56})
    text = build_factor_prose({}, fi_result=fi, duration_line_location="in this report")[0]
    assert f" {expected} Annualized alpha of " in text
    for gone in ("confirms", "meaningful"):
        assert gone not in text, gone


@pytest.mark.parametrize("beta, t, expected", [
    (0.98, 40.0, "loads on Mkt-RF_dev at 0.98 (t = 40.00), a positive loading that is "
                 "statistically significant at the 1% level. The +320 bps"),
    (0.10, 1.0, "loads on Mkt-RF_dev at 0.10 (t = 1.00), a positive loading that is not "
                "statistically distinguishable from zero. The +320 bps"),
    (-0.40, -2.1, "loads on Mkt-RF_dev at -0.40 (t = -2.10), a negative loading that is "
                  "statistically significant at the 5% level. The +320 bps"),
], ids=["positive-significant", "positive-indistinct", "negative-significant"])
def test_the_developed_market_sentence_follows_the_loading_too(beta, t, expected):
    """It said the loading was "within the expected range for a passive cap-weighted
    developed-markets ETF" whatever it was, and nothing defines such a range."""
    from src.factors import build_factor_prose
    dev = dict(_DEV, betas={"Mkt-RF": beta}, t_stats={"Mkt-RF": t})
    text = build_factor_prose({"developed_exus": dev})[0]
    assert expected in text
    assert "expected range" not in text


def test_neither_sentence_survives_in_the_prose_the_functions_spell_out():
    """Read from the functions' literals: a docstring may quote what the text said."""
    prose = " ".join(t for fn in ("build_factor_prose", "_term_reading")
                     for t in literal_texts(SOURCE, fn))
    for gone in ("expected range", "TERM loading confirms", "meaningful interest-rate"):
        assert gone not in prose, gone
