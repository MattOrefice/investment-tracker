"""The README states no CI duration; the CI file carries the measured normal (#533,
decided by the owner on October 13, 2026).

The README said CI takes "about two minutes". The test step took 432 to 767 s across
the sixteen runs of October 13, 2026, and its limits were raised to 20 and 30 minutes in
#508 for that reason. A duration typed in the README is out of date after the next PR
that adds a render test, so the README now says what runs and states no time, and
.github/workflows/ci.yml, where the limits are set, carries the measured normal beside
each limit.

Since #544 (decided by the owner on October 14, 2026) each normal is a dated
approximation, "about N minutes ..., as of <date>", not a range: the October 13 range's
top was 768 s, and main's push runs for 1462b89 and 66f69c5 on October 14 reported 769.72
and 797.44 s, which read as breaches of a normal that was only ever a sample.

Since #544's second decision (October 14, 2026) CI runs the suite on four workers, so
each normal is the parallel trial's, read from run 38636521102, and the README names the
command with its `-n 4`.

Pinned here:
  * the README's CI section states no duration;
  * the CI file states a measured normal for the job and for the test step, each an
    approximation with the date and the run it was read from, and no range;
  * each limit has headroom over its normal: 2x for the job, 1.5x for the step.
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
    assert "GitHub Actions runs `python -m pytest -n 4`" in section, "premise: the CI section"
    assert not re.search(r"\b(\d+|one|two|three|five|ten)\s*(-\s*)?(minutes?|seconds?|mins?)\b",
                         section, flags=re.I), section


def _flat() -> str:
    text = CI.read_text(encoding="utf-8")
    return " ".join(line.strip().lstrip("#").strip() for line in text.splitlines())


_DATED = r"about ([\d.]+) minutes on GitHub's runner, as of ([A-Z][a-z]+ \d{1,2}, \d{4})"


def _normals() -> "dict[str, tuple[float, str]]":
    """{"job" | "step": (about how many minutes, as of what date)}, read from the CI
    file's comments."""
    flat = _flat()
    job = re.search(r"Measured normal for the whole job: " + _DATED, flat)
    step = re.search(r"Measured normal: " + _DATED, flat)
    assert job and step, "the CI file states the job's and the test step's measured normal"
    return {"job": (float(job[1]), job[2]), "step": (float(step[1]), step[2])}


def test_the_ci_file_carries_the_measured_normal_and_its_runs():
    text = CI.read_text(encoding="utf-8")
    normals = _normals()
    assert normals["step"][0] < normals["job"][0], "the job holds the step"
    assert normals["step"][1] == normals["job"][1] == "October 14, 2026"
    # The run it was read from, so the next measurement can be set beside it: #544's
    # parallel trial, once for each normal.
    assert text.count("38636521102") == 2
    assert "#533" in text and "#544" in text


def test_the_normal_is_a_dated_approximation_not_a_range():
    flat = _flat()
    # Both the job's normal and the step's say so.
    assert flat.count("An approximation dated to its measurement, not a range: a single run "
                      "can take longer") == 2
    assert not re.search(r"\b[\d.]+ to [\d.]+ minutes\b", flat), "a normal stated as a range"


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


def test_each_limit_has_headroom_over_its_measured_normal():
    """A dated approximation can be exceeded by a single run, so a limit sits well above
    it rather than just above it."""
    job, step = _limits()
    assert (job, step) == (30, 20), "premise: the limits #508 set"
    normals = _normals()
    assert job >= 2 * normals["job"][0], (job, normals)
    assert step >= 1.5 * normals["step"][0], (step, normals)
