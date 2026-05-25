"""Render-aware tests for the SAA page (pages/1_SAA.py).

Pinned at Phase 37 — verifies Implementation Note, Endowment Context, and
per-sleeve rationale formatting introduced in the Phase 37 polish pass.
"""
from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest


@pytest.fixture(scope="module")
def saa_app() -> AppTest:
    """Run the SAA page once and return the rendered AppTest object."""
    at = AppTest.from_file("pages/1_SAA.py", default_timeout=60)
    at.run()
    return at


def test_saa_runs_without_exception(saa_app: AppTest) -> None:
    """SAA page must complete render without raising an unhandled exception."""
    assert not saa_app.exception, f"SAA page raised: {saa_app.exception}"


def test_implementation_note_paper_trade(saa_app: AppTest) -> None:
    """Implementation note must use paper-trade framing. Pinned: Phase 37."""
    all_text = " ".join(c.value for c in saa_app.caption)
    assert "paper-trade portfolio simulated from May 2025" in all_text, (
        "Implementation note missing 'paper-trade portfolio simulated from May 2025' — "
        "old 'exercise the analytical framework' framing may have been restored"
    )


def test_endowment_context_illiquidity_premium(saa_app: AppTest) -> None:
    """Endowment Context closing must mention illiquidity premium. Pinned: Phase 37."""
    all_text = " ".join(m.value for m in saa_app.markdown)
    assert "illiquidity premium" in all_text, (
        "Endowment Context missing 'illiquidity premium' — Phase 37 prose may have been reverted"
    )


def test_all_sleeve_rationales_have_bold_would_conditions() -> None:
    """All 10 sleeve rationales in the DB must contain bold '**Would' condition blocks. Pinned: Phase 37.

    AppTest does not render collapsed expander content, so this checks the DB directly.
    The SAA page renders rationale verbatim from the DB via _safe_md().
    """
    import os, sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent))
    os.environ.setdefault("DB_PATH", "data/demo.db")
    from src.db import get_connection

    with get_connection() as conn:
        rows = conn.execute(
            "SELECT name, rationale FROM asset_classes WHERE parent_id IS NOT NULL"
        ).fetchall()

    assert len(rows) == 10, f"Expected 10 sleeve rows, got {len(rows)}"
    missing = [r["name"] for r in rows if "**Would" not in (r["rationale"] or "")]
    assert not missing, (
        f"Sleeves missing bold Would conditions in DB rationale: {missing} — "
        "Phase 37 restructuring may not have been applied to the database"
    )
