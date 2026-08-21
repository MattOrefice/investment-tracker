"""The case-against drawdown bullet must name WHOSE drawdown it is. #276.

The hardcoded ">80%" this replaced was **true** — -81.5% over the report's own
sample, measurable from a series already in scope three blocks above. So the defect
was never staleness, and a PR that only derived the figure would have fixed a
correct number while leaving the thing that misleads.

What misleads is adjacency. `templates/quarterly_report.html` renders
`<h3>Drawdown Sensitivity</h3>` — whose Max DD column is the PORTFOLIO's, and which
IMPROVES as BTC rises (-31.3% -> -29.8%) — and then, as the very next `<h3>`,
`Decision Framework`, whose bullet asserted a figure four times larger. Neither said
whose. These tests pin the naming, not the arithmetic.

Two forms, because the two halves have different failure surfaces: btc_ret is
guaranteed non-empty past the loader guard, so Bitcoin's own drawdown always
renders, while the portfolio contrast comes from 5h and can fail alone. The reduced
form declares the contrast ABSENT rather than dropping it — a contrast that quietly
vanishes reads as no contrast, i.e. as though the allocation cost nothing.
"""
import pathlib
import re

import pytest

import src.reports as rp

TEMPLATE = (pathlib.Path(rp.__file__).resolve().parent.parent
            / "templates" / "quarterly_report.html")

# The 2026-08-21 book, kept as a realistic shape rather than as pinned values —
# no assertion below depends on these numbers being current.
PORT = {"max_dd": -0.313, "alloc_top": "10.0%", "mdd22_lo": -0.233, "mdd22_hi": -0.261}
BTC_MDD = -0.8153
BTC_2022 = "-66.9%"
START = "2018-01-01"


def _full():
    return rp._drawdown_argument(BTC_MDD, BTC_2022, PORT, START)


def _reduced():
    return rp._drawdown_argument(BTC_MDD, BTC_2022, {}, START)


# ── the contract: both subjects named ─────────────────────────────────────────

def test_the_full_form_names_bitcoin_as_the_subject_of_its_own_figure():
    """Not "maximum drawdown is -81.5%" — whose."""
    text = _full()
    assert "Bitcoin's own maximum drawdown is -81.5%" in text


def test_the_full_form_names_the_TABLE_figure_as_the_portfolios():
    """THE CLAUSE THIS PR EXISTS FOR. Deriving the figure without this sentence
    would leave a reader with two drawdown numbers, adjacent, differing 4x, and
    nothing saying they measure different things."""
    text = _full()
    assert "-31.3% in the table above, which is the portfolio's" in text


def test_the_full_form_states_the_window():
    """A drawdown with no window is not checkable. -81.5% is true over
    2018-01-01–present and -83.4% over full history; both are 'exceeding 80%'."""
    assert f"over {START}–present" in _full()


def test_the_full_form_sizes_what_the_allocation_did_to_2022():
    """The original second clause asserted 2022 offered no diversification benefit.
    The sweep measures exactly that, one table up, and the bullet now says by how
    much instead of leaving it qualitative."""
    text = _full()
    assert "-23.3% at 0% allocation to -26.1% at 10.0%" in text


# ── two states, both rendered ─────────────────────────────────────────────────

def test_the_reduced_form_fires_when_the_sweep_is_unavailable():
    """THE TWO-STATES CHECK. A message test alone cannot see this half: it would
    pass on a function that returned the reduced string unconditionally."""
    text = _reduced()
    assert "not available this run" in text
    assert "the drawdown sweep did not produce data" in text


def test_the_reduced_form_declares_the_contrast_absent_not_zero():
    """LOAD-BEARING. Without it the missing comparison reads as no comparison —
    as though a BTC allocation had cost nothing in 2022, which is the opposite of
    what the full form reports."""
    assert "Absent, not zero" in _reduced()


def test_the_two_forms_are_actually_different():
    """Assert-it-mutated, applied to the harness itself: every test above is
    worthless if both branches return the same string."""
    assert _full() != _reduced()


def test_the_reduced_form_keeps_bitcoins_own_figure():
    """btc_ret is guaranteed non-empty past the loader guard, so this half never
    degrades — losing it here would be a fabricated failure."""
    assert "Bitcoin's own maximum drawdown is -81.5%" in _reduced()


def test_the_reduced_form_makes_no_portfolio_claim():
    """It must not name a portfolio figure it does not have."""
    text = _reduced()
    assert "-31.3%" not in text and "-26.1%" not in text


# ── derived, not literal ──────────────────────────────────────────────────────

