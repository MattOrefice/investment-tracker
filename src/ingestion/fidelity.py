"""Fidelity portfolio positions CSV parser, and account-history (transaction) parser."""
from __future__ import annotations

import csv
import io
import json
import os
from datetime import datetime
from pathlib import Path

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_ACCOUNT_MAP = _REPO_ROOT / "private" / "account_map.json"


def _load_account_map(account_map_path: str | Path | None = None) -> dict[str, str]:
    """Load the {account_number: pseudonym} map used to pseudonymize ingested rows.

    The map lives in private/account_map.json (gitignored). It is required to
    ingest a Fidelity export so that raw account numbers never reach the returned
    DataFrame. Demo mode never ingests, so demo never needs this file.

    Raises FileNotFoundError with a clear message if the map is absent.
    """
    path = Path(account_map_path) if account_map_path is not None else _DEFAULT_ACCOUNT_MAP
    if not path.exists():
        raise FileNotFoundError(
            f"Account map not found at {path}. It is required to ingest Fidelity "
            "exports (it maps raw account numbers to pseudonyms so numbers never "
            "reach the schema). Create private/account_map.json — see "
            "src/seed/household_accounts.py for the pseudonyms."
        )
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    return {str(k): str(v) for k, v in raw.items()}


_COL_MAP = {
    "Account Number":         "account_number",
    "Account Name":           "account_name",
    "Symbol":                 "symbol",
    "Description":            "description",
    "Quantity":               "quantity",
    "Current Value":          "current_value",
    "Cost Basis Total":       "cost_basis_total",
    "Total Gain/Loss Dollar": "total_gain_loss",
    "Type":                   "type",
}

_NUMERIC_COLS = {"quantity", "current_value", "cost_basis_total", "total_gain_loss"}


def _clean_numeric(series: pd.Series) -> pd.Series:
    s = series.astype(str).str.strip()
    s = s.replace({"--": None, "nan": None, "": None})
    s = s.str.replace(r"[$%,+]", "", regex=True)
    return pd.to_numeric(s, errors="coerce")


def parse_fidelity_csv(
    path: str,
    account_map_path: str | Path | None = None,
) -> pd.DataFrame:
    """Parse a Fidelity portfolio positions CSV export.

    Handles: UTF-8 BOM, trailing footer paragraphs, '--' sentinels,
    dollar/percent/comma symbols, empty Quantity for cash, CUSIP-as-symbol,
    and '*' suffix on money-market symbols.

    Raw account numbers are resolved to pseudonyms via private/account_map.json
    (override with ``account_map_path``) and the raw ``account_number`` column is
    dropped — no account number is ever returned. A parsed account number absent
    from the map raises (the row is never passed through raw, never silently
    dropped). A missing map file raises.

    Returns a DataFrame with columns:
        pseudonym, account_name, symbol, description, quantity,
        current_value, cost_basis_total, total_gain_loss, type
    """
    df = pd.read_csv(path, encoding="utf-8-sig", index_col=False, dtype=str)

    # Drop footer paragraphs and blank rows — keep only rows with a Symbol
    df = df[df["Symbol"].notna() & (df["Symbol"].str.strip() != "")].copy()

    # Strip '*' suffix (e.g. "SPAXX**" -> "SPAXX")
    df["Symbol"] = df["Symbol"].str.replace(r"\*+$", "", regex=True).str.strip()

    # Select and rename output columns
    df = df[list(_COL_MAP.keys())].rename(columns=_COL_MAP)

    # account_number must remain a plain string — no numeric inference. Leading
    # zeros matter: the raw string is the exact lookup key into account_map.json.
    df["account_number"] = df["account_number"].astype(str).str.strip()

    # Resolve to pseudonym and DROP the raw number. Never emit an account number.
    account_map = _load_account_map(account_map_path)
    df["pseudonym"] = df["account_number"].map(account_map)
    unmapped = int(df["pseudonym"].isna().sum())
    if unmapped:
        # Do NOT include the raw numbers in the message — they must not reach logs.
        raise KeyError(
            f"{unmapped} account number(s) in the export are not present in the "
            "account map (private/account_map.json). Add them (mapped to a "
            "pseudonym) and re-run; rows are never imported with a raw number."
        )
    df = df.drop(columns=["account_number"])

    for col in _NUMERIC_COLS:
        df[col] = _clean_numeric(df[col])

    # pseudonym leads the frame (it replaces account_number's former position)
    cols = ["pseudonym"] + [c for c in df.columns if c != "pseudonym"]
    return df[cols].reset_index(drop=True)


