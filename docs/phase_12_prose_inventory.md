# Phase 12 — Prose-vs-Data Inventory

Audit of hardcoded numeric literals in narrative/UI text that could silently diverge from the
database. Produced during Phase 12 Section 3 (2026-05-07).

---

## Priority 1 — Hardcoded in `src/` constants (could diverge from DB without UI warning)

### P1-A: `_FI_WEIGHTS` in `src/factors.py` line 92

```python
_FI_WEIGHTS = {"VGIT": 9.0 / 15.0, "SCHP": 6.0 / 15.0}
```

Used in `regress_fi_sleeve()` to weight-average VGIT and SCHP daily returns.
Derived from DB: Core Fixed Income target = 9%, TIPS target = 6%; proportional weights = 60/40.
**Risk**: If Core FI or TIPS target_weight changes, this constant silently drifts.
**Guard**: `test_prose_fi_weights_constant_matches_db` in `test_prose_consistency.py`.
**Resolution**: Keep hardcoded (portability requirement documented in comment); test validates.

---

### P1-B: `_SAA_US` in `src/factors.py` line 101

```python
_SAA_US = {"VOO": 16, "SPHQ": 14, "VTV": 8, "AVUV": 7}  # in percent
```

Used to weight the US equity sleeve return series without a DB call (portability design intent,
documented in comment at lines 97–113). Values are DB `target_weight × 100` for each US sleeve.
**Risk**: If US sleeve weights change, this constant silently drifts.
**Guard**: `test_prose_saa_us_constants_match_db` in `test_prose_consistency.py`.
**Resolution**: Keep hardcoded; test validates.

---

## Priority 2 — Fallback defaults in page code (guarded by DB lookup, but fallbacks could diverge)

### P2-A: Methodology paragraph `.get()` defaults in `pages/4_Performance.py` lines 790–793

```python
f"(VTV at {_saa_sleeves.get('US Large Value', 0.08)*100:.0f}%), "
f"small-cap value (AVUV at {_saa_sleeves.get('US Small Cap', 0.07)*100:.0f}%), "
f"emerging markets ({_saa_sleeves.get('Emerging Markets', 0.08)*100:.0f}%), "
f"real assets ({_saa_sleeves.get('Real Assets', 0.10)*100:.0f}%), "
f"TIPS ({_saa_sleeves.get('TIPS', 0.06)*100:.0f}%), "
f"~{_saa_parents.get('Equity', 0.72)*100:.0f}/{_non_eq_pct*100:.0f} equity-vs-other"
```

Primary values come from `_saa_sleeves` dict loaded from DB. Fallbacks only activate if DB
lookup fails. Currently active: all keys exist in DB (verified by `test_prose_methodology_sleeve_keys_present`).
**Risk**: Fallback defaults are stale if weights change and DB lookup ever degrades.
**Guard**: `test_prose_methodology_weight_defaults_match_db` in `test_prose_consistency.py`.
**Resolution**: Keep inline `.get()` pattern; test validates fallback values vs live DB.

---

### P2-B: RF rate in `pages/4_Performance.py` line 455

```python
"Sharpe and Sortino use RF = 4.5% (current cash yield). "
```

Hardcoded `"4.5%"` string. The actual default in `src/performance.py` `compute_risk_metrics()`
is `rf_annual=0.045`. Both must match.
**Guard**: `test_identity_rf_default_matches_caption_disclosure` in `test_identity_layer1.py` (already exists).
**Resolution**: Already tested — no new action needed.

---

## Priority 3 — Static ETF/market data (not in DB, expected stable)

### P3-A: Duration values in `pages/6_Positioning.py` line 82

```python
"Duration sourced from ETF fact-sheet values (VGIT: 5.5 yrs, SCHP: 6.8 yrs per Vanguard/Schwab Q1 2026)."
```

These are ETF fact-sheet values, not in the DB. They update quarterly as the ETF's holdings
roll. Caption already notes the data-as-of date.
**Resolution**: No test; update caption each quarter when reviewing positioning.

### P3-B: ETF expense ratios in `pages/2_Research.py`

ERs (0.09%, 0.70%, 61 bps) are correct as of Phase 2 lock. ERs don't change frequently.
**Resolution**: No test; review during annual ETF evaluation.

### P3-C: `_SAA_US_TOTAL = 45` in `src/factors.py` line 102

Derived directly from `sum(_SAA_US.values())` — not hardcoded independently.
**Resolution**: No action; already computed dynamically.

---

## Phase 12.1 additions — surfaced by secondary grep audit (2026-05-07)

### P1-C: `pages/7_Factor_Profile.py` line 197 — FI sleeve caption

```python
f"Tickers: {', '.join(fi_result['tickers'])} (60% / 40%, proportional to SAA)"
```

