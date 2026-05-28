"""Seed household account metadata for personal mode.

Maps the 7 real Fidelity accounts (by account_number) to their
tax treatment, management type, and display metadata.
Only runs against tracker.db — never demo.db.

If an account_number is not found in the mapping, the row is left
at migration defaults (tax_treatment='other', managed_by='self').
"""
import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

_TAX_TO_TYPE: dict[str, str] = {
    "taxable":         "taxable",
    "traditional_ira": "retirement",
    "roth_ira":        "retirement",
    "hsa":             "hsa",
    "workplace_plan":  "retirement",
    "other":           "other",
}

# (account_number, tax_treatment, managed_by, pseudonym, display_name)
_ACCOUNTS: list[tuple[str, str, str, str, str]] = [
    ("acct_taxable_01",                             "taxable",         "self",     "acct_taxable_01",  "Individual Taxable (Self-Directed)"),
    ("acct_taxable_02",                             "taxable",         "external", "acct_taxable_02",  "Individual Taxable (TOD)"),
    ("acct_trad_ira_01",                             "traditional_ira", "external", "acct_trad_ira_01", "Traditional IRA"),
    ("acct_roth_01",                             "roth_ira",        "external", "acct_roth_01",     "Roth IRA"),
    ("74369",                                 "workplace_plan",  "external", "acct_wkpl_01",     "Moody's PPP"),
    ("acct_wkpl_02", "workplace_plan",  "external", "acct_wkpl_02",     "Workplace Plan"),
    ("acct_hsa_01",                             "hsa",             "external", "acct_hsa_01",      "HSA"),
]


def seed_household_accounts(db_path: str | Path) -> None:
    """UPSERT the 7 household accounts into accounts table, keyed on account_number."""
    db_path = Path(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        for acct_num, tax_treat, managed_by, pseudonym, display_name in _ACCOUNTS:
            acct_type = _TAX_TO_TYPE.get(tax_treat, "other")
            try:
                conn.execute(
                    """
                    INSERT INTO accounts
                        (account_number, name, type, custodian, is_active,
                         tax_treatment, pseudonym, display_name, managed_by)
                    VALUES (?, ?, ?, 'Fidelity', 1, ?, ?, ?, ?)
                    ON CONFLICT(account_number) DO UPDATE SET
                        name          = excluded.name,
                        type          = excluded.type,
                        custodian     = excluded.custodian,
                        is_active     = excluded.is_active,
                        tax_treatment = excluded.tax_treatment,
                        pseudonym     = excluded.pseudonym,
                        display_name  = excluded.display_name,
                        managed_by    = excluded.managed_by
                    """,
                    (acct_num, display_name, acct_type, tax_treat, pseudonym, display_name, managed_by),
                )
            except Exception:
                logger.warning("Failed to upsert account %r — skipping", acct_num, exc_info=True)
        conn.commit()
        logger.info("Seeded %d household accounts in %s", len(_ACCOUNTS), db_path)
    finally:
        conn.close()
