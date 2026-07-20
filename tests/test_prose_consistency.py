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

def test_identity_non_equity_fraction_algebraic_consistency():
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


def test_identity_non_us_equity_fraction_matches_intl_plus_em():
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

    from src.reports import INTERNATIONAL_SLEEVES

    sleeves = {r["name"]: r["target_weight"] for r in rows}
    _missing = [s for s in INTERNATIONAL_SLEEVES if s not in sleeves]
    assert not _missing, f"international sleeves missing from asset_classes: {_missing}"
    intl_wt = sum(sleeves[s] for s in INTERNATIONAL_SLEEVES)
    em_wt   = sleeves.get("Emerging Markets")

    assert intl_wt is not None, "international sleeves missing from asset_classes"
    assert em_wt   is not None, "'Emerging Markets' sleeve missing from asset_classes"

    non_us_eq = intl_wt + em_wt
    assert 0.20 < non_us_eq < 0.35, (
        f"Non-US equity fraction {non_us_eq:.2f} is outside the expected range [0.20, 0.35]. "
        "The SAA or the sleeve names may have changed."
    )


def test_prose_methodology_sleeve_keys_present():
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
        "Update the prose expressions in pages/2_Performance.py to match the new names."
    )


def test_prose_equity_parent_name_matches():
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


def test_prose_factor_profile_fi_caption_matches_weights():
    """FI sleeve caption weight percentages (60%/40%) are derived from _FI_WEIGHTS.

    pages/4_Factor_Profile.py renders the FI proportions inline using _FI_WEIGHTS.
    This test verifies the rendered string is well-formed and that VGIT carries
    more weight than SCHP (consistent with Core FI 9% > TIPS 6%).
    """
    from src.factors import _FI_WEIGHTS

    fi_tickers = ["VGIT", "SCHP"]
    pct_parts = [round(_FI_WEIGHTS.get(t, 0) * 100) for t in fi_tickers]

    assert sum(pct_parts) == 100, (
        f"FI caption weights don't sum to 100%: {pct_parts}. "
        "Check _FI_WEIGHTS in src/factors.py."
    )
    assert all(p > 0 for p in pct_parts), (
        f"FI caption has a zero-weight ticker: {list(zip(fi_tickers, pct_parts))}."
    )
    assert pct_parts[0] > pct_parts[1], (
        f"Expected VGIT weight > SCHP weight (Core FI 9% > TIPS 6%); "
        f"got VGIT={pct_parts[0]}% SCHP={pct_parts[1]}%."
    )


def test_prose_fi_weights_constant_matches_db():
    """_FI_WEIGHTS in src/factors.py must reflect DB proportions for Core FI and TIPS.

    _FI_WEIGHTS = {"VGIT": 9/15, "SCHP": 6/15} weights the FI sleeve return series
    in regress_fi_sleeve(). If either sleeve's target_weight changes, the constant
    must be updated too — otherwise the factor regression uses wrong weights silently.
    """
    from src.db import get_connection
    from src.factors import _FI_WEIGHTS

    with get_connection() as conn:
        rows = conn.execute(
            "SELECT name, target_weight FROM asset_classes "
            "WHERE name IN ('Core Fixed Income', 'TIPS')"
        ).fetchall()

    db_wts = {r["name"]: r["target_weight"] for r in rows}
    assert "Core Fixed Income" in db_wts, "'Core Fixed Income' sleeve missing from DB"
    assert "TIPS" in db_wts, "'TIPS' sleeve missing from DB"

    total = db_wts["Core Fixed Income"] + db_wts["TIPS"]
    expected_vgit = db_wts["Core Fixed Income"] / total
    expected_schp = db_wts["TIPS"] / total

    assert abs(_FI_WEIGHTS["VGIT"] - expected_vgit) < 1e-6, (
        f"_FI_WEIGHTS['VGIT'] = {_FI_WEIGHTS['VGIT']:.6f} but DB implies "
        f"{expected_vgit:.6f} (Core FI / (Core FI + TIPS)). "
        "Update _FI_WEIGHTS in src/factors.py to match."
    )
    assert abs(_FI_WEIGHTS["SCHP"] - expected_schp) < 1e-6, (
        f"_FI_WEIGHTS['SCHP'] = {_FI_WEIGHTS['SCHP']:.6f} but DB implies "
        f"{expected_schp:.6f} (TIPS / (Core FI + TIPS)). "
        "Update _FI_WEIGHTS in src/factors.py to match."
    )


