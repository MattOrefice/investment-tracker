"""Capital Deployment's unfunded-sleeve warning reads the coverage record (#662).

Decided by the owner on October 18, 2026:
  * a held holding with no price is reported as unpriced, as the gap warning reports it,
    never as "no holdings yet";
  * the warning never prints a placeholder for a ticker: with no SAA carrier flagged
    for the sleeve, it says no carrier is flagged;
  * no demo.db change.

The sentences are built by rebalance.unfunded_warning, tested here case by case, and
rendered on copies of the frozen book, offline, on July 21, 2026, with $1,000 entered to
deploy. The frozen book, like demo.db, flags no security as an SAA carrier (is_in_saa).
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

PLACEHOLDER = "****"


# ── the sentences ────────────────────────────────────────────────────────────

def _cov(*statuses):
    from src.coverage import TickerStatus, coverage_from_statuses
    return coverage_from_statuses([
        TickerStatus(t, False, reason=r) if r else
        TickerStatus(t, True, served_through="2026-07-20") for t, r in statuses],
        "2026-07-20")


T2S = {"VOO": "US Large Core", "SPY": "US Large Core", "AVDV": "International Small Value",
       "IDHQ": "International Quality", "VNQ": "Real Assets", "PDBC": "Real Assets"}


def _warn(unfunded, statuses, carriers=None, gap=()):
    from src.rebalance import unfunded_warning
    return unfunded_warning(unfunded, T2S, carriers or {}, _cov(*statuses), weights_gap=gap)


def test_a_held_holding_with_no_price_is_named_as_unpriced():
    text = _warn(["US Large Core"], [("VOO", "no_cached_rows"), ("IDHQ", None)],
                 gap=["VOO"])
    assert text == ("**US Large Core** has holdings with no committed price (VOO), so new "
                    "cash cannot be sized for it.")


def test_a_sleeve_holding_nothing_names_its_carriers():
    text = _warn(["International Small Value"], [("VOO", None)],
                 carriers={"International Small Value": ["AVDV"]})
    assert text == (
        "**International Small Value** has an SAA target but no holdings yet, so new cash "
        "cannot be allocated to it. Deploying now would leave part of the contribution "
        "unplaced. Buy **AVDV** (one share each is enough), re-import, then deploy. The "
        "rest of this page works normally.")


def test_with_no_carrier_flagged_it_says_so_and_prints_no_placeholder():
    text = _warn(["International Small Value"], [("VOO", None)])
    assert text == (
        "**International Small Value** has an SAA target but no holdings yet, so new cash "
        "cannot be allocated to it. Deploying now would leave part of the contribution "
        "unplaced. No SAA carrier is flagged for it, so no ticker is named: buy one share "
        "of a holding for it, re-import, then deploy. The rest of this page works normally.")


def test_carriers_flagged_for_some_sleeves_only():
    text = _warn(["International Quality", "International Small Value"], [("VOO", None)],
                 carriers={"International Quality": ["IDHQ"]})
    assert ("Buy **IDHQ** for International Quality (one share each is enough); no SAA "
            "carrier is flagged for International Small Value, so no ticker is named for it. "
            "Re-import, then deploy.") in text
    assert PLACEHOLDER not in text


def test_an_unpriced_holding_in_a_sleeve_with_a_priced_one_is_named_too():
    """Nothing is unfunded: Real Assets still holds VNQ priced. The page's suggestions
    are withheld for PDBC, and it said "**** have an SAA target but no holdings yet"."""
    text = _warn([], [("VNQ", None), ("PDBC", "no_cached_rows")], gap=["PDBC"])
    assert text == ("No contribution is suggested while the sleeve weights miss a holding "
                    "with no committed price (PDBC).")


def test_both_kinds_together():
    text = _warn(["International Small Value", "US Large Core"],
                 [("VOO", "fetch_failed"), ("IDHQ", None)], gap=["VOO"])
    assert text.startswith("**US Large Core** has holdings with no committed price (VOO)")
    assert "**International Small Value** has an SAA target but no holdings yet" in text
    assert "rest of this page works normally" not in text and PLACEHOLDER not in text


# ── rendered ─────────────────────────────────────────────────────────────────

def _render(tmp_path, monkeypatch, *sql: str) -> str:
    """Capital Deployment on a copy of the frozen book after ``sql``, the network blocked,
    with $1,000 to deploy: every warning, info and caption."""
    import requests

    import src.db
    import src.prices

    db = tmp_path / "book.db"
    shutil.copyfile(FROZEN_BOOK, db)
    os.chmod(db, 0o644)
    con = sqlite3.connect(db)
    with con:
        for statement in sql:
            assert con.execute(statement).rowcount > 0, statement
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

    import streamlit as st
    st.cache_data.clear()
    st.cache_resource.clear()

    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(ROOT / "pages" / "11_Capital_Deployment.py"),
                           default_timeout=300)
    at.session_state["contrib_cash"] = 1000.0
    at.run()
    st.cache_data.clear()
    assert not at.exception, at.exception
    return " ".join(str(el.value) for kind in ("warning", "info", "caption")
                    for el in getattr(at, kind, []))


def test_rendered_a_held_voo_with_no_price_is_unpriced_not_unheld(tmp_path, monkeypatch):
    blob = _render(tmp_path, monkeypatch, "DELETE FROM prices WHERE ticker = 'VOO'")
    assert ("**US Large Core** has holdings with no committed price (VOO), so new cash "
            "cannot be sized for it.") in blob
    assert "no holdings yet" not in blob and PLACEHOLDER not in blob


def test_rendered_an_unpriced_pdbc_beside_a_priced_vnq(tmp_path, monkeypatch):
    blob = _render(tmp_path, monkeypatch, "DELETE FROM prices WHERE ticker = 'PDBC'")
    assert ("No contribution is suggested while the sleeve weights miss a holding with no "
            "committed price (PDBC).") in blob
    assert "no holdings yet" not in blob and PLACEHOLDER not in blob


def test_rendered_a_sleeve_holding_nothing_with_no_carrier_flagged(tmp_path, monkeypatch):
    blob = _render(tmp_path, monkeypatch, "DELETE FROM trades WHERE ticker = 'AVDV'")
    assert "has an SAA target but no holdings yet" in blob
    assert "No SAA carrier is flagged for it, so no ticker is named" in blob
    assert PLACEHOLDER not in blob


def test_rendered_a_sleeve_holding_nothing_with_its_carrier_flagged(tmp_path, monkeypatch):
    blob = _render(tmp_path, monkeypatch, "DELETE FROM trades WHERE ticker = 'AVDV'",
                   "UPDATE securities SET is_in_saa = 1 WHERE ticker = 'AVDV'")
    assert "Buy **AVDV** (one share each is enough), re-import, then deploy." in blob
