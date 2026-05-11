"""Unit tests for Trade Log display-layer transforms (no DB, no Streamlit)."""
import sys
import pathlib
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))


# ── Helpers that replicate the display-layer logic from pages/3_Trade_Log.py ─

def _build_ticker_thesis(pos_theses: list[dict]) -> dict[str, tuple[str, str]]:
    """Mirror of the _ticker_thesis build in the Trades tab."""
    mapping: dict[str, tuple[str, str]] = {}
    for pt in pos_theses:
        if pt.get("status") != "active":
            continue
        parts = (pt.get("title") or "").rsplit(" — ", 1)
        if len(parts) == 2:
            sym = parts[-1].strip()
            mapping[sym] = (pt["title"], pt.get("parent_title") or "—")
    return mapping


def _render_row(trade: dict, ticker_thesis: dict) -> dict:
    """Mirror of the row-building logic in the Trades tab."""
    pos_t = trade["position_thesis"]
    inv_t = trade["investment_thesis"]
    if trade.get("lot_source") == "drip" and not pos_t:
        inherited = ticker_thesis.get(trade["ticker"])
        if inherited:
            pos_t, inv_t = inherited
    return {
        "Date":              trade["trade_date"],
        "Action":            trade["action"].title(),
        "Ticker":            trade["ticker"],
        "Shares":            trade["shares"],
        "Price":             trade["price"],
        "Total Value":       round(trade["shares"] * trade["price"], 2),
        "Position Thesis":   pos_t or "—",
        "Investment Thesis": inv_t or "—",
    }


# ── Action column case normalization ─────────────────────────────────────────

def test_action_title_case_from_buy():
    row = _render_row(
        {"trade_date": "2025-05-01", "action": "buy", "ticker": "VOO",
         "shares": 1.0, "price": 500.0, "lot_source": "drip",
         "position_thesis": None, "investment_thesis": None},
        {},
    )
    assert row["Action"] == "Buy"


def test_action_title_case_already_correct():
    row = _render_row(
        {"trade_date": "2025-05-01", "action": "Buy", "ticker": "VOO",
         "shares": 1.0, "price": 500.0, "lot_source": "initial",
         "position_thesis": "US Large Core — VOO", "investment_thesis": "US Large Core"},
        {},
    )
    assert row["Action"] == "Buy"


def test_action_title_case_from_uppercase():
    row = _render_row(
        {"trade_date": "2025-05-01", "action": "BUY", "ticker": "VOO",
         "shares": 1.0, "price": 500.0, "lot_source": "initial",
         "position_thesis": None, "investment_thesis": None},
        {},
    )
    assert row["Action"] == "Buy"


# ── DRIP thesis inheritance ───────────────────────────────────────────────────

def test_drip_row_inherits_active_position_thesis():
    """DRIP row for VOO inherits the active VOO position thesis."""
    pos_theses = [
        {
            "title":        "US Large Core — VOO",
            "parent_title": "US Large Core",
            "status":       "active",
        }
    ]
    ticker_thesis = _build_ticker_thesis(pos_theses)
    row = _render_row(
        {"trade_date": "2026-03-31", "action": "buy", "ticker": "VOO",
         "shares": 0.003, "price": 597.55, "lot_source": "drip",
         "position_thesis": None, "investment_thesis": None},
        ticker_thesis,
    )
    assert row["Position Thesis"] == "US Large Core — VOO"
    assert row["Investment Thesis"] == "US Large Core"


def test_drip_row_fallback_when_no_active_thesis():
    """DRIP row for a ticker with no active position thesis shows em-dash."""
    ticker_thesis = _build_ticker_thesis([])   # empty — no active theses
    row = _render_row(
        {"trade_date": "2026-01-01", "action": "buy", "ticker": "ZZZZ",
         "shares": 1.0, "price": 50.0, "lot_source": "drip",
         "position_thesis": None, "investment_thesis": None},
        ticker_thesis,
    )
    assert row["Position Thesis"] == "—"
    assert row["Investment Thesis"] == "—"


def test_drip_row_skips_inactive_thesis():
    """DRIP row does not inherit a closed position thesis."""
    pos_theses = [
        {
            "title":        "US Large Core — VOO",
            "parent_title": "US Large Core",
            "status":       "closed",
        }
    ]
    ticker_thesis = _build_ticker_thesis(pos_theses)
    row = _render_row(
        {"trade_date": "2026-01-01", "action": "buy", "ticker": "VOO",
         "shares": 0.003, "price": 590.0, "lot_source": "drip",
         "position_thesis": None, "investment_thesis": None},
        ticker_thesis,
    )
    assert row["Position Thesis"] == "—"


def test_discretionary_row_unaffected_by_drip_logic():
    """Discretionary trade with pre-populated thesis fields is unchanged."""
    pos_theses = [
        {
            "title":        "US Large Core — VOO",
            "parent_title": "US Large Core",
            "status":       "active",
        }
    ]
    ticker_thesis = _build_ticker_thesis(pos_theses)
    row = _render_row(
        {"trade_date": "2025-05-01", "action": "Buy", "ticker": "VOO",
         "shares": 10.0, "price": 500.0, "lot_source": "initial",
         "position_thesis": "US Large Core — VOO",
         "investment_thesis": "US Large Core"},
        ticker_thesis,
    )
    assert row["Position Thesis"] == "US Large Core — VOO"
    assert row["Investment Thesis"] == "US Large Core"
    assert row["Action"] == "Buy"


# ── Test 12: write_guard_toast calls st.toast with the demo-mode message ─────

def test_write_guard_toast_calls_st_toast():
    """write_guard_toast() must call st.toast with a message containing 'Demo mode'
    and the lock icon.  This is the message the public demo visitor sees when any
    write action is blocked."""
    with patch("src.ui_helpers.st") as mock_st:
        from src.ui_helpers import write_guard_toast
        write_guard_toast()

    mock_st.toast.assert_called_once()
    call_args = mock_st.toast.call_args
    msg = call_args.args[0] if call_args.args else ""
    assert "Demo mode" in msg
    assert call_args.kwargs.get("icon") == "🔒"