def test_prose_saa_us_constants_match_db():
    """_SAA_US in src/factors.py must match DB target_weights for US equity sleeves.

    _SAA_US = {"VOO": 16, "SPHQ": 14, "VTV": 8, "AVUV": 7} stores weights as integers
    (percent × 100). Each value must equal round(db_target_weight * 100). Designed to
    work without a DB call at runtime, but tested here to catch SAA weight changes.
    """
    from src.db import get_connection
    from src.factors import _SAA_US

    with get_connection() as conn:
        rows = conn.execute("""
            SELECT s.ticker, ac.target_weight
            FROM securities s
            JOIN asset_classes ac ON s.asset_class_id = ac.asset_class_id
            WHERE s.ticker IN ('VOO', 'SPHQ', 'VTV', 'AVUV')
              AND ac.parent_id IS NOT NULL
        """).fetchall()

    db_wts = {r["ticker"]: r["target_weight"] for r in rows}
    for ticker, pct_int in _SAA_US.items():
        assert ticker in db_wts, (
            f"Ticker '{ticker}' from _SAA_US not found in securities→asset_classes."
        )
        db_pct = round(db_wts[ticker] * 100)
        assert pct_int == db_pct, (
            f"_SAA_US['{ticker}'] = {pct_int}% but DB target_weight = "
            f"{db_wts[ticker]:.4f} ({db_pct}%). "
            "Update _SAA_US in src/factors.py and reseed asset_classes."
        )


def test_prose_methodology_has_no_hardcoded_weight_defaults():
    """pages/2_Performance.py must resolve SAA weights with NO hardcoded fallback.

    INVERTED (PR #1c). This test previously asserted those fallback defaults MATCHED
    the live DB target_weight, extracting the literals from the page source. It
    guarded a mechanism that no longer exists: the page now resolves every SAA weight
    through _require_saa_weight(), which RAISES on an unresolvable name.

    Why the fallbacks went (#127, #1c). Each default was BIT-IDENTICAL to its live
    target — enforced by the very assertion this replaces — so a renamed, split, or
    removed sleeve rendered the stale weight into the prose with no error and nothing
    visibly different. Only membership distinguished a fallback from a correct lookup;
    the value could not. Keeping them in sync did not make the fallback safe, it made
    its silence GUARANTEED.

    So do not reintroduce a default as a fix for a KeyError/ValueError here. The
    lookups raise now, which means a default can no longer be stale — it can only be
    silent. If a sleeve was split or renamed, update the prose to name the new
    sleeves. This test fails if the pattern returns.
    """
    import re

    perf_page = pathlib.Path(__file__).resolve().parent.parent / "pages" / "2_Performance.py"
    source = perf_page.read_text(encoding="utf-8")

    # Same extraction as before, asserted EMPTY rather than matched against the DB.
    pattern = re.compile(
        r"_saa_(?:parents|sleeves)\.get\(\s*['\"]([^'\"]+)['\"]\s*,\s*([^)]+)\)"
    )
    matches = pattern.findall(source)
    assert not matches, (
        "pages/2_Performance.py resolves SAA weights with hardcoded fallback "
        f"defaults again: {[(n, d.strip()) for n, d in matches]}. Use "
        "_require_saa_weight(), which raises and names the missing sleeve. A default "
        "here is bit-identical to the live target, so if it ever fires it renders a "
        "stale weight with no error and no visible change — see #127 / #1c."
    )

    # The sanctioned accessor must still be the one in use, or the guard above passes
    # vacuously on a page that resolves nothing at all.
    assert "_require_saa_weight(" in source, (
        "pages/2_Performance.py no longer calls _require_saa_weight() — the raising "
        "accessor was removed, so this guard would pass vacuously."
    )


# ── Phase 12.1 additions ───────────────────────────────────────────────────────

