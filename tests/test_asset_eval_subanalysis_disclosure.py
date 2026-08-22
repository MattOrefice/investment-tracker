"""Seven sub-analyses, three tiers of disclosure, and one that was WRONG not missing.

#249. The section ran seven sub-analyses each behind `except: pass`, and #250 gave the
section a single `disposition`. Rendered one at a time, six of the seven vanished
silently — and the seventh did something worse.

THE ASYMMETRY, which is the point of this file. `args_for` is derived ENTIRELY from
the marginal-Sharpe curve. Three of the four `args_against` are hardcoded and depend
on no computation at all. Only the computed side can fail, so a failure is
structurally DIRECTIONAL: the reader got "Arguments for inclusion: None identified
from available data" beside three confident arguments against — a one-sided verdict
manufactured from a computation that did not run.

Three tiers, because seven banners is coarse disclosure's symmetric failure: a section
that marks six absences teaches the reader to skim all of them.

  tier 1  per-analysis, where a heading is left standing over nothing (5f, 5h; 5a
          already has #250's banner)
  tier 2  ONE grouped note for the two correlation analyses
  tier 3  5g/5j — not a disclosure gap. A wrong claim.

Rendered from the real template with a synthetic context; the section's own keys are
all supplied, so the blank-tolerant Undefined never engages inside it (asserted
below, same as tests/test_asset_eval_disposition.py).
"""
import re

import pandas as pd
import pytest
from jinja2 import ChainableUndefined, Environment, FileSystemLoader

import src.asset_evaluation as ae
import src.reports as reports

OLD = "None identified from available data"


class _Blank(ChainableUndefined):
    def __iter__(self):
        return iter(())

    def __bool__(self):
        return False

    def __len__(self):
        return 0


_BASE = {
    "sample_start": "2018-01-01", "uni_rows": [], "corr_chart_b64": None,
    "corr_prose": None, "rolling_chart_b64": None, "rolling_prose": None,
    "con_rows": [], "sharpe_con_no": None, "sharpe_con_with": None,
    "delta_bps_con": None, "msc_chart_b64": None, "dd_rows": [],
    "btc_2022_mdd": None,
    "args_for": [], "args_against": [], "conclusion": "x",
    "disposition": "computed", "failure_reason": None, "unavailable": [],
}


def _ctx(**over):
    d = dict(_BASE)
    d.update(over)
    return d


def _render(asset_eval: dict) -> str:
    env = Environment(loader=FileSystemLoader(str(reports.TEMPLATES_DIR)),
                      autoescape=True, undefined=_Blank)
    html = env.get_template("quarterly_report.html").render(
        asset_eval=asset_eval, css_content="")
    m = re.search(r"<section>\s*<h2>Asset Evaluation.*?</section>", html, re.S)
    assert m, "asset-eval section not found — template structure changed"
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(0))).strip()


# ── tier 3: the wrong claim, and the one that must not be buried ──────────────

def test_a_failed_curve_says_not_assessed_rather_than_none_identified():
    body = _render(_ctx(unavailable=["marginal_sharpe"]))
    assert "Arguments for inclusion: not assessed" in body
    assert OLD not in body, "a failed computation still reads as a finding"


def test_a_failed_curve_blocks_the_one_sided_verdict_reading():
    """THE LOAD-BEARING CLAUSE, and it exists because args_against still renders.
    Without it, silence on one side reads as a decision."""
    body = _render(_ctx(unavailable=["marginal_sharpe"],
                        args_against=["drawdown exceeds 80%"]))
    assert "were not weighed against anything" in body
    assert "not a finding about the asset" in body


def test_a_computed_curve_with_no_improvement_says_none_found():
    """The other half of the distinction — a real result, stated as one."""
    body = _render(_ctx(unavailable=[], args_for=[]))
    assert "Arguments for inclusion: none found" in body
    assert "This is a result, not a gap" in body
    assert "not assessed" not in body


def test_the_two_states_render_differently():
    """A per-state marker passes while both branches say the same thing. Contrast."""
    assert _render(_ctx(unavailable=["marginal_sharpe"])) != _render(_ctx(unavailable=[]))


def test_a_real_case_for_inclusion_still_renders():
    """Non-vacuity: suppressing args_for unconditionally satisfies the above."""
    body = _render(_ctx(args_for=["Sharpe-improving at 10% allocation"]))
    assert "Sharpe-improving at 10% allocation" in body
    assert "none found" not in body and "not assessed" not in body


def test_the_retired_sentence_is_gone_from_the_template():
    src = (reports.TEMPLATES_DIR / "quarterly_report.html").read_text(encoding="utf-8")
    assert OLD not in src, "the conflated sentence is back in the template"


