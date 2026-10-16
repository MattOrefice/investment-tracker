"""The demo PDF's Asset Evaluation and Macro claims, as the owner decided them on October
16, 2026 (#625). Each was checked against the code first; #625 records which held.

  * The CONCLUSION box printed its markdown literally: "**" around the verdict and " - "
    before each trigger, its line breaks collapsed. The PDF prints it as paragraphs, bold
    runs and a list now (reports.markdown_blocks), read here off the rendered template.
  * Its opening said the Sharpe improvement was "almost entirely attributable to its
    exceptional 2020–2021 bull market return", which nothing computes, and that spot ETFs
    "remove the custody and counterparty frictions", which IBIT's 10-K does not say. The
    ETF sentence keeps what the 10-K states, cited with its filing date.
  * The tax line keeps Notice 2014-21's property treatment, with its bulletin's date, and
    cites the IRS's dated FAQ for the holding period. "That is unfavorable vs. ETFs, which
    qualify for in-kind creation/redemption" went.
  * "Co-movement spikes during stress, when a hedge is most valuable" went: no figure the
    report prints shows it.
  * The CAPE implied-return formula and its figure went (tests/test_macro.py pins that).
  * "Elevated US equity valuations (CAPE 99th) support the SAA's diversification across
    non-US equity and real asset sleeves" went: the elevated band prints no stance.
"""
from __future__ import annotations

import re
from unittest.mock import patch

import pandas as pd
import pytest
from jinja2 import Environment, FileSystemLoader

import src.asset_evaluation as ae
import src.reports as reports
from tests.test_asset_eval_disposition import _Blank, _ctx, _render_section, _text

_DATE = re.compile(r"(January|February|March|April|May|June|July|August|September|October|"
                   r"November|December) \d{1,2}, 20\d\d")


def _rolling() -> pd.Series:
    idx = pd.date_range("2018-03-01", "2026-07-16", freq="D")
    return pd.Series([0.0 if d.year < 2020 else 0.4 for d in idx], index=idx)


# ── the conclusion box renders its bold and its list ──────────────────────────

def test_markdown_blocks_reads_paragraphs_bold_runs_and_a_list():
    blocks = reports.markdown_blocks("Lead. **Bold verdict.**\n\nIntro:\n- one **two**\n- three\n")
    assert blocks == [
        {"kind": "p", "runs": [(False, "Lead. "), (True, "Bold verdict.")]},
        {"kind": "p", "runs": [(False, "Intro:")]},
        {"kind": "ul", "items": [[(False, "one "), (True, "two")], [(False, "three")]]},
    ]
    with pytest.raises(ValueError):
        reports.markdown_blocks("an **unclosed bold")


def _conclusion_div(html: str) -> str:
    m = re.search(r'<p class="bf-comm-title">Conclusion</p>(.*?)</div>', html, re.S)
    assert m, "the conclusion box is not in the section"
    return m.group(1)


def test_the_conclusion_box_renders_bold_and_a_list_not_their_markdown():
    """Read off the real template's rendered text: no "**", no " - " item, the verdict in
    <strong>, and each trigger its own <li>."""
    text = ae.conclusion(_rolling())
    section = _render_section(_ctx(disposition="computed", conclusion=text,
                                   conclusion_blocks=reports.markdown_blocks(text)))
    box = _conclusion_div(section)
    rendered = _text(box)
    assert "**" not in rendered
    assert not re.search(r"(^|\s)- ", rendered), rendered
    assert "<strong>As of July 16, 2026, Bitcoin does not belong in this portfolio&#39;s " \
           "SAA.</strong>" in box
    assert box.count("<li>") == 3 == text.count("\n- ")
    assert "The framework would re-evaluate inclusion" in rendered


def test_the_conclusion_box_escapes_what_it_prints():
    section = _render_section(_ctx(disposition="computed", conclusion="x",
                                   conclusion_blocks=reports.markdown_blocks(
                                       "**<b>bold?</b>** and <i>")))
    box = _conclusion_div(section)
    assert "<b>" not in box and "<i>" not in box
    assert "<strong>&lt;b&gt;bold?&lt;/b&gt;</strong> and &lt;i&gt;" in box