def test_prose_pdf_methodology_parent_weights_match_db():
    """PDF methodology section parent weight string is DB-derived, not hardcoded.

    templates/quarterly_report.html renders {{ methodology_parent_weights }} from
    _build_methodology_vars(), which lists STRATEGIC parents (target_weight > 0).
    Phase 38a: cash is operational float (target 0), so the string is the three
    strategic parents (Equity / Income / Real Assets) and they sum to 100%.
    """
    import re
    from src.db import get_connection

    with get_connection() as conn:
        rows = conn.execute(
            "SELECT name, target_weight FROM asset_classes "
            "WHERE parent_id IS NULL AND target_weight > 0 ORDER BY asset_class_id"
        ).fetchall()

    result = " / ".join(
        f"{r['name']} {round(r['target_weight'] * 100):.0f}%"
        for r in rows
    )

    for cat in ("Equity", "Income", "Real Assets"):
        assert cat in result, (
            f"Parent category '{cat}' missing from methodology weight string: '{result}'"
        )
    assert "Cash" not in result, (
        f"Cash should be operational (untargeted), not in the strategic parent string: '{result}'"
    )

    pcts = [int(m) for m in re.findall(r"(\d+)%", result)]
    assert sum(pcts) == 100, (
        f"Parent weights in methodology string don't sum to 100%: {result}"
    )


def test_prose_pdf_methodology_sleeve_count_matches_db():
    """PDF methodology section sleeve count (12) is DB-derived, not hardcoded.

    templates/quarterly_report.html renders {{ methodology_sleeve_count }} from
    _build_methodology_vars(), which counts STRATEGIC sleeves (target_weight > 0).
    Phase 38a: cash is operational float (target 0), so cash is excluded.
    Phase 39: the developed-international sleeve split into four, taking the
    strategic count from 9 to 12. The template renders the count from the DB, so
    only this pin moves — it exists so a taxonomy change triggers review.
    """
    from src.db import get_connection

    with get_connection() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM asset_classes WHERE parent_id IS NOT NULL AND target_weight > 0"
        ).fetchone()[0]

    assert count == 12, (
        f"Expected 12 strategic SAA sleeves in asset_classes, got {count}. "
        "If the SAA taxonomy changed, update the methodology template and this test."
    )


def test_prose_pdf_real_assets_benchmark_caption_matches_source():
    """Real Assets benchmark description in PDF template matches _SLEEVE_BENCHMARKS.

    templates/quarterly_report.html renders {{ methodology_ra_bench }} derived
    from _SLEEVE_BENCHMARKS["Real Assets"] in src/benchmarks.py. This test
    verifies the benchmark uses VNQ + DBC (not DJP which was delisted 2020)
    and that the 60/40 split is intact. Note: the portfolio holds PDBC (no-K-1);
    DBC is deliberately used as the benchmark for long price history.
    """
    from src.benchmarks import _SLEEVE_BENCHMARKS

    ra = _SLEEVE_BENCHMARKS.get("Real Assets")
    assert ra is not None, "'Real Assets' key missing from _SLEEVE_BENCHMARKS"
    assert len(ra) == 2, (
        f"Expected 2 tickers in Real Assets benchmark, got {len(ra)}: {ra}"
    )

    tickers = [t for t, _w in ra]
    weights = [w for _t, w in ra]
    weight_map = dict(zip(tickers, weights))

    assert "VNQ" in tickers, f"VNQ not in Real Assets benchmark tickers: {tickers}"
    assert "DBC" in tickers, (
        f"DBC not in Real Assets benchmark tickers: {tickers}. "
        "DJP was delisted 2020; benchmark should use DBC. "
        "If DBC was also replaced, update _SLEEVE_BENCHMARKS and this test."
    )
    assert abs(sum(weights) - 1.0) < 1e-9, (
        f"Real Assets benchmark weights don't sum to 1.0: {weights}"
    )
    assert abs(weight_map["VNQ"] - 0.6) < 1e-9 and abs(weight_map["DBC"] - 0.4) < 1e-9, (
        f"Expected 60/40 VNQ/DBC split; got {weight_map}. "
        "Update _SLEEVE_BENCHMARKS in src/benchmarks.py."
    )


