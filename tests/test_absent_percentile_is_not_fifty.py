"""A percentile that does not exist must not become a stance. #244 and #245.

Two sites, ONE shared magic number, TWO different reasons it survived — which is why
this is one change with two dispositions rather than one fix applied twice.

#244 — LIVE, and the mechanism is laundering. `macro.percentile` was changed to
return None on an empty series, citing this defect by number; its docstring names
the caller obligation: "those that can [receive an empty series] must handle None".
`reports.py` did not. It wrapped the call in `except Exception`, so
`f"{None:.0f}th"` raised TypeError, the catch-all swallowed it, and the honest None
was converted back into 50 — which `_cape_regime` maps to a confident "Moderate".
An upstream fix silently reverted one layer down.

#245 — LATENT and structural. The site guarded emptiness ITSELF and supplied 50.0
inline, so it could never reach the None contract at all. It renders nothing wrong
today because its only caller guards first; a second caller hits fabrication
immediately.

THE #244 TESTS ASSERT THE RENDERED STANCE, NOT THE RETURN VALUE. The whole defect is
that the value was already correct upstream, so a test on what `percentile` returns
would have passed throughout.
"""
import re

import pandas as pd
import pytest
from jinja2 import ChainableUndefined, Environment, FileSystemLoader

import src.asset_evaluation as ae
import src.reports as reports

# The stance a fabricated 50 produces. Its presence on an unknown-percentile render
# is the defect, in one string.
FABRICATED = "consistent with the long-run average"


class _Blank(ChainableUndefined):
    def __iter__(self):
        return iter(())

    def __bool__(self):
        return False

    def __len__(self):
        return 0


def _render_macro(macro: dict) -> str:
    env = Environment(loader=FileSystemLoader(str(reports.TEMPLATES_DIR)),
                      autoescape=True, undefined=_Blank)
    html = env.get_template("quarterly_report.html").render(macro=macro,
                                                            css_content="")
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def _macro_with(monkeypatch, **patches) -> dict:
    for name, value in patches.items():
        monkeypatch.setattr(reports, name, value)
    return reports._build_macro_section()


def _all_nan_series():
    """CAPE parses, but nothing ranks: percentile() returns None BY DESIGN."""
    return pd.Series([float("nan")] * 5,
                     index=pd.date_range("2020-01-01", periods=5))


# ── #244: the rendered stance ─────────────────────────────────────────────────

def test_absent_percentile_does_not_render_a_valuation_stance(monkeypatch):
    """THE REGRESSION TEST FOR THE LAUNDERING. Asserts the SENTENCE THE READER GETS,
    because `percentile` already returned the honest None before this fix — a test on
    its return value passes with the defect present."""
    macro = _macro_with(monkeypatch, get_cape_series=lambda *a, **k: _all_nan_series())
    body = _render_macro(macro)
    assert FABRICATED not in body, (
        "an unknown percentile still renders a confident valuation stance — the "
        "fabricated 50 reached _cape_regime again")
    assert "Uncertain" in body


def test_absent_percentile_renders_the_default_to_policy_action(monkeypatch):
    """The action must not depend on the missing input. 'Maintain SAA
    diversification' is what policy says regardless of CAPE, so it is correct when
    nothing is known; 'consistent with the long-run average' asserts a specific
    relationship to a history that produced no observations."""
    macro = _macro_with(monkeypatch, get_cape_series=lambda *a, **k: _all_nan_series())
    assert "maintaining SAA diversification" in _render_macro(macro)


def test_none_is_handled_not_caught(monkeypatch):
    """`percentile` returning None must not travel through an exception handler.

    Pinned because the defect was invisible at the value layer: swap None-handling
    for a catch-all and this is what notices. If a TypeError is being raised and
    swallowed inside the builder, `pct_int` comes back an int rather than None.
    """
    macro = _macro_with(monkeypatch, get_cape_series=lambda *a, **k: _all_nan_series())
    assert macro["cape"]["pct_int"] is None, (
        "pct_int is not None — a fabricated rank survived, or a TypeError was caught")


@pytest.mark.parametrize("trigger,exc", [
    ("get_cape_series", FileNotFoundError("committed CAPE csv missing")),
    ("current_cape", RuntimeError("no current CAPE")),
])
def test_raising_paths_also_render_no_stance(monkeypatch, trigger, exc):
    """These always degraded honestly — the issue said otherwise, and the render
    settled it. Pinned so the two paths cannot diverge again."""
    macro = _macro_with(
        monkeypatch,
        **{trigger: lambda *a, **k: (_ for _ in ()).throw(exc)})
    body = _render_macro(macro)
    assert FABRICATED not in body
    assert "Uncertain" in body
    assert macro["cape"]["pct_int"] is None


