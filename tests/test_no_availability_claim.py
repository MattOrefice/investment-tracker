"""Nothing public says the owner is available for roles, or between jobs (November 3,
2026).

The landing page's byline, every page's sidebar footer and the README each
carried "Available for buy-side allocator and investment due diligence roles."
They were removed together. The byline and the footer's name and links stay,
and the render test asserts both halves: the contact surface is there, and the
availability line is not.

The source scan covers every tracked text file. CHANGELOG.md is history and
keeps its entries; this file names the phrases it bans.
"""
from __future__ import annotations

import pathlib
import re
import subprocess

import pytest
from streamlit.testing.v1 import AppTest

ROOT = pathlib.Path(__file__).resolve().parent.parent

BANNED = re.compile(
    r"available for buy-side|due diligence roles|allocator and investment due diligence"
    r"|resume artifact|Investment Data Analyst"
    # The 401(k) rollover card (November 3, 2026): no page says or implies the owner
    # is between jobs.
    r"|next job|future employer|between jobs",
    re.IGNORECASE,
)
EXEMPT = {"CHANGELOG.md", "tests/test_no_availability_claim.py"}


@pytest.fixture(scope="module")
def app_root() -> AppTest:
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120)
    at.run()
    assert not at.exception, at.exception
    return at


def _rendered_text(at: AppTest) -> str:
    return "\n".join([m.value for m in at.markdown] + [c.value for c in at.caption])


def test_landing_page_keeps_the_byline_and_footer_without_the_availability_line(
        app_root: AppTest) -> None:
    text = _rendered_text(app_root)
    assert "Built by Matt Orefice, CFA" in text
    assert "linkedin.com/in/matthew-orefice-cfa-83536b190" in text
    assert "github.com/MattOrefice/investment-tracker" in text
    assert not BANNED.search(text), BANNED.search(text).group(0)


def test_no_tracked_text_file_says_the_owner_is_available_for_roles() -> None:
    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                           text=True, check=True).stdout.splitlines()
    assert "README.md" in files and "app.py" in files
    hits = []
    for name in files:
        if name in EXEMPT:
            continue
        try:
            text = (ROOT / name).read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        hits += [f"{name}: {m.group(0)}" for m in BANNED.finditer(text)]
    assert not hits, hits


def test_the_rollover_card_cites_what_its_claims_rest_on() -> None:
    """The card's two corrected claims (owner's decision, November 3, 2026): the
    Traditional IRA holds a balance, so no route keeps it empty, and the rule is a
    December 31 total, not a clock."""
    from src.location_actions import ACTION_GROUPS
    g = next(x for x in ACTION_GROUPS if x["key"] == "rollover_401k")
    text = " ".join(g[f] for f in ("action", "pros", "cons"))
    assert "Instructions for Form 8606 (2025), line 6" in text
    assert "SEP and SIMPLE IRAs included, on December 31" in text
    assert "an employer plan's balance is not in it" in text
    assert "Rollover Chart" in text and "a plan is not required to" in text
    assert "Worth a CPA check" in text
    for gone in ("pro-rata clock", "keeps the Traditional IRA empty", "decent plan"):
        assert gone not in text, gone