@pytest.mark.parametrize("mdd,shown", [(-0.5, "-50.0%"), (-0.925, "-92.5%")])
def test_the_figure_tracks_the_data_rather_than_a_literal(mdd, shown):
    """THE MUTATION GUARD, aimed at the RENDERED SENTENCE rather than at the
    computation — the computation was already correct, and a mutant restoring a
    hardcoded number is the regression that matters. A literal '-81.5%' survives
    any assertion that merely looks for '-81.5%'; it dies here."""
    text = rp._drawdown_argument(mdd, BTC_2022, PORT, START)
    assert shown in text
    assert "-81.5%" not in text


def test_the_portfolio_figures_track_the_sweep_too():
    """Both sides of the comparison must move, not just Bitcoin's."""
    other = dict(PORT, max_dd=-0.44, mdd22_lo=-0.11, mdd22_hi=-0.19)
    text = rp._drawdown_argument(BTC_MDD, BTC_2022, other, START)
    assert "-44.0% in the table above" in text
    assert "-11.0% at 0% allocation to -19.0%" in text
    assert "-31.3%" not in text


def test_max_drawdown_is_nan_on_an_empty_series_not_zero():
    """A zero drawdown is a real and very different claim from an unmeasured one."""
    import pandas as pd
    assert pd.isna(rp._max_drawdown(pd.Series(dtype=float)))


def test_max_drawdown_is_signed():
    """The table's figures are signed; a magnitude here would set two numbers
    against each other under opposite conventions."""
    import pandas as pd
    assert rp._max_drawdown(pd.Series([0.5, -0.5, -0.5])) < 0


# ── the other three entries ───────────────────────────────────────────────────

def test_the_editorial_argument_is_labelled_as_standing():
    """Unlike the drawdown, it is not a measurement withheld — it is not measurable
    and should not become conditional. Saying so is what lets a reader tell the two
    kinds apart, which is the whole asymmetry #276 named."""
    src = pathlib.Path(rp.__file__).read_text(encoding="utf-8")
    assert "Standing argument, not a measurement" in src
    assert "independent of every figure above" in src


def test_the_commodity_tax_string_is_frozen_and_says_why():
    """Deliberately NOT rewritten: it is the third in-repo copy of the commodity
    tax-character claim and #278 may make it derive from a rate vocabulary.
    Editing it here first mints a fourth copy, which is how this family reproduces.
    The comment must NAME the issue so the next reader meets the reason rather than
    an unexplained exception."""
    src = pathlib.Path(rp.__file__).read_text(encoding="utf-8")
    assert ("Commodity tax treatment: short-term ordinary income / long-term "
            "capital gains, ") in src
    i = src.index("Commodity tax treatment:")
    assert "#278" in src[i - 700:i], (
        "the frozen string carries no comment naming #278 — without it the "
        "un-derived entry reads as an oversight")


def test_the_unconditional_count_is_still_exactly_three():
    """#276's pin, held. The drawdown entry renders in one of two forms but always
    renders, so deriving it did not make the case-against conditional."""
    src = pathlib.Path(rp.__file__).read_text(encoding="utf-8")
    i = src.index("args_against.extend([")
    block = src[i:src.index("])", i)]
    assert block.count('"Commodity tax treatment') == 1
    assert block.count('"Standing argument') == 1


# ── the helper's output actually REACHES args_against ─────────────────────────
#
# Presence is not use. Every test above exercises `_drawdown_argument` directly and
# would pass in full on a builder that never called it — the same shape as a source
# pin satisfied by a helper's `def`. These two run the real builder.
#
# Stubbed, not real: a live build costs ~78s and the data load is ~2s of it. The
# loader stays REAL because btc_ret's guaranteed non-emptiness is the premise the
# two-form design rests on — stubbing it would assume away what is being relied on.

_STUBS = {
    "build_univariate_table":           lambda: __import__("pandas").DataFrame(),
    "compute_full_sample_correlations": lambda: __import__("pandas").Series(dtype=float),
    "compute_rolling_correlation":      lambda: __import__("pandas").Series(dtype=float),
    "compute_mv_analysis":              lambda: {},
    "compute_marginal_sharpe_curve":    lambda: __import__("pandas").DataFrame(),
}


def _build(monkeypatch, sweep):
    import src.asset_evaluation as ae
    for name, make in _STUBS.items():
        monkeypatch.setattr(ae, name, (lambda m: (lambda *a, **k: m()))(make))
    monkeypatch.setattr(ae, "compute_drawdown_sensitivity", lambda *a, **k: sweep)
    return rp._build_asset_eval_section()


def _sweep(max_dd, lo, hi):
    import pandas as pd
    return pd.DataFrame([
        {"BTC Alloc": "0.0%", "CAGR": 0.10, "Max DD": max_dd, "Sharpe": 0.35,
         "2022 MDD": lo},
        {"BTC Alloc": "10.0%", "CAGR": 0.12, "Max DD": max_dd + 0.015,
         "Sharpe": 0.45, "2022 MDD": hi},
    ])


