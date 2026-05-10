# Phase 12 Test Inventory

Baseline as of Phase 11 close (commit d970c65). 290 tests collected, 274 pass, 16 skip.

## Naming convention (adopted Phase 12 Section 0)

| Prefix | Layer | Blocking? | Description |
|---|---|---|---|
| `test_identity_*` | 1 | Yes | Mathematical identity that must hold by construction. Never needs updating as data refreshes. |
| `test_bound_*` | 2 | Warn | Reasonability bound. May legitimately fail on unusual data; fails hard only if exceeded by wide margin. |
| `test_prose_*` | 3 | Yes | Prose text equals computed source value. Fails if a number in interpretive text diverges from the tile/table it references. |
| `test_render_*` | — | Yes | Streamlit AppTest. Skipped when test runtime not bootstrapped (local empty-DB). |
| `test_snapshot_*` | — | Soft | Pinned values for current data state. Expected to drift; update intentionally. |

Tests that don't fit a category cleanly are left with their original names and annotated below.

---

## File-by-file inventory

### tests/test_returns.py
**Phase introduced:** 0–1 (scaffolding)  
**Lines:** 102 | **Tests collected:** 9  
**Type:** Unit — TWR/Dietz math  
**Skip conditions:** None

| Test | Category | Notes |
|---|---|---|
| test_no_cashflows_daily_linked | unit | Behavioral: TWR returns 0 when no value change |
| test_no_cashflows_modified_dietz | unit | |
| test_midperiod_deposit_daily_linked | unit | Hand-calc vs computed |
| test_midperiod_deposit_modified_dietz | unit | |
| `test_identity_gips_multiperiod_chain_link` | **identity** | Chain-link multiplication identity — renamed Phase 12 S0 |
| test_annualize_one_year | unit | |
| test_annualize_two_years | unit | |
| test_period_return_si_matches_full | unit | |
| test_period_return_insufficient_data | unit | Edge-case guard |

---

### tests/test_attribution.py
**Phase introduced:** 4 Session 2  
**Lines:** 631 | **Tests collected:** 16  
**Type:** Unit (synthetic) + Integration (DB-backed, skip locally)  
**Skip conditions:** `pytest.skip("One or more series is empty — skipped in local/empty-DB mode")`; multiple skip paths gated on non-empty portfolio series

| Test | Category | Notes |
|---|---|---|
| test_two_sleeve_hand_calculation | unit | Hand-calc BF with synthetic two-sleeve portfolio |
| `test_identity_bf_effects_sum_to_active_return` | **identity** | BF alloc+sel+interaction ≡ active return; renamed Phase 12 S0 |
| test_equal_weights_no_allocation_effect | unit | Edge case: equal weights → zero allocation effect |
| test_equal_returns_no_selection_effect | unit | Edge case: equal returns → zero selection effect |
| `test_identity_stage1_plus_stage2_equals_total` | **identity** | Two-stage reconciliation; renamed Phase 12 S0 |
| `test_identity_stage1_sleeve_contributions_sum_to_total` | **identity** | Stage 1 decomposition; renamed Phase 12 S0 |
| test_stage1_hand_calculation | unit | Hand-calc stage 1 synthetic |
| test_naive_benchmark_60_40_composition | unit | May skip (price data) |
| test_stage1_distinguishes_across_windows | unit | May skip (benchmark data) |
| test_two_stage_reconciles_against_spy | unit | |
| test_stage1_distinguishes_across_naive_benchmarks | unit | |
| `test_identity_ps_two_stage_si_60_40` | **identity** | DB-backed; skips locally; renamed Phase 12 S0 |
| `test_identity_ps_two_stage_1y_60_40` | **identity** | DB-backed; skips locally; renamed Phase 12 S0 |
| `test_identity_ps_two_stage_si_spy` | **identity** | DB-backed; skips locally; renamed Phase 12 S0 |
| `test_identity_bf_sum_reconciles_to_stage2` | **identity** | DB-backed; skips locally; renamed Phase 12 S0 |
| test_bf_per_sleeve_returns_are_total_return | unit | DB-backed; skips locally |

---

### tests/test_performance.py
**Phase introduced:** 8k (VaR/CVaR)  
**Lines:** 250 | **Tests collected:** 10  
**Type:** Unit — VaR/CVaR math, window filtering, alpha CI formatting  
**Skip conditions:** None

