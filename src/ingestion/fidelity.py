"""Fidelity portfolio positions CSV parser."""
from __future__ import annotations

import pandas as pd

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


def parse_fidelity_csv(path: str) -> pd.DataFrame:
    """Parse a Fidelity portfolio positions CSV export.

    Handles: UTF-8 BOM, trailing footer paragraphs, '--' sentinels,
    dollar/percent/comma symbols, empty Quantity for cash, CUSIP-as-symbol,
    and '*' suffix on money-market symbols.

    Returns a DataFrame with columns:
        account_number, account_name, symbol, description, quantity,
        current_value, cost_basis_total, total_gain_loss, type
    """
    df = pd.read_csv(path, encoding="utf-8-sig", index_col=False, dtype=str)

    # Drop footer paragraphs and blank rows — keep only rows with a Symbol
    df = df[df["Symbol"].notna() & (df["Symbol"].str.strip() != "")].copy()

    # Strip '*' suffix (e.g. "SPAXX**" -> "SPAXX")
    df["Symbol"] = df["Symbol"].str.replace(r"\*+$", "", regex=True).str.strip()

    # Select and rename output columns
    df = df[list(_COL_MAP.keys())].rename(columns=_COL_MAP)

    # account_number must remain a plain string — no numeric inference
    df["account_number"] = df["account_number"].astype(str).str.strip()

    for col in _NUMERIC_COLS:
        df[col] = _clean_numeric(df[col])

    return df.reset_index(drop=True)
