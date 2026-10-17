"""CI can be dispatched by hand on main (decided by the owner, October 16, 2026).

Main's push run for d164dbd (#631) never fired on October 16, 2026, and ci.yml had no
workflow_dispatch trigger, so no run could be started on that SHA. The trigger is the
fallback: if a merge's push run never fires, dispatch ci.yml on main and use that run by
full SHA. The push and pull_request triggers are unchanged.
"""
from __future__ import annotations

import re
from pathlib import Path

CI = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml"


def _on_block(text: str) -> str:
    """The workflow's `on:` block, up to the next top-level key."""
    m = re.search(r"^on:\n(.*?)(?=^\S)", text, flags=re.M | re.S)
    assert m, "premise: ci.yml has an on: block"
    return m.group(1)


def test_ci_keeps_its_push_and_pull_request_triggers_and_can_be_dispatched():
    on = _on_block(CI.read_text(encoding="utf-8"))
    triggers = re.findall(r"^  ([a-z_]+):", on, flags=re.M)
    assert triggers == ["push", "pull_request", "workflow_dispatch"], triggers
    assert re.search(r"^  push:\n    branches: \[main\]$", on, flags=re.M), on
    assert re.search(r"^  pull_request:\n    branches: \[main\]$", on, flags=re.M), on


def test_the_trigger_says_why_it_is_there():
    on = _on_block(CI.read_text(encoding="utf-8"))
    flat = " ".join(line.strip().lstrip("#").strip() for line in on.splitlines())
    assert "d164dbd (#631) never fired" in flat, flat
    assert "dispatch this workflow on main" in flat and "use that run by full SHA" in flat
