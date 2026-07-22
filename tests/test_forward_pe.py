"""Tests for src/forward_pe.py — the manual-EPS seam's honesty rules."""
import json
from datetime import date, timedelta

import pytest

from src.forward_pe import (
    STALE_SUPPRESS_DAYS,
    STALE_WARN_DAYS,
    compute_forward_pe,
    forward_pe_state,
    load_forward_eps,
    staleness_days,
)


def _write(tmp_path, payload):
    p = tmp_path / "forward_eps.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


# ── load_forward_eps: absent / template / valid / malformed ───────────────────

def test_missing_file_is_none(tmp_path):
    assert load_forward_eps(tmp_path / "nope.json") is None


def test_null_template_is_none(tmp_path):
    """The committed template (both fields null) means 'not entered', not error."""
    p = _write(tmp_path, {"forward_eps": None, "as_of": None, "source": "S&P DJI"})
    assert load_forward_eps(p) is None


def test_committed_template_parses_as_not_entered():
    """The actual committed data/forward_eps.json must be the null-template state."""
    assert load_forward_eps() is None


def test_valid_estimate_loads(tmp_path):
    p = _write(tmp_path, {"forward_eps": 305.0, "as_of": "2026-07-10", "source": "S&P DJI"})
    info = load_forward_eps(p)
    assert info["eps"] == pytest.approx(305.0)
    assert info["as_of"] == date(2026, 7, 10)
    assert info["source"] == "S&P DJI"


@pytest.mark.parametrize("payload,fragment", [
    ({"forward_eps": 305.0, "as_of": None}, "must be set together"),
    ({"forward_eps": None, "as_of": "2026-07-10"}, "must be set together"),
    ({"forward_eps": "lots", "as_of": "2026-07-10"}, "not a number"),
    ({"forward_eps": -5, "as_of": "2026-07-10"}, "positive"),
    ({"forward_eps": 2.5, "as_of": "2026-07-10"}, "index-dollar range"),
    ({"forward_eps": 305.0, "as_of": "July 10 2026"}, "YYYY-MM-DD"),
    ({"forward_eps": 305.0, "as_of": "2200-01-01"}, "future"),
])
def test_malformed_raises_not_none(tmp_path, payload, fragment):
    """A file ATTEMPTING an estimate but failing validation must raise — a
    typo silently degrading to 'not entered' would hide the mistake."""
    p = _write(tmp_path, payload)
    with pytest.raises(ValueError, match=fragment):
        load_forward_eps(p)


# ── staleness state machine ───────────────────────────────────────────────────

def _info(days_old: int) -> dict:
    return {"eps": 305.0, "as_of": date.today() - timedelta(days=days_old), "source": "x"}


def test_fresh_is_ok():
    assert forward_pe_state(_info(0)) == "ok"
    assert forward_pe_state(_info(STALE_WARN_DAYS)) == "ok"


def test_aging_warns():
    assert forward_pe_state(_info(STALE_WARN_DAYS + 1)) == "stale_warn"
    assert forward_pe_state(_info(STALE_SUPPRESS_DAYS)) == "stale_warn"


def test_stale_suppresses():
    """Past the suppression threshold the figure must not render at all —
    same principle as refusing to fabricate the estimate."""
    assert forward_pe_state(_info(STALE_SUPPRESS_DAYS + 1)) == "suppressed"
    assert forward_pe_state(_info(400)) == "suppressed"


def test_staleness_days_counts():
    assert staleness_days(_info(30)) == 30


def test_thresholds_are_the_agreed_ones():
    """45-day warning / 90-day suppression, as specified for the seam."""
    assert STALE_WARN_DAYS == 45
    assert STALE_SUPPRESS_DAYS == 90


# ── the division itself ───────────────────────────────────────────────────────

def test_compute_forward_pe_is_the_plain_division():
    assert compute_forward_pe(7509.2, {"eps": 305.0}) == pytest.approx(24.62, abs=0.01)