def test_prose_saa_real_assets_db_label_matches_computed_split():
    """asset_classes.benchmark_ticker for Real Assets matches the computed split.

    pages/1_SAA.py renders the sleeve-level (parent_id IS NOT NULL) Real Assets
    row's benchmark_ticker straight from the DB. That label is a separate copy
    of the same truth as _SLEEVE_BENCHMARKS["Real Assets"] in src/benchmarks.py
    (the actual computation) — a prior migration desynced them, writing a 50/50
    label while the computation stayed 60/40. This test compares the DB label
    against the computed split directly rather than pinning either as a fresh
    literal, so the two sources can't silently diverge again.
    """
    from src.db import get_connection
    from src.benchmarks import _SLEEVE_BENCHMARKS

    with get_connection() as conn:
        row = conn.execute(
            "SELECT benchmark_ticker FROM asset_classes "
            "WHERE name = 'Real Assets' AND parent_id IS NOT NULL"
        ).fetchone()

    assert row is not None, "No sleeve-level 'Real Assets' row found in asset_classes"
    label = row["benchmark_ticker"]

    computed = _SLEEVE_BENCHMARKS["Real Assets"]
    expected_label = " + ".join(f"{t} ({w:.0%})" for t, w in computed)

    assert label == expected_label, (
        f"asset_classes.benchmark_ticker for Real Assets is {label!r}, but the "
        f"computed benchmark split (_SLEEVE_BENCHMARKS['Real Assets']) is "
        f"{computed} — expected DB label {expected_label!r}. The DB label has "
        "drifted from the computation; fix the migration in src/db.py, not this test."
    )


def test_prose_pdf_drift_thresholds_match_db():
    """PDF drift threshold description derives from DB tolerance_band values.

    templates/quarterly_report.html lines ~598-599 render the per-sleeve tolerance
    bands from _build_methodology_vars(). This test verifies the DB contains exactly
    the two-tier structure (200 bps / 300 bps) that the prose template assumes.
    """
    from src.db import get_connection

    with get_connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT CAST(ROUND(tolerance_band * 10000) AS INTEGER) AS band_bps "
            "FROM asset_classes WHERE parent_id IS NOT NULL ORDER BY band_bps"
        ).fetchall()

    band_bps_vals = [r["band_bps"] for r in rows]

    assert len(band_bps_vals) == 2, (
        f"Expected exactly 2 distinct tolerance bands, got {len(band_bps_vals)}: {band_bps_vals}. "
        "PDF drift prose assumes a two-tier band structure."
    )
    assert 200 in band_bps_vals, (
        f"Expected a 200 bps (±2%) tolerance band; found: {band_bps_vals}. "
        "Update _build_methodology_vars() fallback and the methodology template."
    )
    assert 300 in band_bps_vals, (
        f"Expected a 300 bps (±3%) tolerance band; found: {band_bps_vals}. "
        "Update _build_methodology_vars() fallback and the methodology template."
    )


def test_prose_drift_threshold_matches_rule_constant():
    """_TOLERANCE_BAND_LARGE_CUTOFF_PCT must equal 10, not the data-inferred value.

    The SAA rule is: sleeves with target weight ≥10% → ±300 bps.  Real Assets sits
    at exactly 10% but is assigned to the 200 bps tier as a deliberate exception, so
    MIN(target_weight) for the 300 bps DB group returns 14 (US Large Quality).
    The PDF methodology prose must describe the rule (≥10%), not the DB minimum (≥14%).
    """
    from src.reports import _TOLERANCE_BAND_LARGE_CUTOFF_PCT, _build_methodology_vars

    assert _TOLERANCE_BAND_LARGE_CUTOFF_PCT == 10, (
        f"Rule constant is {_TOLERANCE_BAND_LARGE_CUTOFF_PCT}, expected 10. "
        "The SAA spec says ≥10% target weight → ±300 bps tolerance band."
    )

    vars_ = _build_methodology_vars()
    assert vars_["methodology_drift_large_min_pct"] == 10, (
        f"_build_methodology_vars() returned drift_large_min_pct="
        f"{vars_['methodology_drift_large_min_pct']}, expected 10. "
        "Must use _TOLERANCE_BAND_LARGE_CUTOFF_PCT, not MIN(target_weight) from DB."
    )


# ── Phase 13 additions — SAA investment thesis paragraph ─────────────────────