| Test | Category | Notes |
|---|---|---|
| test_var_95_positive_loss | unit | VaR must be positive loss magnitude |
| test_cvar_ge_var | unit | CVaR ≥ VaR |
| test_var_known_normal_distribution | unit | ~1.645σ for N(0,σ) |
| test_cvar_known_normal_distribution | unit | ~σ×φ(1.645)/0.05 |
| test_risk_metrics_returns_var_cvar_keys | unit | Key presence check |
| test_compute_risk_metrics_short_windows | unit | 5-window coverage: all keys present + finite |
| test_risk_metrics_distinguish_across_windows | unit | Window filtering collapses → distinct n_days and Sharpe |
| test_alpha_ci_str_format | unit | Output format check |
| test_alpha_ci_str_known_values | unit | Hand-calc SE → CI bounds |
| test_alpha_ci_str_no_hac_bse | unit | Fallback when SE unavailable |

*Note: tests_compute_risk_metrics_short_windows and test_risk_metrics_distinguish_across_windows are de facto bounds tests for the window-filtering behavior but named before the convention was established. Leave as-is.*

---

### tests/test_prose_consistency.py
**Phase introduced:** 10 Section 4  
**Lines:** 222 | **Tests collected:** 16  
**Type:** Unit (helper functions) + Identity/Prose (DB-backed)  
**Skip conditions:** None

| Test | Category | Notes |
|---|---|---|
| test_significance_label_1pct | unit | Helper function boundary |
| test_significance_label_5pct | unit | |
| test_significance_label_10pct | unit | |
| test_significance_label_not_significant | unit | |
| test_significance_label_monotone | unit | |
| test_percentile_label_extreme | unit | |
| test_percentile_label_very_high | unit | |
| test_percentile_label_elevated | unit | |
| test_percentile_label_near_median | unit | |
| test_percentile_label_below_median | unit | |
| test_percentile_label_historically_low | unit | |
| test_percentile_label_monotone | unit | |
| `test_identity_non_equity_fraction_algebraic_consistency` | **identity** | 1 − Equity ≡ sum of other parent weights; renamed Phase 12 S0 |
| `test_identity_non_us_equity_fraction_matches_intl_plus_em` | **identity** | Intl + EM = non-US equity total; renamed Phase 12 S0 |
| `test_prose_methodology_sleeve_keys_present` | **prose** | Keys used in methodology paragraph exist in DB; renamed Phase 12 S0 |
| `test_prose_equity_parent_name_matches` | **prose** | 'Equity' parent name used by prose dict lookup; renamed Phase 12 S0 |

---

### tests/test_factors.py
**Phase introduced:** 8c (FF5 regression)  
**Lines:** 829 | **Tests collected:** ~55 (includes parametrized expansion of test_nw_lags_formula)  
**Type:** Unit — parser, OLS math, regression structure, prose helpers  
**Skip conditions:** None

*Not systematically renamed: tests cover parsing internals, OLS computation, and prose formatting — none cleanly fit identity/bound/prose/render/snapshot. See individual test names for specifics.*

| Test group | Category | Notes |
|---|---|---|
| test_ff_parser_* (7 tests) | unit | CSV parsing correctness |
| test_nw_lags_formula (parametrized) | unit | Newey-West lag formula |
| test_sig_marker_* (4 tests) | unit | Significance star formatter |
| test_beta_*/test_alpha_*/test_r_squared_* (5 tests) | unit | OLS regression recovery with synthetic data |
| test_newey_west_se_* | unit | HAC SE > OLS SE under AR(1) |
| test_alignment_produces_trading_days_only | unit | Inner-join drops weekend rows |
| test_run_sleeve_regressions_* (4 tests) | unit | Structure and key checks |
| test_us_sleeve_* (2 tests) | unit | Embedded weights, non-empty DB |
| test_developed_sleeve_prose_* | unit | Korea disclosure present |
| test_run_benchmark_attribution_* (4 tests) | unit | Benchmark regression structure |
| test_parse_momentum_* (4 tests) | unit | Momentum CSV parsing |
| test_run_sleeve_regressions_mom_* | unit | Structure check |
| test_global_region_in_factor_config | unit | Config key present |
| test_fi_term_credit_* (2 tests) | unit | FI regression betas |
| test_regress_fi_sleeve_* (2 tests) | unit | FI regression structure |
| test_em_disclosure_is_non_empty_string | unit | Non-empty string check |
| test_factor_section_values_match_raw_result | unit | Section builder round-trip |
| test_fetch_factors_* (3 tests) | unit | Network retry logic |

