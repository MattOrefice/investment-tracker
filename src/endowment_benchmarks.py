"""
Endowment allocation reference data for SAA comparison panel.

Sources:
  Yale Investments Office, FY2024 Annual Report (yale.edu/investments)
  Princeton University Investment Company (PRINCO), FY2024 Annual Report
  This Portfolio: the SAA's top-level targets, read from asset_classes

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

# ── This portfolio: the SAA's targets, read from the book ─────────────────────
# The row used to be a typed-in "Phase 1 locked" allocation, 78 / 10 / 10 / 2 cash,
# under the label "This Portfolio (SAA)". The SAA it sat beside targets 80 / 10 / 10
# with no cash (Equity 79.6, Income 10.2, Real Assets 10.2, Cash 0), so the chart
# plotted neither the targets nor the holdings. It now reads the targets.
PARENT_TO_CATEGORY = {
    "Equity":      "Public Equity",
    "Income":      "Fixed Income",
    "Real Assets": "Real Assets",
    "Cash":        "Cash",
}

PORTFOLIO_LABEL = "This Portfolio (SAA targets)"
ENDOWMENTS = {
    "Yale (FY2024)":   YALE_FY2024,
    "PRINCO (FY2024)": PRINCO_FY2024,
}


def this_portfolio_targets() -> dict[str, float]:
    """The SAA's top-level target weights, in percent, on the endowment categories.

    Raises on a parent with a positive target and no category: a silent drop would
    plot an allocation that does not sum to the SAA.
    """
    from src.db import get_connection

    with get_connection() as conn:
        rows = conn.execute(
            "SELECT name, target_weight FROM asset_classes WHERE parent_id IS NULL"
        ).fetchall()
    out = {c: 0.0 for c in CATEGORIES}
    for r in rows:
        w = float(r["target_weight"] or 0.0) * 100
        if w == 0:
            continue
        cat = PARENT_TO_CATEGORY.get(r["name"])
        if cat is None:
            raise ValueError(
                f"SAA parent {r['name']!r} carries a {w:.1f}% target but maps to no "
                f"endowment category. Add it to PARENT_TO_CATEGORY."
            )
        out[cat] += w
    return out


def entities() -> dict[str, dict[str, float]]:
    """The endowments, then this portfolio's SAA targets, in display order."""
    return {**ENDOWMENTS, PORTFOLIO_LABEL: this_portfolio_targets()}


def get_endowment_data() -> list[dict]:
    """
    Return allocation data as a list of records ready for charting.
    Each record: {entity, category, weight}.
    """
    records = []
    for entity, alloc in entities().items():
        for cat in CATEGORIES:
            records.append({
                "entity":   entity,
                "category": cat,
                "weight":   alloc.get(cat, 0.0),
            })
    return records


def sanity_check() -> dict[str, float]:
    """Return sum of weights for each entity — should all be 100.0."""
    return {name: sum(alloc.values()) for name, alloc in entities().items()}
