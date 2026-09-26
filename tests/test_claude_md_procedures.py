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


def test_before_every_push_runs_the_suite_and_the_live_data_tests():
    s = _section("Before every push")
    assert "TRACKER_MODE=demo" in s and "python -m pytest -q" in s
    # The live_data tests run only on the schedule unless someone runs them (#394).
    assert "python -m pytest -m\n  live_data" in s or "python -m pytest -m live_data" in s
    assert "rendered page" in s