---

### tests/test_positioning.py
**Phase introduced:** 8a  
**Lines:** 308 | **Tests collected:** 19  
**Type:** Unit — positioning helpers  
**Skip conditions:** None

*Not renamed: tests are unit tests of positioning helper functions (tilts, duration, scenarios, style box). They don't fit identity/prose/bound cleanly.*

---

### tests/test_reports.py
**Phase introduced:** 6 polish  
**Lines:** 311 | **Tests collected:** 19  
**Type:** Unit — report builder helpers + chart structure  
**Skip conditions:** None

*Not renamed: drift_status_* are unit tests of a boundary-checking helper. chart tests are structural.*

---

### tests/test_cache.py
**Phase introduced:** 8j.1 (quarter-snapshot price lock)  
**Lines:** 163 | **Tests collected:** 17  
**Type:** Unit — quarter parsing, snapshot context manager  
**Skip conditions:** None

---

### tests/test_seed.py
**Phase introduced:** 8a  
**Lines:** 117 | **Tests collected:** 3  
**Type:** Integration — demo DB seeding  
**Skip conditions:** `pytest.skip("No portfolio data")`, `pytest.skip("No price data in DB")`

---

### tests/test_shiller.py
**Phase introduced:** 8k (CAPE data source)  
**Lines:** 119 | **Tests collected:** 14  
**Type:** Unit — Shiller CAPE parser; light live-data checks  
**Skip conditions:** None

*test_current_cape_is_positive and test_current_cape_above_30 read the local CSV cache — they pass if the file exists, which it does after any Macro page load.*

---

### tests/test_macro.py
**Phase introduced:** 8j  
**Lines:** 128 | **Tests collected:** 12  
**Type:** Unit — CAPE implied return formula, ECY formula, FRED retry logic  
**Skip conditions:** None

---

### tests/test_endowment.py
**Phase introduced:** 8k 3C  
**Lines:** 80 | **Tests collected:** 9  
**Type:** Unit — endowment comparison data structure  
**Skip conditions:** None

*test_all_entities_sum_to_100 checks that each endowment's allocations sum to 100% in the static comparison dataset — a data-integrity check on the hardcoded table, not a portfolio identity.*

---

### tests/test_style_box.py
**Phase introduced:** 8e  
**Lines:** 255 | **Tests collected:** 25  
**Type:** Unit — style box coordinate mapping, figure construction  
**Skip conditions:** None

---

### tests/test_prices.py
**Phase introduced:** 8p (duplicate-index fix)  
**Lines:** 129 | **Tests collected:** 2  
**Type:** Unit — price cache deduplication  
**Skip conditions:** None

---

### tests/test_imports.py
**Phase introduced:** hotfix  
**Lines:** 62 | **Tests collected:** ~12 (parametrized over 11 src modules + 1)  
**Type:** Structural — all src modules importable, no dual-import fallbacks  
**Skip conditions:** None

---

### tests/test_phase11_integrity.py
**Phase introduced:** 11 Section 4  
**Lines:** 448 | **Tests collected:** 17  
**Type:** Identity + Prose — Phase 11 regression pins  
**Skip conditions:** None

