"""Tests for the Fidelity account-history (transaction) CSV import:
parsing, classification, multi-lot, dedup multiset, ticker validation, guard."""
from __future__ import annotations

import io
import sys
import pathlib
from collections import Counter

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.ingestion.fidelity import parse_fidelity_transactions
from src.ingestion.trade_import import (
    dedupe_against_existing,
    fingerprint,
    split_known_tickers,
)

# Realistic export: BOM, blank line, "Brokerage" line, header, transaction rows
# (multi-lot fills, a sell with negative qty, a dividend, a reinvestment, an
# unknown ticker, a "Processing" settlement, quoted descriptions with commas /
# parens / "S&P500"), a blank gap, a quoted disclaimer block, and a footer.
SAMPLE_CSV = (
    "﻿\n"
    "Brokerage\n"
    "\n"
    "Run Date,Action,Symbol,Description,Type,Price ($),Quantity,Commission ($),"
    "Fees ($),Accrued Interest ($),Amount ($),Cash Balance ($),Settlement Date\n"
    '06/02/2026,"YOU BOUGHT VANGUARD FTSE DEVELOPED MARKETS ETF (VEA) (Cash)",VEA,'
    '"VANGUARD FTSE DVLP MKT, S&P500 PROXY",Cash,70.61,2.0,0.00,0.00,0.00,-141.22,858.78,06/04/2026\n'
    '06/02/2026,"YOU BOUGHT VANGUARD FTSE DEVELOPED MARKETS ETF (VEA) (Cash)",VEA,'
    '"VANGUARD FTSE DVLP MKT",Cash,70.61,0.738,0.00,0.00,0.00,-52.11,806.67,Processing\n'
    '06/02/2026,"YOU BOUGHT VANGUARD INTERM TERM TREASURY ETF (VGIT) (Cash)",VGIT,'
    '"VANGUARD INTERM TERM TREAS",Cash,58.63,1.0,0.00,0.00,0.00,-58.63,748.04,06/04/2026\n'
    '06/02/2026,"YOU BOUGHT VANGUARD INTERM TERM TREASURY ETF (VGIT) (Cash)",VGIT,'
    '"VANGUARD INTERM TERM TREAS",Cash,58.64,0.333,0.00,0.00,0.00,-19.53,728.51,06/04/2026\n'
    '06/03/2026,"YOU SOLD VANGUARD VALUE ETF (VTV) (Cash)",VTV,'
    '"VANGUARD VALUE, LARGE CAP",Cash,170.00,-1.0,0.00,0.00,0.00,170.00,898.51,06/05/2026\n'
    '06/02/2026,"DIVIDEND RECEIVED FIDELITY GOVERNMENT MMKT (SPAXX) (Cash)",SPAXX,'
    '"FIDELITY GOVT MMKT, EX-DIV",Cash,,,,,,2.77,2.77,06/02/2026\n'
    '06/02/2026,"REINVESTMENT FIDELITY GOVERNMENT MONEY MARKET (SPAXX) (Cash)",SPAXX,'
    '"FIDELITY GOVT MMKT",Cash,1.00,2.77,0.00,0.00,0.00,-2.77,0.00,06/02/2026\n'
    '06/04/2026,"YOU BOUGHT TESLA INC (TSLA) (Cash)",TSLA,'
    '"TESLA INC COM",Cash,250.00,1.0,0.00,0.00,0.00,-250.00,648.51,06/06/2026\n'
    "\n"
    '"The data and information provided is for informational purposes only, and is not, '
    'and should not be construed as, investment advice."\n'
    '"Date downloaded 06/09/2026 10:01 am"\n'
)

KNOWN_TICKERS = {"VEA", "VGIT", "VTV", "VOO", "SPHQ", "AVUV", "IEMG", "SCHP", "PDBC", "VNQ", "SPAXX", "BIL"}


def _parse():
    return parse_fidelity_transactions(io.StringIO(SAMPLE_CSV))


# ── 1. Real-format parse: BOM / Brokerage / header / footer handling ──────────

def test_parse_extracts_only_trade_rows_ignoring_footer():
    res = _parse()
    # 4 buys (2x VEA, 2x VGIT) + 1 sell (VTV) + 1 unknown buy (TSLA) = 6 trade rows.
    # Dividend + reinvestment are NOT trades; disclaimer/footer ignored.
    assert len(res["trades"]) == 6
    assert all(t["trade_date"] == "2026-06-02" or t["trade_date"] in ("2026-06-03", "2026-06-04")
               for t in res["trades"])
    # No disclaimer text leaked in as a trade
    assert not any("data and information" in (t.get("raw_action") or "").lower() for t in res["trades"])


# ── 2. Multi-lot: each row is its own trade; different prices preserved ────────

def test_multi_lot_fills_stay_separate():
    res = _parse()
    vea = [t for t in res["trades"] if t["ticker"] == "VEA"]
    assert len(vea) == 2
    assert sorted(t["shares"] for t in vea) == [0.738, 2.0]
    assert all(t["price"] == 70.61 for t in vea)

    vgit = [t for t in res["trades"] if t["ticker"] == "VGIT"]
    assert len(vgit) == 2
    # Different prices must be preserved distinctly (NOT blended)
    assert sorted(t["price"] for t in vgit) == [58.63, 58.64]
    assert {t["shares"] for t in vgit} == {1.0, 0.333}


