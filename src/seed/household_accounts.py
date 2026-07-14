"""Seed household account metadata for personal mode.

Defines the 7 household accounts by pseudonym, with their tax treatment,
management type, and display metadata. Keyed on pseudonym — raw Fidelity
account numbers are NEVER stored here. Ingestion resolves a real account
number to its pseudonym via private/account_map.json (gitignored); only the
pseudonym reaches the schema, fixtures, and logs.

Only runs against tracker.db — never demo.db.
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

# (tax_treatment, managed_by, pseudonym, display_name, included_in_household)
# included_in_household=0 means the account holds money that is not a household
# asset — excluded from every total, allocation, and liquidity calc at this one
# source, never re-filtered per page.
_ACCOUNTS: list[tuple[str, str, str, str, int]] = [
    ("taxable",         "self",     "acct_taxable_01",  "Individual Taxable (Self-Directed)", 1),
    ("taxable",         "external", "acct_taxable_02",  "Individual Taxable (TOD)", 1),
    ("traditional_ira", "external", "acct_trad_ira_01", "Traditional IRA", 1),
    ("roth_ira",        "external", "acct_roth_01",     "Roth IRA", 1),
    ("workplace_plan",  "external", "acct_wkpl_01",     "Former Employer 401(k) (MissionSquare)", 1),
    # 0% vested at a former employer — forfeitable employer money, not the
    # user's asset. Excluded from household totals/allocation/liquidity.
    ("workplace_plan",  "external", "acct_wkpl_02",     "Moody's PPP", 0),
    ("hsa",             "external", "acct_hsa_01",      "HSA", 1),
]


def seed_household_accounts(db_path: str | Path) -> None:
    """UPSERT the 7 household accounts into the accounts table, keyed on pseudonym."""
    db_path = Path(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        for tax_treat, managed_by, pseudonym, display_name, included in _ACCOUNTS:
            acct_type = _TAX_TO_TYPE.get(tax_treat, "other")
            try:
                conn.execute(
                    """
                    INSERT INTO accounts
                        (name, type, custodian, is_active,
                         tax_treatment, pseudonym, display_name, managed_by,
                         included_in_household)
                    VALUES (?, ?, 'Fidelity', 1, ?, ?, ?, ?, ?)
                    ON CONFLICT(pseudonym) DO UPDATE SET
                        name          = excluded.name,
                        type          = excluded.type,
                        custodian     = excluded.custodian,
                        is_active     = excluded.is_active,
                        tax_treatment = excluded.tax_treatment,
                        display_name  = excluded.display_name,
                        managed_by    = excluded.managed_by,
                        included_in_household = excluded.included_in_household
                    """,
                    (display_name, acct_type, tax_treat, pseudonym, display_name, managed_by, included),
                )
            except Exception:
                logger.warning("Failed to upsert account %r — skipping", pseudonym, exc_info=True)
        conn.commit()
        logger.info("Seeded %d household accounts in %s", len(_ACCOUNTS), db_path)
    finally:
        conn.close()
