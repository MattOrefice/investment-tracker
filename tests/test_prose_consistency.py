"""Prose-vs-table consistency tests.

Section 4 of Phase 10. Verifies that:
1. prose_helpers functions obey their documented thresholds
2. DB-derived fractions used in prose (non-equity %, non-US equity %)
   are algebraically consistent with the underlying asset_classes table
3. Sleeve target weights referenced in the methodology paragraph match
   the DB values that the prose expressions pull from
"""
from __future__ import annotations

import sys
import pathlib

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.prose_helpers import percentile_label, significance_label


# ── significance_label unit tests ─────────────────────────────────────────────

def test_significance_label_1pct():
    """|t| >= 2.58 → 1% level."""
    assert significance_label(2.58) == "statistically significant at the 1% level"
    assert significance_label(-3.5) == "statistically significant at the 1% level"
    assert significance_label(10.0) == "statistically significant at the 1% level"


def test_significance_label_5pct():
    """1.96 <= |t| < 2.58 → 5% level."""
    assert significance_label(1.96) == "statistically significant at the 5% level"
    assert significance_label(2.57) == "statistically significant at the 5% level"
    assert significance_label(-2.0) == "statistically significant at the 5% level"


def test_significance_label_10pct():
    """1.65 <= |t| < 1.96 → marginal (10% level)."""
    assert significance_label(1.65) == "marginally significant (10% level)"
    assert significance_label(1.95) == "marginally significant (10% level)"
    assert significance_label(-1.7) == "marginally significant (10% level)"


def test_significance_label_not_significant():
    """|t| < 1.65 → not distinguishable from zero."""
    assert significance_label(0.0)  == "not statistically distinguishable from zero"
    assert significance_label(1.64) == "not statistically distinguishable from zero"
    assert significance_label(-1.0) == "not statistically distinguishable from zero"


def test_significance_label_monotone():
    """Higher |t| must never yield a weaker label."""
    thresholds = [0.5, 1.64, 1.65, 1.95, 1.96, 2.57, 2.58, 5.0]
    labels = [significance_label(t) for t in thresholds]
    tier_order = [
        "not statistically distinguishable from zero",
        "marginally significant (10% level)",
        "statistically significant at the 5% level",
        "statistically significant at the 1% level",
    ]
    tiers = [tier_order.index(lbl) for lbl in labels]
    assert tiers == sorted(tiers), f"Labels not monotone with t: {list(zip(thresholds, labels))}"


# ── percentile_label unit tests ────────────────────────────────────────────────

def test_percentile_label_extreme():
    """pct > 90 → historically extreme."""
    assert percentile_label(91) == "historically extreme"
    assert percentile_label(100) == "historically extreme"


def test_percentile_label_very_high():
    """75 < pct <= 90 → very high historically."""
    assert percentile_label(76) == "very high historically"
    assert percentile_label(90) == "very high historically"


def test_percentile_label_elevated():
    """55 < pct <= 75 → elevated historically."""
    assert percentile_label(56) == "elevated historically"
    assert percentile_label(75) == "elevated historically"


def test_percentile_label_near_median():
    """40 < pct <= 55 → near the historical median."""
    assert percentile_label(41) == "near the historical median"
    assert percentile_label(55) == "near the historical median"


def test_percentile_label_below_median():
    """25 < pct <= 40 → below the historical median."""
    assert percentile_label(26) == "below the historical median"
    assert percentile_label(40) == "below the historical median"


def test_percentile_label_historically_low():
    """pct <= 25 → historically low."""
    assert percentile_label(0)  == "historically low"
    assert percentile_label(25) == "historically low"


def test_percentile_label_monotone():
    """Higher percentile must yield a tier >= lower percentile."""
    vals = [0, 25, 26, 40, 41, 55, 56, 75, 76, 90, 91]
    labels = [percentile_label(v) for v in vals]
    tier_order = [
        "historically low",
        "below the historical median",
        "near the historical median",
        "elevated historically",
        "very high historically",
        "historically extreme",
    ]
    tiers = [tier_order.index(lbl) for lbl in labels]
    assert tiers == sorted(tiers), f"Percentile labels not monotone: {list(zip(vals, labels))}"


