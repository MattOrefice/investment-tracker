"""Household account display helpers.

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
