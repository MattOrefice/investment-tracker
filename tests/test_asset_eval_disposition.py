"""Three states reach the Asset Evaluation section and each says its own thing.

#250. Before this, all three rendered one sentence — "Asset evaluation data
unavailable — requires market data access" — which was a guess at a cause in one
state and false in another. Measured by rendering each in memory; the source
reading alone got #260's equivalent wrong, so these tests render.

  no_data   the input return series are empty
  failed    a loader raised; the exception is the only thing that says which
  partial   loads succeeded, the univariate table did not — and the correlation,
            rolling-correlation and mean-variance analyses were being DISCARDED,
            because the gate keyed on uni_rows

The template is rendered directly with a synthetic context rather than through a
full PDF build: the gate is what these tests are about, and a build costs minutes.
The end-to-end path is covered by test_disposition_is_assigned_* below, which call
the real builder.
"""
import re

import pandas as pd
import pytest
from jinja2 import ChainableUndefined, Environment, FileSystemLoader

import src.reports as reports

SAMPLE_START = "2018-01-01"

# The sentence this change deletes. It named a cause the code does not know: in the
# `failed` state the exception may have nothing to do with market data access.
OLD = "requires market data access"

_BASE = {
    "sample_start": SAMPLE_START, "uni_rows": [], "corr_chart_b64": None,
    "corr_prose": None, "rolling_chart_b64": None, "rolling_prose": None,
    "con_rows": [], "sharpe_con_no": None, "sharpe_con_with": None,
    "delta_bps_con": None, "msc_chart_b64": None, "dd_rows": [],
    "args_for": [], "args_against": [], "conclusion": "x",
    "disposition": "computed", "failure_reason": None,
}


def _ctx(**over):
    d = dict(_BASE)
    d.update(over)
    return d


class _Blank(ChainableUndefined):
    """Every variable this test does not supply behaves as empty/falsy.

    The template is the whole report and expects ~20 context objects; supplying
    them all would make these tests about the fixture rather than the gate. This
    differs from the production environment ONLY in undefined handling — same
    loader, same template file, same autoescape — so the asset-eval section
    renders exactly as it does in a real build, which the end-to-end renders
    recorded in #250 confirm.
    """

    def __iter__(self):
        return iter(())

    def __bool__(self):
        return False

    def __len__(self):
        return 0


def _render_section(asset_eval: dict) -> str:
    """The Asset Evaluation <section>, rendered from the real template."""
    env = Environment(loader=FileSystemLoader(str(reports.TEMPLATES_DIR)),
                      autoescape=True, undefined=_Blank)
    html = env.get_template("quarterly_report.html").render(
        asset_eval=asset_eval, css_content="")
    m = re.search(r"<section>\s*<h2>Asset Evaluation.*?</section>", html, re.S)
    assert m, "asset-eval section not found — the template structure changed"
    return m.group(0)


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fragment)).strip()


# ── each state says its own thing ─────────────────────────────────────────────

STATES = {
    "no_data": _ctx(disposition="no_data"),
    "failed": _ctx(disposition="failed",
                   failure_reason="RuntimeError: forced for the test"),
    "partial": _ctx(disposition="partial", corr_prose="BTC correlation: 0.31",
                    rolling_prose="Post-2020 average: 0.37"),
}

MARKERS = {
    "no_data": "input return series are empty",
    "failed": "could not be computed",
    "partial": "Univariate statistics unavailable",
}


@pytest.mark.parametrize("state", sorted(STATES))
def test_each_state_renders_its_own_marker(state):
    assert MARKERS[state] in _text(_render_section(STATES[state]))


@pytest.mark.parametrize("state", sorted(STATES))
@pytest.mark.parametrize("other", sorted(MARKERS))
def test_no_state_renders_another_states_message(state, other):
    """The 3x3 matrix. Three cells are legitimate (the diagonal); the six
    off-diagonal cells must stay empty, or one sentence is covering two states
    again — which is the entire defect."""
    if state == other:
        return
    assert MARKERS[other] not in _text(_render_section(STATES[state])), (
        f"the {state!r} render carries the {other!r} message")


