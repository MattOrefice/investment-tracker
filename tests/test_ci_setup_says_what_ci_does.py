"""docs/ci_setup.md says what ci.yml does (#598, decided by the owner on October 15, 2026).

Its "Diagnosing CI failures" list pointed a WeasyPrint failure at "the `apt-get install`
step in ci.yml", which ci.yml no longer has: its REMOVED note records that the step hung
and guarded nothing CI asserted. tests/test_the_pdf_draws_its_charts.py holds ci.yml to
no apt step; this holds the doc to the same.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_the_doc_points_a_weasyprint_failure_at_no_apt_step():
    doc = (ROOT / "docs" / "ci_setup.md").read_text(encoding="utf-8")
    [line] = [ln for ln in doc.splitlines() if ln.strip().startswith("- WeasyPrint missing")]
    assert "ci.yml installs no apt package" in line
    assert "should cover this" not in line and "apt-get install` step" not in line
    assert "packages.txt" in line


def test_ci_yml_has_the_removed_note_the_doc_cites_and_no_apt_step():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "# REMOVED: an apt step" in ci
    steps = [ln for ln in ci.splitlines() if not ln.lstrip().startswith("#")]
    assert not [ln for ln in steps if "apt-get" in ln or re.search(r"\bapt\b", ln)]