# ── Account-history (transaction) export parser ───────────────────────────────
#
# A DIFFERENT format from the positions export above: a BOM + blank line(s) +
# a "Brokerage…" line, then the header "Run Date,Action,Symbol,…", then
# transaction rows, then a blank gap + a quoted legal-disclaimer block + a
# "Date downloaded …" footer. We log each fill LOT separately (tax-lot
# faithful) and classify non-trade rows (dividends/reinvestments) rather than
# silently dropping them.

_TXN_HEADER_PREFIX = "Run Date,Action"


def _read_text(source) -> str:
    """Accept a path str, raw bytes, or a file-like (e.g. st.file_uploader)."""
    if hasattr(source, "read"):
        data = source.read()
        return data.decode("utf-8-sig") if isinstance(data, (bytes, bytearray)) else str(data)
    if isinstance(source, (bytes, bytearray)):
        return source.decode("utf-8-sig")
    if isinstance(source, str):
        if os.path.exists(source):
            with open(source, encoding="utf-8-sig") as fh:
                return fh.read()
        return source  # already raw content
    raise TypeError(f"unsupported source type for Fidelity transactions: {type(source)!r}")


def _parse_run_date(s: str) -> str | None:
    """MM/DD/YYYY (or 2-digit year) → ISO date string; None if not a date."""
    s = (s or "").strip()
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _num(s: str) -> float:
    return float(str(s).replace(",", "").replace("$", "").strip())


def parse_fidelity_transactions(source) -> dict:
    """Parse a Fidelity account-history (transaction) CSV export.

    ``source`` may be a path, raw bytes, or a file-like object.

    Returns a dict:
        {
          "trades":  [ {trade_date, ticker, action, shares, price, amount,
                        total_value, raw_action}, ... ]  # buys/sells, lot-level
          "skipped": { "DIVIDEND RECEIVED": n, "REINVESTMENT": m, "unrecognized": k }
          "errors":  [ str, ... ]
        }

    - BOM, leading blanks/"Brokerage" line, and the trailing disclaimer/footer
      block are ignored (only rows with a parseable Run Date + Action are kept).
    - Quoted Action/Description fields (commas, parens, "S&P500") are handled by
      csv.DictReader — never split(',').
    - Classification is by Action PREFIX, not row shape: REINVESTMENT carries a
      price and quantity but is NOT a discretionary buy.
    - MULTI-LOT: each CSV row is one trade dict — no aggregation/blending.
    - Settlement Date is ignored entirely (so "Processing" never matters).
    """
    text = _read_text(source)
    lines = text.splitlines()

    hdr_idx = None
    for i, ln in enumerate(lines):
        if ln.lstrip("﻿").strip().startswith(_TXN_HEADER_PREFIX):
            hdr_idx = i
            break
    if hdr_idx is None:
        return {"trades": [], "skipped": {}, "errors": ["transaction header row not found"]}

    body = "\n".join(ln.lstrip("﻿") for ln in lines[hdr_idx:])
    reader = csv.DictReader(io.StringIO(body))

    trades: list[dict] = []
    skipped: dict[str, int] = {}
    errors: list[str] = []

    for row in reader:
        iso = _parse_run_date(row.get("Run Date", ""))
        action = (row.get("Action") or "").strip()
        if iso is None or not action:
            continue  # blank gap, disclaimer block, or "Date downloaded …" footer

        act_u = action.upper()
        if act_u.startswith("YOU BOUGHT") or act_u.startswith("YOU SOLD"):
            side   = "Buy" if act_u.startswith("YOU BOUGHT") else "Sell"
            ticker = (row.get("Symbol") or "").strip().upper()
            try:
                shares = abs(_num(row.get("Quantity", "")))   # sells carry negative qty
                price  = _num(row.get("Price ($)", ""))
            except (ValueError, AttributeError):
                errors.append(f"unparseable {side} row on {iso} ({ticker or '?'})")
                continue
            try:
                amount = _num(row.get("Amount ($)", ""))
            except (ValueError, AttributeError):
                amount = None
            if not ticker or shares <= 0 or price <= 0:
                errors.append(f"invalid {side} fields on {iso} ({ticker or '?'})")
                continue
            trades.append({
                "trade_date":  iso,
                "ticker":      ticker,
                "action":      side,
                "shares":      shares,
                "price":       price,
                "amount":      amount,
                "total_value": round(shares * price, 2),
                "raw_action":  action,
            })
        else:
            if act_u.startswith("DIVIDEND RECEIVED"):
                key = "DIVIDEND RECEIVED"
            elif act_u.startswith("REINVESTMENT"):
                key = "REINVESTMENT"
            else:
                key = "unrecognized"
            skipped[key] = skipped.get(key, 0) + 1

    return {"trades": trades, "skipped": skipped, "errors": errors}