def test_the_three_states_render_three_different_sections():
    """A per-state marker can pass while every state renders the same body.
    Assert the CONTRAST."""
    rendered = {s: _render_section(c) for s, c in STATES.items()}
    assert len(set(rendered.values())) == 3, "two states render identically"


@pytest.mark.parametrize("state", sorted(STATES))
def test_no_state_claims_a_cause_it_cannot_know(state):
    assert OLD not in _text(_render_section(STATES[state]))


# ── the state that renders wrong rather than says wrong ───────────────────────

def test_partial_surfaces_the_analyses_instead_of_discarding_them():
    """THE POINT OF #250's GATE CHANGE, and the thing a message-only fix would
    not achieve. With the gate keyed on uni_rows the whole section was suppressed
    whenever the summary table was empty, throwing away successful work."""
    body = _text(_render_section(STATES["partial"]))
    assert "Full-Sample Correlation" in body, "the correlation analysis was discarded"
    assert "BTC correlation: 0.31" in body, "computed correlation prose was discarded"
    assert "Post-2020 average: 0.37" in body, "rolling-correlation prose was discarded"


def test_partial_omits_only_the_table_that_actually_failed():
    body = _text(_render_section(STATES["partial"]))
    assert "Ann. Return" not in body, "the univariate table has no rows; do not render it"


def test_a_computed_section_still_renders_the_table():
    """Non-vacuity: if the uni_rows gate suppressed the table unconditionally the
    test above would pass for the wrong reason."""
    rows = [{"asset": "BTC", "ann_return": "14.3%", "ann_vol": "52.8%",
             "sharpe": "0.19", "max_drawdown": "-81.5%", "skewness": "-0.29",
             "kurtosis": "8.40"}]
    body = _text(_render_section(_ctx(disposition="computed", uni_rows=rows)))
    assert "Ann. Return" in body and "14.3%" in body


def test_failed_carries_the_exception_and_blocks_the_verdict_inference():
    """#251's third clause, and it blocks a SPECIFIC inference here: a missing
    Bitcoin case study reads as a verdict on Bitcoin."""
    body = _text(_render_section(STATES["failed"]))
    assert "RuntimeError: forced for the test" in body, "the reason must survive"
    assert "not a judgement about the asset" in body
    assert "negative conclusion" in body


# ── the builder assigns the disposition ───────────────────────────────────────

def test_disposition_is_assigned_no_data_when_inputs_are_empty(monkeypatch):
    import src.asset_evaluation as ae
    monkeypatch.setattr(ae, "get_candidate_returns",
                        lambda *a, **k: pd.Series(dtype=float))
    assert reports._build_asset_eval_section()["disposition"] == "no_data"


def test_disposition_is_assigned_failed_and_carries_the_reason(monkeypatch):
    import src.asset_evaluation as ae

    def boom(*a, **k):
        raise RuntimeError("forced by test")
    monkeypatch.setattr(ae, "get_candidate_returns", boom)
    out = reports._build_asset_eval_section()
    assert out["disposition"] == "failed"
    assert "RuntimeError: forced by test" in out["failure_reason"]


def test_empty_dict_defaults_to_failed_not_to_a_gentler_state():
    """An exit that returns the unmodified dict must not claim `no_data`, which
    would assert an absence of input it never checked."""
    import inspect
    src = inspect.getsource(reports._build_asset_eval_section)
    head = src.split("try:")[0]
    assert '"disposition":      "failed"' in head or '"disposition": "failed"' in head


def test_the_log_line_does_not_claim_the_section_is_omitted():
    """It said "section omitted from PDF"; the render disproved it — the section
    emits a banner. An internal message contradicting the output is read by a
    maintainer mid-debug."""
    import inspect
    src = inspect.getsource(reports._build_asset_eval_section)
    assert "section omitted from PDF" not in src
    assert "NOT omitted" in src