| Test | Category | Notes |
|---|---|---|
| `test_identity_twr_equals_absolute_return_no_cashflows` | **identity** | TWR ≡ (V_end − V_start)/V_start; renamed Phase 12 S0 |
| `test_identity_twr_absolute_return_multiple_seeds` | **identity** | Same, multiple seeds; renamed Phase 12 S0 |
| `test_identity_ir_times_te_equals_geometric_active` | **identity** | IR × TE = ann_port − ann_bench; renamed Phase 12 S0 |
| `test_identity_ir_times_te_multiple_windows` | **identity** | Same, 1Y and SI windows; renamed Phase 12 S0 |
| `test_identity_n_days_excludes_calendar_zeros` | **identity** | n_days ≈ trading days, not calendar days; renamed Phase 12 S0 |
| `test_identity_calendar_bday_same_n_days` | **identity** | Cal-day and bday series yield same n_days after filter; renamed Phase 12 S0 |
| `test_identity_tracking_error_calendar_bday_consistent` | **identity** | TE consistent across series types; renamed Phase 12 S0 |
| test_em_disclosure_no_duplicate_label_prefix | unit | EM_DISCLOSURE text content check; doesn't fit prose category |
| test_em_disclosure_still_mentions_iemg | unit | Content check |
| `test_prose_cape_template_uses_dynamic_percentile` | **prose** | Template uses {{ macro.cape.percentile }}; renamed Phase 12 S0 |
| test_macro_section_regime_dispatch_elevated | unit | Regime logic unit test |
| test_macro_section_regime_dispatch_moderate | unit | |
| test_macro_section_regime_dispatch_below_average | unit | |
| test_macro_section_regime_pct_int_in_output | unit | |
| `test_prose_duration_in_line_with_benchmark` | **prose** | "in line with benchmark" branch; renamed Phase 12 S0 |
| `test_prose_duration_above_benchmark` | **prose** | "above benchmark by N yrs" branch; renamed Phase 12 S0 |
| `test_prose_duration_below_benchmark` | **prose** | "below benchmark by N yrs" branch; renamed Phase 12 S0 |

---

### tests/render/test_performance_render.py
**Phase introduced:** 8p  
**Lines:** 296 | **Tests collected:** 14  
**Type:** Render — Streamlit AppTest  
**Skip conditions:** 11 tests skip with "No portfolio data — skipped in local/empty-DB mode"

---

### tests/render/test_macro_render.py
**Phase introduced:** 8o  
**Lines:** 92 | **Tests collected:** 9  
**Type:** Render — Streamlit AppTest  
**Skip conditions:** None locally (macro page doesn't require trade data)

---

### tests/render/test_factor_profile_render.py
**Phase introduced:** 8q  
**Lines:** 82 | **Tests collected:** 6  
**Type:** Render — Streamlit AppTest  
**Skip conditions:** 2 tests skip with "No regression data — skipped in local/empty-DB mode"

---

## Summary

| Category | Count (post-Phase 12 S0) | Notes |
|---|---|---|
| `test_identity_*` | 16 (after renames) | Mathematical identities, all should pass without DB |
| `test_prose_*` | 7 (after renames) | Prose-vs-data, all should pass without DB |
| `test_bound_*` | 0 | Added in Phase 12 Section 2 |
| `test_render_*` | 29 (file-level prefix) | 16 skip locally, 13 always run |
| `test_snapshot_*` | 0 | None yet |
| Uncategorized unit/integration | ~238 | Retain original names |

**Total collected:** 290  
**Pass / skip:** 274 / 16  
**Skip root cause:** All 16 are `local/empty-DB mode` — tracker.db has no trades. All pass under `TRACKER_MODE=demo`.

## Tests not renamed (with rationale)

| File | Test | Why not renamed |
|---|---|---|
| test_returns.py | test_no_cashflows_daily_linked | Tests function behavior, not a math identity |
| test_performance.py | test_compute_risk_metrics_short_windows | Window-filtering behavior; predates convention |
| test_performance.py | test_risk_metrics_distinguish_across_windows | Same |
| test_attribution.py | test_two_sleeve_hand_calculation | Hand-calc unit test, not a system-level identity |
| test_attribution.py | test_bf_per_sleeve_returns_are_total_return | DB-backed integration; naming unclear |
| test_prose_consistency.py | test_significance_label_* (5 tests) | Unit tests of helper function, not prose-vs-data |
| test_prose_consistency.py | test_percentile_label_* (6 tests) | Same |
| test_endowment.py | test_all_entities_sum_to_100 | Static dataset check, not portfolio identity |
| test_phase11_integrity.py | test_em_disclosure_* (2 tests) | Code-quality checks, not identity or prose-vs-data |
| test_phase11_integrity.py | test_macro_section_regime_* (4 tests) | Dispatch-table unit tests, not prose-vs-data |
| All of test_factors.py | (all 42+ tests) | Parser, OLS, regression structure — no clean fit |
| All of test_positioning.py | (all 19 tests) | Positioning helper unit tests |
| All of test_reports.py | (all 19 tests) | drift_status helper + chart structure tests |