# ── DB-backed prose consistency tests ─────────────────────────────────────────

def test_non_equity_fraction_algebraic_consistency():
    """1 - equity_weight == sum of non-equity parent weights (Income + Real Assets + Cash).

    The Performance page L291 computes _non_eq_pct = 1 - _saa_parents['Equity'].
    This must equal the sum of the other three parent weights. Verifies that the
    SAA table is internally consistent (no phantom allocation gap).
    """
    from src.db import get_connection

    with get_connection() as conn:
        rows = conn.execute(
            "SELECT name, target_weight FROM asset_classes WHERE parent_id IS NULL"
        ).fetchall()

    parents = {r["name"]: r["target_weight"] for r in rows}
    equity_wt = parents.get("Equity", None)
    assert equity_wt is not None, "Equity parent row missing from asset_classes"

    non_eq_from_complement = 1.0 - equity_wt
    non_eq_from_sum = sum(w for name, w in parents.items() if name != "Equity")

    assert abs(non_eq_from_complement - non_eq_from_sum) < 1e-9, (
        f"1 - Equity ({non_eq_from_complement:.4f}) != sum of other parents "
        f"({non_eq_from_sum:.4f}). SAA parent weights don't sum to 1."
    )


def test_non_us_equity_fraction_matches_intl_plus_em():
    """Non-US equity fraction = Intl Developed + Emerging Markets target weights.

    The Performance page L292 computes _non_us_eq as the sum of these two
    sleeve target weights. This test checks that both sleeves exist in the DB
    and that their sum is the expected non-US equity fraction (27% for the
    current SAA).
    """
    from src.db import get_connection

    with get_connection() as conn:
        rows = conn.execute(
            "SELECT name, target_weight FROM asset_classes WHERE parent_id IS NOT NULL"
        ).fetchall()

    sleeves = {r["name"]: r["target_weight"] for r in rows}
    intl_wt = sleeves.get("International Developed")
    em_wt   = sleeves.get("Emerging Markets")

    assert intl_wt is not None, "'International Developed' sleeve missing from asset_classes"
    assert em_wt   is not None, "'Emerging Markets' sleeve missing from asset_classes"

    non_us_eq = intl_wt + em_wt
    assert 0.20 < non_us_eq < 0.35, (
        f"Non-US equity fraction {non_us_eq:.2f} is outside the expected range [0.20, 0.35]. "
        "The SAA or the sleeve names may have changed."
    )


def test_methodology_prose_sleeve_keys_present():
    """All sleeve names used in the methodology paragraph exist in the SAA DB.

    The Performance page methodology paragraph references 'US Large Value',
    'US Small Cap', 'Emerging Markets', 'Real Assets', and 'TIPS' by name
    as dict keys in _saa_sleeves. If any sleeve is renamed or removed, the
    .get() calls fall back to hard-coded defaults — this test catches that.
    """
    from src.db import get_connection

    with get_connection() as conn:
        rows = conn.execute(
            "SELECT name FROM asset_classes WHERE parent_id IS NOT NULL"
        ).fetchall()

    sleeve_names = {r["name"] for r in rows}
    required = {
        "US Large Value",
        "US Small Cap",
        "Emerging Markets",
        "Real Assets",
        "TIPS",
    }
    missing = required - sleeve_names
    assert not missing, (
        f"Sleeve names referenced in methodology paragraph are absent from DB: {missing}. "
        "Update the prose expressions in pages/4_Performance.py to match the new names."
    )


def test_equity_parent_name_matches_prose():
    """Equity parent name is 'Equity' — the key used in _saa_parents.get('Equity', ...)."""
    from src.db import get_connection

    with get_connection() as conn:
        names = [r[0] for r in conn.execute(
            "SELECT name FROM asset_classes WHERE parent_id IS NULL"
        ).fetchall()]

    assert "Equity" in names, (
        f"'Equity' parent not found in asset_classes. Found: {names}. "
        "The Performance page prose uses _saa_parents.get('Equity', 0.72) — "
        "if the parent was renamed, the fallback default will be used silently."
    )