def test_a_real_percentile_still_renders_its_real_stance(monkeypatch):
    """Non-vacuity, and the direction that matters: a fix that suppressed the stance
    unconditionally would satisfy every assertion above."""
    series = pd.Series([10.0, 15.0, 20.0, 25.0, 30.0, 36.0],
                       index=pd.date_range("2020-01-01", periods=6))
    macro = _macro_with(monkeypatch,
                        current_cape=lambda *a, **k: 36.0,
                        get_cape_series=lambda *a, **k: series)
    body = _render_macro(macro)
    assert macro["cape"]["pct_int"] == 100
    assert macro["cape"]["regime"] == "Elevated"
    assert "Uncertain" not in body


def test_the_honest_form_has_a_single_definition():
    """Both paths must read the same constant, or they drift — which is how the two
    branches came to disagree in the first place."""
    import inspect
    src = inspect.getsource(reports._build_macro_section)
    assert src.count("_CAPE_UNKNOWN") >= 2, (
        "the unknown-stance branches no longer share one definition")
    assert "suggest maintaining SAA diversification" not in src, (
        "the stance is spelled out inline again instead of read from _CAPE_UNKNOWN")


# ── #245: latent, and reachable only from a second caller ─────────────────────

def test_empty_history_does_not_invent_a_percentile():
    """THE SECOND CALLER. `pages/9_Correlations.py` guards emptiness before calling,
    so this branch renders nothing today — this test IS the second caller that makes
    it reachable, which is why the site is worth fixing while latent.
    """
    text = ae.interpret_rolling_correlation(0.42, pd.Series(dtype=float))
    assert "50th" not in text, "the inline 50.0 fabrication is back"
    assert "no history available to rank it against" in text


def test_empty_history_still_reports_what_is_known():
    """Only the historical RANK is withheld. The level band is derived from
    current_value, which is known, so suppressing it would be over-disclosure."""
    text = ae.interpret_rolling_correlation(0.42, pd.Series(dtype=float))
    assert "+0.42" in text
    assert "moderate" in text


def test_a_real_history_still_ranks():
    """Non-vacuity for the same reason as the CAPE case."""
    idx = pd.date_range("2015-01-01", periods=400, freq="D")
    hist = pd.Series([0.30 + 0.05 * i / 400 for i in range(400)], index=idx)
    text = ae.interpret_rolling_correlation(0.42, hist)
    assert "percentile of its history since 2015" in text
    assert "no history available" not in text


def test_the_site_no_longer_guards_emptiness_itself():
    """The structural half of #245: the fix is that the None CONTRACT can now reach
    this line at all. An inline `if not hist.empty else <number>` puts it back beyond
    reach of anything macro.percentile does."""
    import inspect
    src = inspect.getsource(ae.interpret_rolling_correlation)
    call = [ln for ln in src.splitlines()
            if "_macro_percentile(" in ln and not ln.strip().startswith("#")]
    assert call, "the percentile call vanished"
    assert not any("hist.empty" in ln for ln in call), (
        "the inline emptiness guard is back — macro.percentile's contract can no "
        "longer reach this site")


def test_the_neighbours_dead_fallback_is_gone_not_merely_unused():
    """The honest neighbour is what made #245 legible — `start_year` reached the
    right answer under the identical guard the line above fabricated under.

    Its `else "the sample start"` fallback is now UNREACHABLE: pct is None exactly
    when hist is empty, so the no-history clause subsumes it. Removed rather than
    left, because a dead honest fallback beside a deleted dishonest one leaves the
    same latency this change exists to remove — and the next reader would have to
    work out which of the two branches can still fire.

    Written as an assertion rather than a comment so that reintroducing the phrase
    without a reachable branch goes red.
    """
    import inspect
    src = inspect.getsource(ae.interpret_rolling_correlation)
    code = [ln for ln in src.splitlines() if not ln.strip().startswith("#")]
    assert not any("the sample start" in ln for ln in code), (
        "the unreachable start-year fallback is back in executable code")
    text = ae.interpret_rolling_correlation(0.42, pd.Series(dtype=float))
    assert "the sample start" not in text
