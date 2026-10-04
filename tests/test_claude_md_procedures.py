"""The procedures CLAUDE.md carries, pinned so a rewrite cannot drop one silently.

The owner asked twice for a step to be added to a CLAUDE.md procedure that did not
exist (the quarterly close-out, #395; the pre-push steps, #394). Both now exist, and
the procedure has to live there, not in one session's memory. These tests read the
sections as a reader would find them.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _section(title: str) -> str:
    text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert f"\n## {title}\n" in text, f"CLAUDE.md has no '## {title}' section"
    return text.split(f"\n## {title}\n", 1)[1].split("\n## ", 1)[0]


def test_the_quarterly_close_out_names_the_code_behind_each_step():
    """Every step names a command or function that exists; the unsupported steps are
    listed as gaps, not written as steps (audit item 4g)."""
    import re
    s = _section("Quarterly close-out")
    for needle in ("data/etf_metadata.json", "committed_price_frontier",
                   "tools/advance_demo_prices.py", "tools/lock_demo_quarters.py",
                   "refresh_market_data.py --files cape pe",
                   "refresh_market_data.py --files ff_us ff_developed_exus",
                   "complete_quarter_inputs", "inputs_pending", "-m live_data",
                   "Not yet supported (#397)"):
        assert needle in s.replace("\n  ", " "), needle
    # The named files and functions exist.
    for path in re.findall(r"tests/test_\w+\.py|tools/\w+\.py", s):
        assert (ROOT / path).exists(), path
    from src import asof, cache, holdings
    assert callable(holdings.committed_price_frontier)
    assert callable(cache.complete_quarter_inputs) and callable(cache.get_quarter_snapshot)
    assert callable(asof.most_recent_reportable_quarter)


def test_the_session_close_rule_names_the_command_not_a_count():
    """#390: the rule said "14 tracked entries" until #389 left 13. It now names the
    command that produces the set, so no retirement can make it stale."""
    import re
    text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    flat = re.sub(r"\s+", " ", text)
    counted = re.findall(r"\b\d+ (?:tracked (?:entries|data files)|`git ls-files data` entries)",
                         flat)
    assert not counted, counted
    assert "git ls-files data | Get-Item | Where-Object { -not $_.IsReadOnly }" in flat


def test_before_every_push_runs_the_suite_and_the_live_data_tests():
    s = _section("Before every push")
    assert "TRACKER_MODE=demo" in s and "python -m pytest -q" in s
    # The live_data tests run only on the schedule unless someone runs them (#394).
    assert "python -m pytest -m\n  live_data" in s or "python -m pytest -m live_data" in s
    assert "rendered page" in s
    # Without a FRED key, as the scheduled job runs them (#485): with one, a test that
    # needed FRED passed before the merge and failed on schedule.
    flat = " ".join(s.split())
    assert "WITHOUT a FRED key" in flat
    assert "env -u FRED_API_KEY TRACKER_MODE=demo python -m pytest -m live_data -q" in flat
    assert "run them with the network up and FRED_API_KEY set" not in flat


def test_the_scheduled_live_data_job_sets_no_fred_key():
    """The premise of "as the scheduled job does". If the job is ever given the key,
    this fails, and the pre-push step has to change with it: the two must run the
    live_data tests in the same environment."""
    workflow = (ROOT / ".github" / "workflows" / "live-data.yml").read_text(encoding="utf-8")
    assert "pytest -m \"live_data\"" in workflow, "premise: this is the job that runs them"
    sets_key = [ln.strip() for ln in workflow.splitlines()
                if "FRED_API_KEY" in ln and not ln.strip().startswith("#")]
    assert sets_key == [], sets_key