# ── tier 1: per-analysis notes ────────────────────────────────────────────────

@pytest.mark.parametrize("key,marker", [
    ("mv", "Mean-variance analysis unavailable"),
    ("drawdown", "Drawdown sensitivity unavailable"),
])
def test_tier_one_notes_render_for_their_own_failure(key, marker):
    assert marker in _render(_ctx(unavailable=[key]))


@pytest.mark.parametrize("key,marker", [
    ("mv", "Mean-variance analysis unavailable"),
    ("drawdown", "Drawdown sensitivity unavailable"),
])
def test_tier_one_notes_do_not_render_otherwise(key, marker):
    """A note that fires on a healthy section is worse than none — it teaches the
    reader to skim. Asserted as ELEMENT ABSENCE on the control."""
    assert marker not in _render(_ctx(unavailable=[]))
    other = "drawdown" if key == "mv" else "mv"
    assert marker not in _render(_ctx(unavailable=[other])), (
        f"the {key} note fired for a {other} failure")


# ── tier 2: ONE grouped note, naming which ────────────────────────────────────

@pytest.mark.parametrize("keys,expected", [
    (["correlation"], "the full-sample sleeve correlations"),
    (["rolling"], "the rolling BTC-vs-SPY series"),
    (["correlation", "rolling"], "neither the full-sample sleeve correlations nor"),
])
def test_the_grouped_correlation_note_names_what_failed(keys, expected):
    body = _render(_ctx(unavailable=keys))
    assert "Correlation figures unavailable" in body
    assert expected in body


def test_the_grouped_note_is_one_note_not_two():
    """Grouping is the point: two correlation failures must not produce two banners."""
    body = _render(_ctx(unavailable=["correlation", "rolling"]))
    assert body.count("Correlation figures unavailable") == 1


def test_the_grouped_note_says_what_the_placeholder_cannot():
    """The chart placeholder carries TWO meanings — computation failed, or image
    rendering failed — separated only by whether prose survives, and it reads as the
    image case in both. The note must disambiguate or it is redundant with it."""
    body = _render(_ctx(unavailable=["correlation"]))
    assert "refers to image rendering and is a separate matter" in body


def test_no_correlation_note_on_a_healthy_section():
    assert "Correlation figures unavailable" not in _render(_ctx(unavailable=[]))


# ── the builder records what failed ───────────────────────────────────────────

# The six sub-analyses and the trivial EMPTY value each returns when stubbed. An
# empty result makes the block skip its body without raising, so it records nothing —
# which is what lets one target raise while the other five stay quiet.
#
# WHY STUB AT ALL: a real build costs ~78s, and the data load is only ~2s of it — the
# sub-analyses are the other ~76s. Stubbing the five non-targets keeps each test at
# ~2s and preserves independence, where six real builds would cost ~470s and roughly
# double the suite. The REAL sub-analyses running healthily is covered elsewhere, by
# the PDF build tests that render this section for real.
_STUB_EMPTY = {
    "build_univariate_table":           lambda: pd.DataFrame(),
    "compute_full_sample_correlations": lambda: pd.Series(dtype=float),
    "compute_rolling_correlation":      lambda: pd.Series(dtype=float),
    "compute_mv_analysis":              lambda: {},
    "compute_marginal_sharpe_curve":    lambda: pd.DataFrame(),
    "compute_drawdown_sensitivity":     lambda: pd.DataFrame(),
}
_KEY_OF = {
    "build_univariate_table": "univariate",
    "compute_full_sample_correlations": "correlation",
    "compute_rolling_correlation": "rolling",
    "compute_mv_analysis": "mv",
    "compute_marginal_sharpe_curve": "marginal_sharpe",
    "compute_drawdown_sensitivity": "drawdown",
}


def _stub_all_but(monkeypatch, raising: str | None):
    for name, make in _STUB_EMPTY.items():
        if name == raising:
            def boom(*a, **k):
                raise RuntimeError("forced by test")
            monkeypatch.setattr(ae, name, boom)
        else:
            monkeypatch.setattr(ae, name, (lambda m: (lambda *a, **k: m()))(make))


@pytest.mark.parametrize("fname", sorted(_KEY_OF))
def test_each_sub_analysis_records_its_own_failure_and_only_its_own(monkeypatch, fname):
    """Independence, asserted in BOTH directions: the failing block records its key,
    and no other block records anything. One-directional assertion would pass for a
    builder that appended every key on any failure."""
    _stub_all_but(monkeypatch, fname)
    out = reports._build_asset_eval_section()
    assert out["unavailable"] == [_KEY_OF[fname]], (
        f"{fname} failing recorded {out['unavailable']!r}, expected exactly "
        f"[{_KEY_OF[fname]!r}]")