# ── 3. Non-trade classification (REINVESTMENT not a buy despite price+qty) ─────

def test_dividend_and_reinvestment_are_classified_not_traded():
    res = _parse()
    assert res["skipped"].get("DIVIDEND RECEIVED") == 1
    assert res["skipped"].get("REINVESTMENT") == 1
    # SPAXX must NOT appear as a trade even though REINVESTMENT carried price+qty
    assert not any(t["ticker"] == "SPAXX" for t in res["trades"])


# ── 4. "Processing" settlement parses without error ───────────────────────────

def test_processing_settlement_row_still_parsed():
    res = _parse()
    proc = [t for t in res["trades"] if t["ticker"] == "VEA" and t["shares"] == 0.738]
    assert len(proc) == 1  # the "Processing" settlement row parsed fine


# ── 5. Quoted descriptions with commas / parens / S&P500 don't bleed fields ───

def test_quoted_descriptions_do_not_bleed_fields():
    res = _parse()
    vea2 = next(t for t in res["trades"] if t["ticker"] == "VEA" and t["shares"] == 2.0)
    # Quoted "VANGUARD FTSE DVLP MKT, S&P500 PROXY" must not split ticker/price
    assert vea2["price"] == 70.61
    assert vea2["total_value"] == pytest.approx(141.22, abs=0.01)
    sell = next(t for t in res["trades"] if t["ticker"] == "VTV")
    assert sell["action"] == "Sell" and sell["shares"] == 1.0  # abs of -1.0


# ── 6. Dedup multiset: cumulative re-import + genuine duplicate edge ──────────

def test_dedup_skips_already_logged_on_reimport():
    res = _parse()
    known, _unknown = split_known_tickers(res["trades"], KNOWN_TICKERS)
    # First import: nothing logged yet -> all known lots import
    to_import, already = dedupe_against_existing(known, existing_trades=[])
    assert len(to_import) == len(known) and not already

    # Simulate they were logged (mirror trades-table rows), then re-import the same file
    existing = [
        {"trade_date": l["trade_date"], "ticker": l["ticker"],
         "action": l["action"], "shares": l["shares"], "price": l["price"]}
        for l in known
    ]
    to_import2, already2 = dedupe_against_existing(known, existing)
    assert to_import2 == [] and len(already2) == len(known)  # cumulative re-import = 0 new


def test_dedup_multiset_preserves_genuine_duplicate_lot():
    # Two identical lots logged; a later export contains THREE identical lots.
    lot = {"trade_date": "2026-06-02", "ticker": "VEA", "action": "Buy",
           "shares": 1.0, "price": 70.61}
    existing = [dict(lot), dict(lot)]               # N = 2 already logged
    parsed = [dict(lot), dict(lot), dict(lot)]      # N+1 = 3 in the new export
    to_import, already = dedupe_against_existing(parsed, existing)
    assert len(already) == 2 and len(to_import) == 1  # multiset: skip 2, import exactly 1
    # A plain set would have dropped all 3 — prove the distinction
    assert len({fingerprint(**{k: l[k] for k in ("trade_date", "ticker", "action", "shares", "price")}) for l in parsed}) == 1


# ── 7. Unknown ticker pre-filter (FK-safe) ────────────────────────────────────

def test_unknown_ticker_is_split_off_not_imported():
    res = _parse()
    known, unknown = split_known_tickers(res["trades"], KNOWN_TICKERS)
    assert [u["ticker"] for u in unknown] == ["TSLA"]
    assert not any(k["ticker"] == "TSLA" for k in known)


# ── 8. Write-path reuse respects the guard ────────────────────────────────────

def test_write_path_respects_guard(monkeypatch):
    import src.trade_writer as tw
    monkeypatch.setattr(tw, "is_write_enabled", lambda: False)
    monkeypatch.setattr(tw, "write_guard_toast", lambda: None)
    res = _parse()
    known, _ = split_known_tickers(res["trades"], KNOWN_TICKERS)
    to_import, _ = dedupe_against_existing(known, [])
    trades_list = [{
        "ticker": l["ticker"], "trade_date": l["trade_date"], "shares": l["shares"],
        "price": l["price"], "total_value": l["total_value"], "action": l["action"],
        "lot_source": "Fidelity CSV",
    } for l in to_import]
    expected = round(sum(t["total_value"] for t in trades_list), 2)
    # Guard disabled -> no write, returns False
    assert tw.write_trades_batch(trades_list, expected) is False


# ── 9. Notes fingerprint matches the dedup key ────────────────────────────────

def test_fingerprint_is_deterministic_and_composite():
    fp = fingerprint("2026-06-02", "vea", "buy", 0.738, 70.61)
    assert fp == "2026-06-02|VEA|Buy|0.738000|70.6100"
    # The same lot re-fingerprinted (e.g. from a re-export) is identical
    assert fp == fingerprint("2026-06-02", "VEA", "Buy", 0.738, 70.6100)


def test_dedup_attaches_fingerprint_for_notes():
    res = _parse()
    known, _ = split_known_tickers(res["trades"], KNOWN_TICKERS)
    to_import, _ = dedupe_against_existing(known, [])
    for lot in to_import:
        assert "fingerprint" in lot
        assert lot["fingerprint"] == fingerprint(
            lot["trade_date"], lot["ticker"], lot["action"], lot["shares"], lot["price"]
        )