def test_the_builder_renders_the_full_form_from_the_sweeps_own_numbers(monkeypatch):
    """Synthetic sweep figures chosen so they cannot coincide with the real book —
    if they appear in the bullet, `_port` was populated from the sweep and handed to
    the helper, which no direct call to the helper can demonstrate."""
    out = _build(monkeypatch, _sweep(-0.44, -0.11, -0.19))
    bullet = [a for a in out["args_against"] if "Bitcoin's own maximum drawdown" in a]
    assert len(bullet) == 1, f"expected one drawdown bullet, got {bullet}"
    assert "-44.0% in the table above, which is the portfolio's" in bullet[0]
    assert "-11.0% at 0% allocation to -19.0% at 10.0%" in bullet[0]
    assert "drawdown" not in out["unavailable"]


def test_the_builder_renders_the_reduced_form_when_the_sweep_is_empty(monkeypatch):
    """THE TWO-STATES CHECK, end to end. An empty sweep is not a raise, so nothing
    lands in `unavailable` — and the bullet must still appear, in reduced form.
    A builder that dropped it here would shrink the case against silently."""
    import pandas as pd
    out = _build(monkeypatch, pd.DataFrame())
    bullet = [a for a in out["args_against"] if "Bitcoin's own maximum drawdown" in a]
    assert len(bullet) == 1
    assert "Absent, not zero" in bullet[0]
    assert "in the table above" not in bullet[0]


def test_the_two_builder_states_render_different_bullets(monkeypatch):
    """Assert-it-mutated at the builder level: both tests above would pass if the
    builder emitted one fixed string regardless of the sweep."""
    import pandas as pd
    full = _build(monkeypatch, _sweep(-0.44, -0.11, -0.19))["args_against"]
    monkeypatch.undo()
    reduced = _build(monkeypatch, pd.DataFrame())["args_against"]
    fb = [a for a in full if "Bitcoin's own" in a][0]
    rb = [a for a in reduced if "Bitcoin's own" in a][0]
    assert fb != rb
    assert len(full) == len(reduced), (
        "the case against changed LENGTH between the two states — the reduced form "
        "exists so that it does not")


def test_the_case_against_is_never_empty_in_either_state(monkeypatch):
    import pandas as pd
    for sweep in (_sweep(-0.44, -0.11, -0.19), pd.DataFrame()):
        monkeypatch.undo()
        assert len(_build(monkeypatch, sweep)["args_against"]) == 3


# ── the template: subject named at the point of reading ───────────────────────

def test_the_table_headers_name_the_portfolio_as_the_subject():
    """THE CHEAPEST HALF OF THIS FIX, and it was found while writing the expensive
    one. Two <th> cells: read as 'Max DD' against a bullet saying '>80%' the pair
    looks like a contradiction; read as 'Portfolio Max DD' it is two quantities."""
    html = TEMPLATE.read_text(encoding="utf-8")
    assert ">Portfolio Max DD<" in html
    assert ">Portfolio 2022 MDD<" in html


def test_the_caption_does_not_call_the_measured_column_a_scenario():
    """It read '2022 stress scenario: equities -20%, bonds -15%, BTC -65%
    simultaneously' beside a column computed from ACTUAL 2022 returns. Calling
    measured history a scenario invites reading it as hypothetical — the inverse of
    the '>80%' defect. Comments stripped first: the {# #} block above the caption
    quotes the retired wording, and a raw substring search would find its own
    documentation."""
    html = TEMPLATE.read_text(encoding="utf-8")
    body = re.sub(r"\{#.*?#\}", "", html, flags=re.S)
    i = body.index("realized history, not a")
    caption = body[i - 400:i + 700]
    assert "stress scenario" not in caption
    assert "realized history, not a" in caption
    assert "hypothetical shock" in caption


def test_the_caption_drops_the_unverified_sleeve_figures():
    """-65% was a rounded restatement of a measured -66.9%; the equity and bond
    figures were never verified and are not what the column computes. Dropped
    rather than derived — accurate sleeve numbers would still describe something
    else."""
    html = TEMPLATE.read_text(encoding="utf-8")
    body = re.sub(r"\{#.*?#\}", "", html, flags=re.S)
    assert "equities &minus;20%" not in body
    assert "bonds &minus;15%" not in body


def test_the_caption_templates_bitcoins_figure_rather_than_stating_one():
    html = TEMPLATE.read_text(encoding="utf-8")
    body = re.sub(r"\{#.*?#\}", "", html, flags=re.S)
    assert "{{ asset_eval.btc_2022_mdd }}" in body
    assert "&minus;65%" not in body
