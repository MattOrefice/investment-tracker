"""CI and the live-data job run on ubuntu-24.04, not ubuntu-latest (decided by the
owner, October 15, 2026).

GitHub moves ubuntu-latest from Ubuntu 24.04 to 26.04 gradually between October 19 and
November 19, 2026 (its changelog of September 17, 2026). During that month one SHA could
run on either image, so a green run would not say which image it was green on. The two
workflows that run the suite name their image; moving them to 26.04 is a trial for later
(#585).

keep-awake.yml runs no tests and was left on the label, so it is not checked here.
"""
from __future__ import annotations

import re
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"


def _runs_on(text: str) -> "list[str]":
    return re.findall(r"^\s*runs-on:\s*(\S+)\s*$", text, flags=re.M)


def test_the_workflows_that_run_the_suite_name_ubuntu_24_04():
    for name, runs_pytest in (("ci.yml", "python -m pytest --tb=long -q"),
                              ("live-data.yml", 'python -m pytest -m "live_data"')):
        text = (WORKFLOWS / name).read_text(encoding="utf-8")
        assert runs_pytest in text, f"premise: {name} runs the suite"
        images = _runs_on(text)
        assert images, f"premise: {name} names a runner for its job"
        assert set(images) == {"ubuntu-24.04"}, (name, images)