def test_saa_thesis_has_no_hardcoded_weight_defaults():
    """pages/1_SAA.py must resolve thesis weights with NO hardcoded fallback.

    INVERTED (PR #1c). This test previously asserted the next(..., fallback) defaults
    in the investment-thesis paragraph MATCHED the live DB target_weight, extracting
    the literals from the page source. It guarded a mechanism that no longer exists:
    the page now resolves every weight through _require_weight(), which RAISES on an
    unresolvable name.

    Why the fallbacks went (#127, #1c). Each default was BIT-IDENTICAL to its live
    target — enforced by the very assertion this replaces — so a renamed, split, or
    removed sleeve rendered the stale weight into the thesis prose with no error and
    nothing visibly different. Only membership distinguished a fallback from a correct
    lookup; the value could not. Keeping them in sync did not make the fallback safe,
    it made its silence GUARANTEED.

    So do not reintroduce a default as a fix for a StopIteration/ValueError here. The
    lookups raise now, which means a default can no longer be stale — it can only be
    silent. If a sleeve was split or renamed, rewrite the thesis prose to name the new
    sleeves. This test fails if the pattern returns.
    """
    import re

    saa_page = pathlib.Path(__file__).resolve().parent.parent / "pages" / "1_SAA.py"
    source = saa_page.read_text(encoding="utf-8")

    # Same extraction as before, asserted EMPTY rather than matched against the DB.
    pattern = re.compile(
        r'\[?"name"\]\s*==\s*"([^"]+)"\)\s*,\s*([\d.]+\s*/\s*[\d.]+|[\d.]+)\s*\)'
    )
    matches = pattern.findall(source)
    assert not matches, (
        "pages/1_SAA.py resolves thesis weights with hardcoded fallback defaults "
        f"again: {matches}. Use _require_weight(), which raises and names the missing "
        "sleeve. A default here is bit-identical to the live target, so if it ever "
        "fires it renders a stale weight with no error and no visible change — see "
        "#127 / #1c."
    )

    assert "_require_weight(" in source, (
        "pages/1_SAA.py no longer calls _require_weight() — the raising accessor was "
        "removed, so this guard would pass vacuously."
    )


def test_saa_thesis_cape_label_matches_computed_percentile():
    """CAPE language in the SAA thesis paragraph is consistent with percentile_label() output.

    pages/1_SAA.py calls _load_cape_label() which computes the CAPE percentile from the
    Shiller dataset and passes it through percentile_label(). This test verifies:
    1. The cached CAPE series produces a valid percentile (not NaN / degenerate).
    2. The resulting label is one of the recognized qualitative buckets.
    3. CAPE is above the historical median (a sanity check — at 40x it should be far above).
    """
    from src.shiller import get_cape_series
    from src.macro import percentile
    from src.prose_helpers import percentile_label

    VALID_LABELS = {
        "historically low",
        "below the historical median",
        "near the historical median",
        "elevated historically",
        "very high historically",
        "historically extreme",
    }

    s = get_cape_series()
    cv = float(s.dropna().iloc[-1])
    pct = percentile(s.dropna(), cv)
    label = percentile_label(pct)

    assert label in VALID_LABELS, (
        f"percentile_label({pct:.1f}) returned '{label}', not in {VALID_LABELS}. "
        "SAA thesis uses this label dynamically — check percentile_label() thresholds in "
        "src/prose_helpers.py."
    )
    assert pct > 50, (
        f"CAPE at {cv:.1f}x is at only the {pct:.0f}th historical percentile — below the median. "
        "At sub-median CAPE, the SAA thesis claim of 'elevated valuations' is questionable. "
        "Review the thesis paragraph in pages/1_SAA.py."
    )


