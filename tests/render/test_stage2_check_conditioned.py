"""#349 — the "vs. Stage 2" check warns only where a gap means something is wrong.

Brinson-Fachler holds the start-of-window holdings fixed. Once Stage 2's portfolio
side became the book's TWR (#350), a window holding a deposit could no longer
reconcile: on the owner's book the check read ⚠ +17.59 bps beside a correct Stage 2,
a known limitation presented as a failure. Suppressing the tile on that flag would
have hidden correct figures on every deposit window, so the check is conditioned on
the window instead:

  * no external flow in the window: the 0.5 bps threshold and the warning stay;
  * a deposit or withdrawal in it: no glyph, and a disclosure saying why BF and the
    TWR differ there.
"""
from __future__ import annotations

import sqlite3

import pytest
from streamlit.testing.v1 import AppTest

from src.attribution import stage2_reconciliation

GLYPHS = ("✓", "⚠")


# ── the decision, by branch ──────────────────────────────────────────────────

@pytest.mark.parametrize("gap, glyph", [(0.0, "✓"), (0.49, "✓"), (-0.49, "✓"),
                                        (0.5, "⚠"), (-17.59, "⚠")])
def test_a_window_with_no_flow_keeps_the_threshold_and_the_warning(gap, glyph):
    frag, note = stage2_reconciliation(gap, "2026-04-21", {})
    assert frag == f"vs. Stage 2: {glyph} {gap:+.2f} bps"
    assert note is None


def test_a_zero_flow_is_no_flow():
    """cf is zero on most days; only a non-zero amount is a deposit."""
    assert stage2_reconciliation(0.2, "2026-04-21", {"2026-05-01": 0.0}) == (
        "vs. Stage 2: ✓ +0.20 bps", None)


def test_a_deposit_window_discloses_instead_of_warning():
    frag, note = stage2_reconciliation(17.59, "2026-06-09", {"2026-07-01": 1008.29})
    assert not any(g in frag for g in GLYPHS), frag
    assert "+17.59 bps" in frag and "deposit" in frag
    assert note is not None and not any(g in note for g in GLYPHS)
    for must in ("deposit of July 1, 2026", "start of the window (June 9, 2026)",
                 "fixed for the whole window", "what an intra-window deposit bought",
                 "time-weighted return", "+17.59 bps", "not an error",
                 "within 0.5 bps"):
        assert must in note, (must, note)


def test_several_deposits_are_all_named():
    _, note = stage2_reconciliation(3.0, "2026-01-01",
                                    {"2026-03-02": 500.0, "2026-02-02": 500.0})
    assert "deposits of February 2, 2026 and March 2, 2026" in note, note


def test_a_withdrawal_is_called_one():
    frag, note = stage2_reconciliation(-4.0, "2026-01-01", {"2026-02-02": -250.0})
    assert "withdrawal" in frag and "deposit" not in frag
    assert "withdrawal of February 2, 2026" in note and "sold" in note, note


# ── the page picks the branch from the window it attributes ─────────────────

def _frozen_conftest(config):
    return next(p for p in config.pluginmanager.get_plugins()
                if getattr(p, "FROZEN_TODAY", None) is not None)


def _render(pytestconfig, tmp_path, deposit: bool):
    """Page 2 on the frozen book (anchor 2026-07-20), optionally with one deposit on
    2026-06-01: inside the default 3M window and SI, outside 1M. SI starts ON the
    inception seed buys, which are the starting value, never a deposit. Returns
    {window: (the 'vs. Stage 2' fragment, [disclosure captions])}."""
    import streamlit as st
    conftest = _frozen_conftest(pytestconfig)
    out = {}
    try:
        with pytest.MonkeyPatch.context() as mp:
            conftest.pin_today(mp)
            book = conftest.point_at_frozen_book(mp, tmp_path)
            if deposit:
                con = sqlite3.connect(book)
                with con:
                    close = con.execute("SELECT close FROM prices WHERE ticker = 'VOO' "
                                        "AND price_date = '2026-06-01'").fetchone()[0]
                    assert con.execute(
                        "INSERT INTO trades (account_id, ticker, trade_date, action, shares, "
                        "price, fees, notes, lot_source) VALUES (1, 'VOO', '2026-06-01', "
                        "'Buy', ?, ?, 0, '#349 test deposit', 'Fidelity CSV')",
                        (300.0 / close, close)).rowcount == 1
                con.close()
            st.cache_data.clear()
            at = AppTest.from_file("pages/2_Performance.py", default_timeout=180).run()
            assert not at.exception, f"page raised: {at.exception}"
            for label in ("3M", "1M", "SI"):
                [r for r in at.radio if r.key == "bf_period"][0].set_value(label).run()
                caps = [str(c.value) for c in at.caption]
                line = next(c for c in caps if c.startswith("**BF decomposition:**"))
                out[label] = (line.split("&nbsp;·&nbsp;")[-1].strip(),
                              [c for c in caps if c.startswith("**Attribution cannot see")])
    finally:
        conftest.unpin_leftovers()
        st.cache_data.clear()
    return out


def test_the_page_discloses_on_a_deposit_window_and_checks_the_others(pytestconfig, tmp_path):
    seen = _render(pytestconfig, tmp_path, deposit=True)
    frag, notes = seen["3M"]
    assert not any(g in frag for g in GLYPHS) and "not comparable" in frag, frag
    assert len(notes) == 1 and "deposit of June 1, 2026" in notes[0], notes
    frag, notes = seen["SI"]
    assert "not comparable" in frag and len(notes) == 1 and "June 1, 2026" in notes[0], (frag, notes)
    # 1M starts 2026-06-20, after the deposit: no flow, so the check is live again.
    frag, notes = seen["1M"]
    assert frag.startswith("vs. Stage 2: ✓"), frag
    assert notes == []


def test_the_clean_book_is_checked_in_every_window_it_renders(pytestconfig, tmp_path):
    """CONTROL: no deposit anywhere, so no window may disclose and each reconciles
    (the BF bridge holds on a flow-free window). SI is the case that pins "strictly
    after the first day": its first day carries the $1k inception seed."""
    seen = _render(pytestconfig, tmp_path, deposit=False)
    for label, (frag, notes) in seen.items():
        assert frag.startswith("vs. Stage 2: ✓"), (label, frag)
        assert notes == [], (label, notes)