def test_nothing_is_recorded_when_nothing_raises(monkeypatch):
    """Non-vacuity for all six above: a builder that always appended would satisfy
    every one of them. Empty results are NOT failures and must record nothing."""
    _stub_all_but(monkeypatch, None)
    assert reports._build_asset_eval_section()["unavailable"] == []


def test_the_disposition_stays_section_level(monkeypatch):
    """Extending a section-level string to seven values would relocate #249's problem
    into the data model. `disposition` keeps its #250 vocabulary; groups key on the
    list."""
    _stub_all_but(monkeypatch, None)
    out = reports._build_asset_eval_section()
    assert out["disposition"] in {"computed", "partial", "no_data", "failed"}


def test_the_harness_context_leaves_nothing_undefined_inside_the_section():
    """Same premise-check as tests/test_asset_eval_disposition.py: the blank-tolerant
    Undefined must not be able to engage inside the section, or it could hide exactly
    the suppression this file measures."""
    import ast
    import inspect
    import textwrap

    fn = ast.parse(textwrap.dedent(
        inspect.getsource(reports._build_asset_eval_section))).body[0]
    keys = None
    for node in ast.walk(fn):
        if (isinstance(node, ast.AnnAssign)
                and getattr(node.target, "id", None) == "_empty"):
            keys = {k.value for k in node.value.keys}
            break
    assert keys, "_empty dict not found — the builder's shape changed"
    assert not (keys - set(_BASE)), (
        f"builder keys the test context omits: {sorted(keys - set(_BASE))}")


# ── the asymmetry that this change discloses but does NOT remove ──────────────

def test_the_hardcoded_args_against_count_is_two_since_the_drawdown_was_derived():
    """UPDATED DELIBERATELY at #276, which is what the previous version asked for.

    It read `static == 3` and said "if that was deliberate, update #276." It was:
    the drawdown argument moved out of the `extend([...])` block and is now derived
    from `btc_ret` per render, so the hardcoded literal count is **2**.

    THE COUNT ALONE WAS NEVER THE POINT, and reading the old figure as "three of four
    are hardcoded" was itself brittle — the fourth entry is the correlation argument,
    which fires only above a `> 0.3` gate and measured **0.31** on 2026-08-21. One
    hundredth lower and the ratio would have read "three of three", describing a
    WORSE asymmetry with a better-sounding number, with nothing changed in the code.

    What survives, and why each is right:

      - the commodity tax-treatment string, FROZEN pending #278 (a rate vocabulary
        may land, and this string should then derive from it rather than be a fourth
        copy of the same claim)
      - the editorial no-cash-flow-anchor string, now LABELLED as standing, because
        it is not a measurement withheld — it is not measurable and should not
        become conditional
    """
    import ast
    import inspect
    import textwrap

    fn = ast.parse(textwrap.dedent(
        inspect.getsource(reports._build_asset_eval_section))).body[0]
    static = 0
    for node in ast.walk(fn):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "extend"
                and getattr(node.func.value, "id", "") == "args_against"):
            static = len(node.args[0].elts)
    assert static == 2, (
        f"the hardcoded args_against count changed to {static}; if that was "
        f"deliberate, update #276 (asymmetry) and #278 (the frozen tax string)")


def test_deriving_the_drawdown_did_not_make_the_case_against_conditional():
    """The reason the count fell without the case-against weakening: the drawdown
    entry is appended UNCONDITIONALLY, in one of two forms. A version that appended
    only when the sweep succeeded would have quietly shrunk the arguments against
    on exactly the runs where the reader can least check them."""
    import ast
    import inspect
    import textwrap

    fn = ast.parse(textwrap.dedent(
        inspect.getsource(reports._build_asset_eval_section))).body[0]
    appends = [n for n in ast.walk(fn)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
               and n.func.attr == "append"
               and getattr(n.func.value, "id", "") == "args_against"
               and any(isinstance(a, ast.Call)
                       and getattr(a.func, "id", "") == "_drawdown_argument"
                       for a in n.args)]
    assert len(appends) == 1, "the drawdown argument is not appended exactly once"
    # ...and not inside an `if`. Walking the branch bodies is what separates
    # "always appended" from "appended when the sweep ran".
    guarded = [n for b in ast.walk(fn) if isinstance(b, ast.If)
               for n in ast.walk(b) if n is appends[0]]
    assert not guarded, (
        "the drawdown argument is appended inside a conditional — the case against "
        "would shrink silently when the sweep fails; the two forms exist so it "
        "does not")