# ── each claim cited to its source, or gone ───────────────────────────────────

def test_the_opening_keeps_only_what_ibits_10k_states():
    text = ae.conclusion(_rolling())
    assert ('"intended to constitute a simple means of making an investment similar to an '
            'investment in bitcoin rather than by acquiring, holding and trading bitcoin '
            'directly on a peer-to-peer or other basis or via a digital asset platform"') in text
    assert ("(iShares Bitcoin Trust ETF, annual report on Form 10-K for 2025, filed "
            "February 27, 2026)") in text
    for gone in ("almost entirely attributable", "2020–2021 bull market", "FBTC",
                 "remove the custody and counterparty", "frictions", "not operational",
                 "sample-period Sharpe improvement is real"):
        assert gone not in text, gone


def test_the_tax_line_says_what_its_irs_sources_say_with_their_dates():
    line = ae.BITCOIN_TAX_LINE
    assert ("the IRS treats bitcoin as property (Notice 2014-21, Internal Revenue Bulletin "
            "2014-16, April 14, 2014)") in line
    assert ("short-term if the bitcoin was held one year or less and long-term if it was "
            "held more than one year (the IRS's \"Frequently asked questions on virtual "
            "currency transactions\", last reviewed or updated June 30, 2026)") in line
    assert len(_DATE.findall(line)) == 2
    for gone in ("in-kind", "unfavorable vs. ETFs", "ordinary rates", "within 12 months"):
        assert gone not in line, gone


def test_the_equity_like_bullet_states_its_figure_and_no_stress_claim():
    bullet = ae.equity_like_argument(0.5)
    assert bullet == ("Equity-like correlation with US Large Core (0.50 over the full "
                      "sample, 2018 to present)")
    assert "stress" not in bullet and "hedge" not in bullet


# ── Macro Context: the elevated band prints no stance ─────────────────────────

def _macro(cape_val: float, cape_series: pd.Series) -> dict:
    with patch("src.reports.current_cape", return_value=cape_val), \
         patch("src.reports.get_cape_series", return_value=cape_series), \
         patch("src.reports.get_series", side_effect=RuntimeError("mocked")):
        return reports._build_macro_section()


def _macro_paragraph(macro: dict) -> str:
    env = Environment(loader=FileSystemLoader(str(reports.TEMPLATES_DIR)),
                      autoescape=True, undefined=_Blank)
    html = env.get_template("quarterly_report.html").render(macro=macro, css_content="")
    m = re.search(r"<h2[^>]*>Macro Context</h2>.*?</section>", html, re.S)
    assert m, "the Macro Context section is not in the template"
    return _text(m.group(0))


def test_an_elevated_cape_renders_no_stance_and_no_implied_return():
    macro = _macro(36.0, pd.Series([10.0, 15.0, 20.0, 25.0, 30.0, 36.0]))
    assert macro["cape"]["regime"] == "Elevated", "premise: the elevated band"
    assert "regime_action" not in macro["cape"], "no band carries a stance since #627"
    assert "implied_return" not in macro["cape"]
    text = _macro_paragraph(macro)
    for gone in ("US equity valuations", "support the SAA", "diversification",
                 "Implied 10Y real return", "ln(CAPE"):
        assert gone not in text, gone
    assert "Indicators are reference data; tactical positioning remains anchored to SAA " \
           "targets." in text


def test_a_below_average_cape_renders_no_stance_either():
    """MIGRATED at #627 (decided by the owner on October 16, 2026). This was #625's
    control: a below-average CAPE still rendered its stance through the same template.
    Every stance went, so the paragraph keeps only its policy sentence at every band."""
    macro = _macro(10.0, pd.Series([float(v) for v in range(10, 41)]))
    assert macro["cape"]["regime"] == "Below-average", "premise: the below-average band"
    text = _macro_paragraph(macro)
    for gone in ("US equity valuations", "support increased US equity exposure",
                 "modest diversification", "no strong tilt signal"):
        assert gone not in text, gone
    assert "Indicators are reference data; tactical positioning remains anchored to SAA " \
           "targets." in text