def test_saa_thesis_above_40_cape_reference_still_relevant():
    """Soft-warning test: static 'CAPE above 40' reference in SAA thesis should be reviewed
    if current CAPE drops materially below 35.

    pages/1_SAA.py contains a static empirical sentence: 'historical CAPE readings above 40
    have been associated with low or negative forward 10-year real returns.' This is accurate
    at extreme CAPE levels but becomes misleading if CAPE falls significantly. This test does
    NOT fail CI — it issues a pytest warning when the cached CAPE drops below 35, signaling
    that the static prose reference may need updating.
    """
    import warnings
    from src.shiller import get_cape_series

    REVIEW_THRESHOLD = 35.0

    try:
        s = get_cape_series()
        cv = float(s.dropna().iloc[-1])
    except Exception:
        return  # CAPE data unavailable — skip silently

    if cv < REVIEW_THRESHOLD:
        warnings.warn(
            f"Current CAPE ({cv:.1f}x) has dropped below {REVIEW_THRESHOLD}x. "
            "Review the static 'CAPE above 40' sentence in the SAA thesis paragraph "
            "(pages/1_SAA.py) and the '1929 and 1999 peaks' comparator — both may no "
            "longer accurately describe current valuation conditions.",
            UserWarning,
            stacklevel=2,
        )


# ── Phase 38a — ex-cash SAA guards ─────────────────────────────────────────────

def test_sleeve_weights_match_db():
    """The two SAA target sources must agree (Phase 38a risk #2).

    src.asset_evaluation.SLEEVE_WEIGHTS (the ex-cash MV-analysis weights) must
    equal the DB strategic targets sleeve-for-sleeve, so a change to one cannot
    silently diverge from the other. The benchmark-proxy subsystem abbreviates
    'International ...' to 'Intl ...'; this map is the bridge.
    """
    from src.db import get_connection
    from src.asset_evaluation import SLEEVE_WEIGHTS

    _AE_TO_DB = {
        "US Large Core":     "US Large Core",
        "US Large Quality":  "US Large Quality",
        "US Large Value":    "US Large Value",
        "US Small Cap":      "US Small Cap",
        "Intl Core":         "International Core",
        "Intl Quality":      "International Quality",
        "Intl Large Value":  "International Large Value",
        "Intl Small Value":  "International Small Value",
        "Emerging Markets":  "Emerging Markets",
        "Core Fixed Income": "Core Fixed Income",
        "TIPS":              "TIPS",
        "Real Assets":       "Real Assets",
    }

    with get_connection() as conn:
        db = {
            r["name"]: r["target_weight"]
            for r in conn.execute(
                "SELECT name, target_weight FROM asset_classes "
                "WHERE parent_id IS NOT NULL AND target_weight > 0"
            ).fetchall()
        }

    assert len(SLEEVE_WEIGHTS) == 12, f"Expected 12 ex-cash sleeve weights, got {len(SLEEVE_WEIGHTS)}"
    assert len(db) == 12, f"Expected 12 strategic DB sleeves, got {len(db)}"
    for ae_name, db_name in _AE_TO_DB.items():
        assert db_name in db, f"DB strategic sleeve '{db_name}' missing"
        assert abs(SLEEVE_WEIGHTS[ae_name] - db[db_name]) < 1e-6, (
            f"SLEEVE_WEIGHTS['{ae_name}']={SLEEVE_WEIGHTS[ae_name]:.6f} != "
            f"DB '{db_name}'={db[db_name]:.6f}. The two SAA target sources diverged — "
            "update src/asset_evaluation.py and/or the DB/seed together."
        )


def test_ex_cash_denominator_drops_cash_and_sums_to_one():
    """get_sleeve_weights_on_date measures strategic weights ex-cash (Phase 38a).

    Cash / SPAXX must NOT be a strategic row; the 9 strategic weights are a share
    of invested value (sum ~1.0); operational cash is exposed on .attrs with
    invested_value = total_value - cash_mv.
    """
    from datetime import date
    from src.holdings import get_sleeve_weights_on_date

    sw = get_sleeve_weights_on_date(date.today().isoformat())
    if sw.empty:
        return  # no holdings in this DB — nothing to assert

    assert "Cash / SPAXX" not in sw.index, "cash must be excluded from strategic rows"
    assert abs(sw["Actual Weight"].sum() - 1.0) < 0.01, (
        f"ex-cash strategic weights should sum to ~1.0, got {sw['Actual Weight'].sum():.4f}"
    )
    for key in ("total_value", "cash_mv", "invested_value", "cash_weight_of_total"):
        assert key in sw.attrs, f"operational-cash attr '{key}' missing from sleeve frame"
    assert abs(sw.attrs["invested_value"] - (sw.attrs["total_value"] - sw.attrs["cash_mv"])) < 0.01
