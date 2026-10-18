"""With SPAXX the only unresolved holding, the bands and the suggestions stand (#660).

Decided by the owner on October 18, 2026: the sleeve weights exclude cash and are exact,
so with SPAXX the only unresolved holding the SAA page's band status and Capital
Deployment's suggestions stand. Their notes say the cash figures (the operational cash
and the funding float) are at SPAXX's $1.00 NAV, without the income BIL would model. The
"weights distorted" text stays only for an unresolved holding the weights include.

Rendered on copies of the frozen book, offline, on July 21, 2026. SPAXX is left
unresolved by deleting BIL's closes before May 20, 2025, after the book's May 1
inception (#646); with the network blocked the fetch of that stretch fails, so the
reason is the failed fetch. The control also deletes VOO's closes, a holding the
weights include.
"""
from __future__ import annotations

import os
import shutil
import socket
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
FROZEN_BOOK = ROOT / "tests" / "fixtures" / "frozen_book.db"
pytestmark = pytest.mark.usefixtures("frozen_clock_module")

FAILED = "the fetch of BIL's prices back to the book's inception failed"
NAV = f"at SPAXX's $1.00 NAV, without the income BIL would model, because {FAILED}"
DISTORTED_SAA = "Weights exclude part of the book."
DISTORTED_CD = "Bands cannot be assessed."


def _render(page: str, tmp_path, monkeypatch, *, voo: bool = False, cash: float = 0.0):
    """Render ``page`` on a copy of the frozen book with BIL cut after inception (and
    VOO's closes deleted when ``voo``), the network blocked, and ``cash`` entered in
    Capital Deployment's two cash inputs."""
    import requests

    import src.db
    import src.prices

    db = tmp_path / "book.db"
    shutil.copyfile(FROZEN_BOOK, db)
    os.chmod(db, 0o644)
    con = sqlite3.connect(db)
    with con:
        assert con.execute("DELETE FROM prices WHERE ticker = 'BIL' AND price_date < "
                           "'2025-05-20'").rowcount > 0
        if voo:
            assert con.execute("DELETE FROM prices WHERE ticker = 'VOO'").rowcount > 0
    con.close()

    def _dns(*_a, **_k):
        raise socket.gaierror("test harness: DNS blocked")

    def _no_http(*_a, **_k):
        raise requests.ConnectionError("test harness: HTTP blocked")

    monkeypatch.setattr(socket, "getaddrinfo", _dns)
    monkeypatch.setattr(src.prices._SESSION, "get", _no_http)
    monkeypatch.setattr(src.db, "DB_PATH", db)
    monkeypatch.setattr(src.db, "_migrated_paths", set())
    src.prices._reset_trailing_memo()

    # Page 11 loads through a function cached on the as-of date alone.
    import streamlit as st
    st.cache_data.clear()
    st.cache_resource.clear()

    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(ROOT / "pages" / page), default_timeout=300)
    if cash:
        at.session_state["contrib_cash"] = cash
        at.session_state["rebal_cash"] = cash
    at.run()
    st.cache_data.clear()
    assert not at.exception, f"{page} raised: {at.exception}"
    return at


def _text(at) -> str:
    out = []
    for kind in ("warning", "error", "info", "success", "caption", "markdown",
                 "subheader", "title", "header"):
        out.extend(str(el.value) for el in getattr(at, kind, []))
    return " ".join(out)


def _verdict(blob: str) -> bool:
    return "within their tolerance" in blob or "out of band" in blob


# ── SPAXX alone: the bands and the suggestions stand ─────────────────────────

def test_the_saa_band_status_stands_and_the_cash_note_says_nav(tmp_path, monkeypatch):
    blob = _text(_render("1_SAA.py", tmp_path, monkeypatch))
    assert DISTORTED_SAA not in blob
    assert "unavailable" not in blob
    assert "**Band status:** " in blob and _verdict(blob)
    assert f"The operational cash is {NAV}." in blob


def test_capital_deployments_bands_and_suggestions_stand(tmp_path, monkeypatch):
    at = _render("11_Capital_Deployment.py", tmp_path, monkeypatch, cash=1000.0)
    blob = _text(at)
    assert DISTORTED_CD not in blob and "No buy suggestions" not in blob
    assert _verdict(blob)
    assert "recompute automatically from the edited amounts" in blob, (
        "the contribution suggestions are shown")
    assert ("All sleeves within tolerance bands" in blob
            or any("Suggested $" in d.value.columns for d in at.dataframe)), (
        "the band-breach suggestions ran")
    assert (f"The SPAXX balance, the funding float ($20.00), is {NAV}. The sleeve weights "
            "and bands exclude cash.") in blob


# ── a holding the weights include: the warnings stay ─────────────────────────

def test_with_voo_unresolved_too_the_saa_withdraws_its_band_status(tmp_path, monkeypatch):
    blob = _text(_render("1_SAA.py", tmp_path, monkeypatch, voo=True))
    assert DISTORTED_SAA in blob
    assert "**Band status:** unavailable" in blob and not _verdict(blob)


def test_with_voo_unresolved_too_capital_deployment_withholds(tmp_path, monkeypatch):
    blob = _text(_render("11_Capital_Deployment.py", tmp_path, monkeypatch, voo=True,
                         cash=1000.0))
    assert DISTORTED_CD in blob and "No buy suggestions" in blob
    assert "recompute automatically from the edited amounts" not in blob
    assert "All sleeves within tolerance bands" not in blob


# ── the two helpers ──────────────────────────────────────────────────────────

def _cov(*statuses):
    from src.coverage import TickerStatus, coverage_from_statuses
    return coverage_from_statuses([
        TickerStatus(t, False, reason=r) if r else
        TickerStatus(t, True, served_through="2026-07-20") for t, r in statuses],
        "2026-07-20")


@pytest.mark.parametrize("statuses, in_weights, cash", [
    pytest.param((("SPAXX", None), ("VOO", None)), [], None, id="every holding priced"),
    pytest.param((("SPAXX", "no_cached_rows"), ("VOO", None)), [],
                 "at SPAXX's $1.00 NAV, without the income BIL would model, because BIL's "
                 "stored prices do not reach back to the book's inception", id="SPAXX alone"),
    pytest.param((("SPAXX", "fetch_failed"), ("VOO", "fetch_failed"), ("VTV", None)),
                 ["VOO"], NAV, id="SPAXX and VOO"),
    pytest.param((("VOO", "no_cached_rows"), ("VTV", None)), ["VOO"], None,
                 id="VOO alone, no SPAXX held"),
])
def test_what_the_weights_include_and_what_the_cash_is(statuses, in_weights, cash):
    from src.coverage import cash_at_nav, unresolved_in_weights
    cov = _cov(*statuses)
    assert unresolved_in_weights(cov) == in_weights
    assert cash_at_nav(cov) == cash
