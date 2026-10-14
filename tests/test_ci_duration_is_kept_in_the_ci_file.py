"""The README states no CI duration; the CI file carries the measured normal (#533,
decided by the owner on October 13, 2026).

The README said CI takes "about two minutes". The test step took 432 to 767 s across
the sixteen runs of October 13, 2026, and its limits were raised to 20 and 30 minutes in
#508 for that reason. A duration typed in the README is out of date after the next PR
that adds a render test, so the README now says what runs and states no time, and
.github/workflows/ci.yml, where the limits are set, carries the measured normal beside
each limit.

Pinned here:
  * the README's CI section states no duration;
  * the CI file states a measured normal for the job and for the test step, with the
    runs it was read from;
  * each limit is above the top of its measured normal.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CI = ROOT / ".github" / "workflows" / "ci.yml"


def _readme_ci_section() -> str:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    start = text.index("## CI/CD")
    end = text.find("\n## ", start + 1)
    return text[start:end if end != -1 else None]


def test_the_readme_states_no_ci_duration():
    section = _readme_ci_section()
    assert "GitHub Actions runs `python -m pytest`" in section, "premise: the CI section"
    assert not re.search(r"\b(\d+|one|two|three|five|ten)\s*(-\s*)?(minutes?|seconds?|mins?)\b",
                         section, flags=re.I), section


def _normals() -> "dict[str, tuple[float, float]]":
    """{"job" | "step": (low, high) minutes}, read from the CI file's comments."""
    text = CI.read_text(encoding="utf-8")
    flat = " ".join(line.strip().lstrip("#").strip() for line in text.splitlines())
    job = re.search(r"Measured normal for the whole job is ([\d.]+) to ([\d.]+) minutes", flat)
    step = re.search(r"Measured normal: ([\d.]+) to ([\d.]+) minutes", flat)
    assert job and step, "the CI file states the job's and the test step's measured normal"
    return {"job": (float(job[1]), float(job[2])), "step": (float(step[1]), float(step[2]))}


def test_the_ci_file_carries_the_measured_normal_and_its_runs():
    text = CI.read_text(encoding="utf-8")
    normals = _normals()
    assert normals["step"][1] < normals["job"][1], "the job holds the step"
    # The runs it was read from, so the next measurement can be set beside it.
    assert text.count("38489180090 to 38498085712") == 2
    assert "#533" in text


def _limits() -> "tuple[int, int]":
    """(the job's timeout-minutes, the test step's), read from the file's lines: the
    job's sits at the job's indent before its steps, the step's under its name. Read as
    text: the project carries no YAML parser."""
    lines = CI.read_text(encoding="utf-8").splitlines()
    steps = lines.index("    steps:")
    job = [int(m[1]) for line in lines[:steps]
           if (m := re.fullmatch(r"    timeout-minutes: (\d+)", line))]
    start = next(i for i, line in enumerate(lines)
                 if line.strip() == "- name: Run fast tests (slow/network tests excluded)")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].strip().startswith("- ")),
               len(lines))
    step = [int(m[1]) for line in lines[start:end]
            if (m := re.fullmatch(r"        timeout-minutes: (\d+)", line))]
    assert len(job) == 1 and len(step) == 1, (job, step)
    return job[0], step[0]


def test_each_limit_is_above_the_top_of_its_measured_normal():
    job, step = _limits()
    assert (job, step) == (30, 20), "premise: the limits #508 set"
    normals = _normals()
    assert job > normals["job"][1], (job, normals)
    assert step > normals["step"][1], (step, normals)
