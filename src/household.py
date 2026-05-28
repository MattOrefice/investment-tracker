"""Household account display and look-through helpers.

UI code must call get_account_display() rather than reading
account_number directly — raw account numbers must never appear in pages/.
"""
import pandas as pd


def get_account_display(account_number: str, accounts_df: pd.DataFrame) -> dict:
    """Return display metadata for an account number.

    Returns dict with keys: pseudonym, display_name, tax_treatment, managed_by.
    All values are strings; empty strings are returned for unknown accounts.
    """
    match = accounts_df[accounts_df["account_number"] == account_number]
    if match.empty:
        return {"pseudonym": "", "display_name": "", "tax_treatment": "", "managed_by": ""}
    row = match.iloc[0]
    return {
        "pseudonym":     str(row.get("pseudonym", "") or ""),
        "display_name":  str(row.get("display_name", "") or ""),
        "tax_treatment": str(row.get("tax_treatment", "") or ""),
        "managed_by":    str(row.get("managed_by", "") or ""),
    }


def look_through_position(
    symbol: str,
    dollar_value: float,
    compositions_df: pd.DataFrame,
    securities_df: pd.DataFrame,
) -> pd.DataFrame:
    """Decompose a holding into sleeve-level dollar values.

    If the symbol has rows in compositions_df, expands into N rows where each
    row's dollar_value = input * weight. Rows sum to input dollar_value
    (within floating-point rounding).

    If no composition exists, returns a single row using the security's
    sleeve_category from securities_df.

    Raises ValueError if the symbol has neither a composition nor a
    sleeve_category — this is a hard guard against silent mis-aggregation.
    """
    comp = compositions_df[compositions_df["fund_symbol"] == symbol]
    if not comp.empty:
        return pd.DataFrame({
            "sleeve":       comp["underlying_sleeve"].values,
            "dollar_value": dollar_value * comp["weight"].values,
        })

    sec_match = securities_df[securities_df["ticker"] == symbol]
    if not sec_match.empty:
        sleeve = sec_match.iloc[0].get("sleeve_category")
        if sleeve:
            return pd.DataFrame([{"sleeve": sleeve, "dollar_value": dollar_value}])

    raise ValueError(
        f"look_through_position: no composition or sleeve_category for symbol {symbol!r}. "
        "Ensure the symbol is loaded in securities and fund_compositions tables."
    )