The "60% / 40%" string is hardcoded but derives from the same `_FI_WEIGHTS` constant that
`regress_fi_sleeve()` uses. Both live in `src/factors.py`; if the proportions change the
caption silently diverges from the actual regression weights.
**Risk**: Caption out of sync with regression if `_FI_WEIGHTS` is updated.
**Fix**: Template the percentages from `_FI_WEIGHTS` at render time.
**Guard**: `test_prose_factor_profile_fi_caption_matches_weights` in `test_prose_consistency.py`.

---

### P2-C: `templates/quarterly_report.html` lines 617–618 — parent category weights + sleeve count

```html
<dd>Equity 72% / Income 15% / Real Assets 10% / Cash 3%. Ten sleeves with tolerance
bands (±2–3%). Full rationale in the SAA section of the application.</dd>
```

Parent weights and sleeve count are hardcoded in the PDF methodology section.
**Risk**: PDF methodology section silently diverges from DB if SAA is ever revised.
**Fix**: Pass `methodology_parent_weights` and `methodology_sleeve_count` from `_build_methodology_vars()` in `src/reports.py`; template the values.
**Guards**: `test_prose_pdf_methodology_parent_weights_match_db`, `test_prose_pdf_methodology_sleeve_count_matches_db`.

---

### P2-D: `templates/quarterly_report.html` line 587 — Real Assets benchmark tickers

```html
of benchmark tickers (SPY, QUAL, IWD, IWM, EFA, EEM, IEF, TIP, 50% VNQ + 50% DBC, BIL)
```

"50% VNQ + 50% DBC" is hardcoded. **Diagnosis: branch 3a** — DBC *is* the correct benchmark
ticker (`src/benchmarks.py` comment explains DJP was delisted 2020, replaced with DBC). The
template text is factually correct but needs: (a) the split refactored from `_SLEEVE_BENCHMARKS`,
(b) a clarifying sentence noting DBC (K-1) is the benchmark while PDBC (no-K-1) is the holding.
**Fix**: Pass `methodology_ra_bench` from `_build_methodology_vars()`; add clarifying sentence.
**Guard**: `test_prose_pdf_real_assets_benchmark_caption_matches_source`.

---

### P2-E: `templates/quarterly_report.html` lines 598–599 — drift thresholds

```html
(±300 bps for sleeves with ≥10% target weight; ±200 bps for smaller sleeves)
```

Tolerance band thresholds are hardcoded in PDF prose. The same values are stored in the DB
`asset_classes.tolerance_band` column and already tested in `test_bound_drift_bands_match_saa_rule`.
**Fix**: Pass `methodology_drift_large_bps`, `methodology_drift_small_bps`,
`methodology_drift_large_min_pct` from `_build_methodology_vars()`; template the values.
**Guard**: `test_prose_pdf_drift_thresholds_match_db`.

---

## Out of scope (not hardcoded prose numerics)

- Statistical thresholds (1.96, 2.58, 1.65) in `src/prose_helpers.py` — domain constants, not data
- Percentile thresholds (25, 40, 55, 75, 90) in `src/prose_helpers.py` — domain constants
- Test file reference comments (e.g., `# "4.5%"` in `test_identity_layer1.py`) — documentation only
- DB tolerance band rule (±3%/±2%) — already tested in `test_bound_drift_bands_match_saa_rule`
- CAPE formula coefficients (`-0.070`, `0.066`) — calibrated to Shiller long-run data, not DB

---

## Summary

| ID   | Location                    | Value(s)              | Risk     | Guard test                                       |
|------|-----------------------------|-----------------------|----------|--------------------------------------------------|
| P1-A | `src/factors._FI_WEIGHTS`   | 9/15, 6/15            | Silent   | `test_prose_fi_weights_constant_matches_db`      |
| P1-B | `src/factors._SAA_US`       | 16, 14, 8, 7 (%)      | Silent   | `test_prose_saa_us_constants_match_db`           |
| P2-A | `pages/4_Performance.py`    | 0.08, 0.07, 0.10, etc.| Fallback | `test_prose_methodology_weight_defaults_match_db`|
| P2-B | `pages/4_Performance.py`    | 4.5%                  | Caption  | `test_identity_rf_default_matches_caption_disclosure` (existing) |
| P3-A | `pages/6_Positioning.py`    | 5.5, 6.8 yrs          | Quarterly| Manual review                                    |
| P3-B | `pages/2_Research.py`       | 0.09%, 0.70%          | Annual   | Manual review                                    |
| P1-C | `pages/7_Factor_Profile.py` | 60% / 40%             | Caption  | `test_prose_factor_profile_fi_caption_matches_weights` |
| P2-C | `templates/quarterly_report.html:617` | 72/15/10/3, "Ten" | Silent | `test_prose_pdf_methodology_parent_weights_match_db`, `test_prose_pdf_methodology_sleeve_count_matches_db` |
| P2-D | `templates/quarterly_report.html:587` | 50% VNQ+DBC   | Silent   | `test_prose_pdf_real_assets_benchmark_caption_matches_source` |
| P2-E | `templates/quarterly_report.html:598` | ±300/±200 bps | Silent   | `test_prose_pdf_drift_thresholds_match_db` |
