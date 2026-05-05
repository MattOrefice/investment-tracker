"""
Endowment allocation reference data for SAA comparison panel.

Sources:
  Yale Investments Office, FY2024 Annual Report (yale.edu/investments)
  Princeton University Investment Company (PRINCO), FY2024 Annual Report
  This Portfolio: locked Phase 1 SAA targets

Allocations are approximate rounded figures from public disclosures.
Grouping into comparable categories is the author's classification;
endowments use their own internal taxonomy which differs by institution.
"""
from __future__ import annotations

# ── Allocation categories (display order) ─────────────────────────────────────
CATEGORIES = [
    "Public Equity",
    "Private Equity / VC",
    "Absolute Return / HF",
    "Real Assets",
    "Fixed Income",
    "Cash",
]

# ── Yale FY2024 (rounded from public annual report) ───────────────────────────
# Source: Yale Investments Office FY2024 Annual Report
# Domestic Equity 2% + Foreign Equity 7% = Public Equity 9%
# Leveraged Buyouts 17.5% + Venture Capital 17.5% = Private 35%
# Absolute Return: 22%
# Real Estate 13% + Natural Resources 12% = Real Assets 25%
# Fixed Income: 4.5%  Cash: 4.5%
YALE_FY2024: dict[str, float] = {
    "Public Equity":          9.0,
    "Private Equity / VC":   35.0,
    "Absolute Return / HF":  22.0,
    "Real Assets":           25.0,
    "Fixed Income":           4.5,
    "Cash":                   4.5,
}

# ── PRINCO FY2024 (rounded from public annual report) ────────────────────────
# Source: Princeton University Investment Company FY2024 Annual Report
# US Equity 12% + Intl Equity 13% = Public Equity 25%
# Private Equity: 32%
# Absolute Return (hedge funds): 21%
# Real Assets: 11%
# Fixed Income + Cash: 11%
PRINCO_FY2024: dict[str, float] = {
    "Public Equity":          25.0,
    "Private Equity / VC":    32.0,
    "Absolute Return / HF":   21.0,
    "Real Assets":            11.0,
    "Fixed Income":            8.0,
    "Cash":                    3.0,
}

# ── This portfolio SAA (Phase 1 locked) ──────────────────────────────────────
# All six equity sleeves sum to 72%. No private equity or hedge fund access.
# Real Assets: 10% (VNQ + PDBC). Income: 15% (VGIT + SCHP). Cash: 3%.
THIS_PORTFOLIO: dict[str, float] = {
    "Public Equity":          72.0,
    "Private Equity / VC":     0.0,
    "Absolute Return / HF":    0.0,
    "Real Assets":            10.0,
    "Fixed Income":           15.0,
    "Cash":                    3.0,
}

# ── Named entity list ─────────────────────────────────────────────────────────
ENTITIES = {
    "Yale (FY2024)":        YALE_FY2024,
    "PRINCO (FY2024)":      PRINCO_FY2024,
    "This Portfolio (SAA)": THIS_PORTFOLIO,
}


def get_endowment_data() -> list[dict]:
    """
    Return allocation data as a list of records ready for charting.
    Each record: {entity, category, weight}.
    """
    records = []
    for entity, alloc in ENTITIES.items():
        for cat in CATEGORIES:
            records.append({
                "entity":   entity,
                "category": cat,
                "weight":   alloc.get(cat, 0.0),
            })
    return records


def sanity_check() -> dict[str, float]:
    """Return sum of weights for each entity — should all be 100.0."""
    return {name: sum(alloc.values()) for name, alloc in ENTITIES.items()}
