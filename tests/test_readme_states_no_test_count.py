"""The README states no test count; each run reports its own (#543, decided by the
owner on October 14, 2026).

Its Integrity Testing section said "as of 2026-07-05 the per-push run reports 942 passed /
48 skipped / 35 deselected", and its CI/CD section sent the reader there for "the current
pass/skip/deselect breakdown". Main's push run for 46b5300 (38498085712, October 13,
2026) reported 3171 passed and 99 skipped. Like the CI duration (#533), a typed count is
out of date after the next PR that adds a test, and pytest prints the run's own at the
end of every run.

Pinned here: the README states no count of tests, in figures or in words, and says
where the count is.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"

_COUNTED = r"(passed|pass|skipped|skips?|deselected|failed|failures|tests?|test cases)"
_A_NUMBER = r"(\d[\d,]*\+?|one|two|three|four|five|six|seven|eight|nine|ten|dozens?|hundreds?)"
COUNT = re.compile(
    rf"\b{_A_NUMBER}\s*(?:/\s*)?{_COUNTED}\b"
    rf"|\b{_COUNTED}\s*[:=(]?\s*\d", flags=re.I)


def _section(heading: str) -> str:
    text = README.read_text(encoding="utf-8")
    start = text.index(heading)
    end = text.find("\n#", start + len(heading))
    return text[start:end if end != -1 else None]


def test_the_readme_states_no_test_count():
    text = README.read_text(encoding="utf-8")
    assert not [m.group(0) for m in COUNT.finditer(text)], [
        m.group(0) for m in COUNT.finditer(text)]


def test_the_readme_says_each_run_reports_its_own_count():
    """And says MOST skips are personal data's: with its count gone, "the skips" would
    claim every one, and GitHub's runner skips one test for its OS
    (tests/test_connection_closes.py: only Windows refuses to delete an open file)."""
    integrity, ci = _section("### Integrity Testing"), _section("## CI/CD")
    assert "All three layers run on every push via GitHub Actions" in integrity, "premise"
    assert "each run reports its own passed, skipped and deselected counts" in integrity
    assert "Most skips gate on personal-mode data files" in integrity
    assert "so those skip identically in CI and in a fresh local clone" in integrity
    assert "each run reports its own pass, skip and deselect counts" in ci
    assert "breakdown" not in ci


def test_the_count_pattern_finds_the_counts_the_readme_stated():
    """The pattern above, on the sentences the README carried until #543, and on a
    sentence with no count."""
    found = [m.group(0) for m in COUNT.finditer(
        "as of 2026-07-05 the per-push run reports 942 passed / 48 skipped / 35 deselected "
        "on the Linux runner. The 48 skips gate on personal-mode data files")]
    assert found == ["942 passed", "48 skipped", "35 deselected", "48 skips"]
    assert [m.group(0) for m in COUNT.finditer("Some 3,171 tests ran; failed: 0")] == [
        "3,171 tests", "failed: 0"]
    assert not list(COUNT.finditer("Three layers: (1) math identities that must hold, the "
                                   "skips gate on personal-mode data files"))
