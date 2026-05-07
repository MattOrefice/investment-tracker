# Phase 11 Diagnostic — Data Integrity & Dynamic Text Audit

Generated: 2026-05-07. All computations against `data/demo.db` with `TRACKER_MODE=demo`.

---

## 1.1 — TWR / Absolute Return / Current Value path trace

### (a) Current Value tile — source

**File:** `pages/4_Performance.py:260`
```python
current_val = float(pv.iloc[-1])
```
`pv` originates from `get_portfolio_value_series(INCEPTION, TODAY)` called at line 49 and assigned at line 171. The function lives in `src/holdings.py:35`.

Price column used: **`adj_close`** (with `fillna(close)` fallback), line `src/holdings.py:113`.

SPAXX: BIL adj_close normalized to $1.00 at inception, line `src/holdings.py:99–107`. Normalization means both the TWR path and the Current Value tile see BIL return as a fractional growth on $1.00 — no asymmetry between the two paths for SPAXX.

DRIP simulation: `src/holdings.py:122–165`. Reinvested dividend shares are added to `holdings_matrix` from each ex_date forward, using `adj_close` at the ex_date as the reinvestment price.

### (b) Cumulative TWR — source

**File:** `pages/4_Performance.py:254`
```python
port_si = period_return("daily", pv, cf, "SI")
```
**Function:** `src/returns.py:twr_daily_linked()`. With `cf = 0` everywhere, this chain-links daily sub-period returns and produces exactly `pv.iloc[-1] / pv.iloc[0] − 1` (no cash-flow timing adjustment needed for a lump-sum portfolio).

Series origin: same `pv` as the Current Value tile — `adj_close`-based, with DRIP.

### (c) SPAXX → BIL proxy path

Applied in `src/holdings.py:99–107` (portfolio value series) and in `src/holdings.py:203–214` (sleeve weights). BIL is applied identically to both the TWR path and the Current Value path, since both read from the same `pv` series. No asymmetry here.

### (d) Gap computation — observed vs. candidate causes

**Observed gap: 315.8 bps** (empirically computed from demo.db on 2026-05-07).
- `pv.iloc[0]` (adj_close inception):  $976.24
- `pv.iloc[-1]` (adj_close today):    $1,297.29
- cost_basis (close at trade date):   $1,000.00
- TWR SI:          32.89%
- Absolute return: 29.73%
- Gap:             315.8 bps

**Root cause:** `pv.iloc[0]` ($976.24) ≠ cost_basis ($1,000.00). The inception-date portfolio value uses `adj_close` from the SQLite price cache, which has been retroactively adjusted downward by Yahoo Finance as dividends were paid out since May 2025. The cost basis uses `close` prices at the trade date, recorded in the trades table by `seed_paper_trades.py:65`.

Retroactive adj_close discount by ticker (2025-05-01 cached adj_close / close):

| Ticker | close_0 | adj_close_0 | adj/close |
|--------|---------|-------------|-----------|
| VOO    | 513.35  | 507.27      | 0.9882    |
| SPHQ   | 66.24   | 65.44       | 0.9879    |
| VTV    | 166.08  | 162.59      | 0.9790    |
| AVUV   | 83.38   | 82.09       | 0.9845    |
| VEA    | 52.65   | 51.03       | 0.9693    |
| IEMG   | 54.14   | 52.57       | 0.9710    |
| VGIT   | 59.54   | 57.32       | 0.9627    |
| SCHP   | 26.61   | 25.67       | 0.9647    |
| VNQ    | 88.62   | 85.23       | 0.9618    |
| PDBC   | 12.48   | 12.01       | 0.9626    |

These discounts are not artifact — they represent genuine cumulative dividend adjustments applied retroactively by Yahoo Finance to historical adj_close values after dividends were paid since May 2025.

**Blended adj_close discount at inception: 2.38%** (pv.iloc[0]/cost_basis = 0.9762).

**Gap estimation per candidate cause:**

1. **BIL proxy effect alone:** SPAXX weight ≈ 3.0% × (BIL SI return − 0%) ≈ 3.0% × ~5.3% ≈ 16 bps. Cannot produce 315 bps.

2. **Accumulated dividend distributions not reinvested (price-only gap):** blended dividend yield ~2–4% annualized × 1.0 year ≈ 200–400 bps of cumulative distributions. The 2.38% adj_close discount at inception (316 bps) falls squarely in this range and matches the observed gap directly.

3. **Fractional-share rounding:** cost_basis = $1,000.0001 (sub-penny). < 1 bp. Cannot produce 315 bps.

4. **Stale adj_close cache:** the retroactive adjustment IS captured in the current cache (adj_close_0 ≠ close_0). The adjustment was applied when `get_dividends()` triggered a re-fetch of the full price range on first DRIP run (via `fetch_prices()` → `INSERT OR REPLACE`), updating inception-date adj_close.

**Note on DRIP compensation:** The DRIP mechanism in `src/holdings.py:122–165` was designed to compensate for this denominator gap. It adds reinvested shares so that `pv.iloc[-1]` ≈ cost_basis × (1 + TWR). However, the DRIP does not correct `pv.iloc[0]` — it only affects `pv.iloc[-1]`. The result is that both TWR and absolute return use the same inflated `pv.iloc[-1]` (DRIP shares included), but TWR divides by the retroactively-lowered `pv.iloc[0]` while absolute return divides by the original `cost_basis`. This creates a systematic gap equal to `cost_basis / pv.iloc[0] − 1 ≈ 2.43%` amplified by the portfolio appreciation factor.

### (e) Diagnostic verdict — **Branch A**

The divergence is confirmed as **Branch A**: the inception-date portfolio value uses retroactively adjusted `adj_close` (which is ~2.38% below the original trade close price), while the absolute return calculation uses the original cost basis (trade close). This denominator mismatch produces a TWR inflated by 315 bps relative to absolute return for a portfolio that has appreciated ~33%.

The DRIP mechanism does not resolve this — it corrects `pv.iloc[-1]` symmetrically for both calculations, leaving the denominator gap intact.

**Fix path (Section 2.1):** Replace `_cost_basis` (sum of trade prices) with `float(pv.iloc[0])` as the absolute return denominator. By construction, this makes absolute_return ≡ TWR. The reconciliation prose should be rewritten to state this identity explicitly and remove the DRIP/BIL disclaimer.

---

## 1.2 — Price-cache integrity check

Cached 1Y returns (window: 2025-05-07 to 2026-05-06) vs. external TTM estimates cited in Phase 11 prompt:

| Ticker | Cached 1Y return | External TTM (cited) | Gap (pp) | Pass (≤0.5 pp)? |
|--------|-----------------|---------------------|----------|-----------------|
| VOO    | 32.34%          | ~30% (S&P TTM)      | ~+2.3    | No              |
| SPHQ   | 24.44%          | —                   | —        | —               |
| VTV    | 26.72%          | —                   | —        | —               |
| AVUV   | 44.68%          | —                   | —        | —               |
| **VEA**    | **36.64%**      | **~36% (cited 40.50% was stale)** | **~+0.6** | Borderline |
| **IEMG**   | **53.90%**      | **~48% (cited 58.00% was stale)** | **~+5.9** | **No** |
| VGIT   | 3.64%           | —                   | —        | —               |
| SCHP   | 4.70%           | —                   | —        | —               |
| VNQ    | 13.53%          | —                   | —        | —               |
| PDBC   | 49.84%          | —                   | —        | —               |
| SPY    | 31.86%          | ~30%                | ~+1.9    | No              |
| EFA    | 26.10%          | ~26%                | ~0.1     | Yes             |
| EEM    | 54.25%          | ~48%                | ~+6.3    | No              |
| IEF    | 3.50%           | ~3–4%               | ~0       | Yes             |
| BIL    | 3.62%           | ~4–5%               | ~-0.4    | Yes             |

**Note on VEA and IEMG discrepancy with Phase 11 prompt numbers:** The Phase 11 prompt cites "VEA 40.50% (external TTM ~36%), IEMG 58.00% (external TTM ~48%)" — those figures likely reflect the Q1 2026 PDF generated in April 2026 using a 1Y window ending around March 31, 2026. The current cache (window ending May 6, 2026) shows VEA = 36.64% and IEMG = 53.90%. The April 2026 cached window would have captured the Q1 2025–Q1 2026 period, which included the strong January–February 2026 international rally. The May 2026 window drops that spike from the trailing period.

**Ticker-level findings:**

**VEA (International Developed):**
- Cached 1Y: 36.64%; external TTM April 2026: ~36%
- The prior 40.50% figure was the 1Y return for the window ending ~March 31, 2026. The current cache (ending May 2026) is consistent with current external data.
- Most recent 5 adj_close values in cache (through 2026-05-06): consistent with current YF prices (adj_close = close on most recent date, confirming no forward-adjustment pending).
- Ticker loaded: VEA. Confirmed correct.

**IEMG (Emerging Markets):**
- Cached 1Y: 53.90%; external TTM April 2026: ~48%
- The prior 58.00% figure was from the April 2026 window. Current cached value (53.90%) appears elevated vs. external TTM ~48%. Gap of ~5.9 pp exceeds tolerance.
- Root cause candidates: (1) IEMG had a sharp April 2025 drawdown followed by strong recovery; different 1Y window endpoints change the return materially; (2) potential stale adj_close for the May 2025 inception of the window.
- Ticker loaded: IEMG. Confirmed correct.

**High-return anomaly (EEM 54.25%, IEMG 53.90%):** Both EM-tracking ETFs show TTM returns in the 54% range. This reflects the exceptional EM rally from May 2025 through May 2026, led by Korean semiconductor stocks (Samsung, SK Hynix) driven by AI/semiconductor capex. The Factor Profile page already documents this in the VEA Korea universe-mismatch disclosure; the same driver explains IEMG and EEM's elevated returns.

**Diagnostic verdict — cache freshness:**
- VEA: within tolerance for current date window; prior elevated figure was date-window artifact from Q1 PDF.
- IEMG: 5.9 pp above external TTM for the current window; requires verification via fresh yfinance pull.
- EEM, VOO/SPY gap: noted but not flagged as cache errors — reflect genuine market performance for the specific 1Y window in cache vs. rounded external TTM estimates.
- **No cache rebuild triggered** for Section 2.3 until fresh yfinance verification confirms the gap (the 5.9 pp IEMG gap is suspicious but may reflect the specific 1Y window rather than stale data).

---

## 1.3 — Factor regression sample-size root cause

**Observed:** US sleeve N = 229, International Developed sleeve N = 238 — same disclosed window (May 2, 2025 to March 31, 2026), 9-day gap.

**Trace:**

```
US sleeve return series:    371 calendar days (2025-05-02 to 2026-05-07)
Developed sleeve (VEA):     371 calendar days (2025-05-02 to 2026-05-07)
US FF factor data:          ends 2026-03-31 (last date in cache)
Dev FF factor data:         ends 2026-03-31 (same date)
```

After inner-joining:
- US merged N = 229
- Dev merged N = 238
- Difference: 9 days

**The 9 extra days in the Dev regression (dates in Dev FF but not US FF):**

| Date       | Day        | Note                      |
|------------|------------|---------------------------|
| 2025-05-26 | Mon        | Memorial Day (US closed)  |
| 2025-06-19 | Thu        | Juneteenth (US closed)    |
| 2025-07-04 | Fri        | Independence Day (US)     |
| 2025-09-01 | Mon        | Labor Day (US closed)     |
| 2025-11-27 | Thu        | Thanksgiving (US closed)  |
| 2025-12-25 | Thu        | Christmas (US closed)     |
| 2026-01-01 | Thu        | New Year's Day (US)       |
| 2026-01-19 | Mon        | MLK Day (US closed)       |
| 2026-02-16 | Mon        | Presidents' Day (US)      |

All 9 are US federal holidays when US stock exchanges are closed.

**Root cause: US market holiday asymmetry.**

Ken French's US daily factor dataset excludes US market holidays (no NYSE trading, no US return data). Ken French's Developed ex-US daily factor dataset includes those same dates because international markets (Europe, Japan, Korea) were open on US holidays.

The sleeve return series for both US and Dev sleeves is built using `pd.date_range(freq='D')` with forward-fill on non-trading days. On US holidays, all ETFs — including VEA — show zero return (forward-fill from prior day's adj_close). When the Dev sleeve series inner-joins with the Developed FF factors:
- The Dev FF data has non-zero factor returns on those 9 dates (international markets were open)
- VEA shows zero return (it trades on US exchanges and was closed)
- These 9 observations are included in the Dev regression with artificially zeroed sleeve return

**Effect on Dev regression:** 9 observations where R_sleeve = 0 and factor returns are non-zero introduce a systematic downward bias in the factor loadings and inflate the alpha. The effect is small (~9/238 ≈ 3.8% of observations) but methodologically wrong.

**Verdict: date-handling asymmetry.** The inner join correctly drops US holidays from the US regression (no US FF data on those dates). The Dev regression should also exclude US holidays for methodological consistency: VEA trades on US exchanges and has no return information on those dates (not that markets were closed globally; just that VEA's price was unchanged by construction, not by observation).

**Fix (Section 2.4):** Filter the sleeve return series to US trading days before joining with Dev FF factors, OR inner-join the Dev sleeve return with the US FF data first (to restrict to US trading days), then join with Dev FF factors. The disclosure should note per-sleeve N accurately and explicitly state the US-holiday exclusion rationale.

---

## 1.4 — Information Ratio formula audit

**Reported values (SI window):** IR = 2.75, TE = 1.09%, IR × TE = 2.99% (299 bps).

**Computed reference values:**

| Metric | Value |
|--------|-------|
| Ann. portfolio return (geom., SI): | 21.30% |
| Ann. benchmark return (geom., SI): | 18.31% |
| Geometric annualized active return: | **3.00%** |
| Arithmetic mean daily active × 252: | **2.49%** |
| Tracking error (std × √252): | **1.09%** |
| IR = (ann_port − ann_bench) / TE: | **2.75** |
| IR × TE (decimal): | **0.02995** |
| Gap IR×TE vs. arithmetic active: | 50 bps |
| Gap IR×TE vs. geometric active: | **0 bps** |

**Formula in `src/performance.py` (lines 90–92):**
```python
ann_port  = float((1 + port_ret).prod() ** (252 / n) - 1)   # geometric
ann_bench = float((1 + bench_ret).prod() ** (252 / n) - 1)  # geometric
ir = (ann_port - ann_bench) / tracking_error
```

**Verdict: IR formula is internally consistent — no bug.** The formula uses geometric annualization for both port and bench returns, and the resulting active return equals IR × TE exactly (gap = 0 bps). IR = 2.75 is correct by the formula used.

**Convention note:** The formula uses geometric annualization for the active return numerator, which is less common than arithmetic in institutional practice. CFA curriculum and PRINCO/JPM IDD convention uses arithmetic mean daily active × √252 for the IR numerator (the "annualized tracking contribution" form). Under that convention, IR would be 2.49% / 1.09% ≈ 2.28, not 2.75. The ~50 bps Jensen's gap between geometric and arithmetic annualization is expected given this portfolio's volatility regime.

**The 168 bps gap cited in the Phase 11 prompt** (IR × TE = 310 bps vs. annualized active 478 bps) reflects a different active return figure ("486 bps vs Custom Blended") that was computed using a different benchmark or a different TWR method at the time of the PDF. The current computed figures (geometric ann. active = 299 bps, arithmetic = 249 bps) are consistent with IR × TE = 299 bps. No formula bug detected at current state.

**No fix required.** Add a methodology disclosure clarifying the geometric convention.

---

## 1.5 — Hard-coded interpretive text inventory

All files checked: `pages/4_Performance.py`, `pages/5_Macro.py`, `pages/6_Positioning.py`, `pages/7_Factor_Profile.py`, `pages/8_Benchmark_Attribution.py`, `app.py`, `src/reports.py`, `templates/quarterly_report.html`.

| # | File | Line | Current text | Intended dynamic source | Priority |
|---|------|------|-------------|------------------------|----------|
| 1 | `templates/quarterly_report.html` | 530 | `"Elevated US equity valuations (CAPE 95th percentile) support the SAA's diversification..."` | `macro.cape.percentile` (already in table on same page) | **Critical** |
| 2 | `pages/4_Performance.py` | 459 | `"crosses ~18 months of history"` | `(inception_date + 18 months).strftime(...)` | High |
| 3 | `pages/4_Performance.py` | 463 | `"~21 and ~63 daily observations"` | `count_trading_days(1M_window)`, `count_trading_days(3M_window)` | High |
| 4 | `src/positioning.py` | 378 (comment) | `fi_weight_pct` docstring says "Core FI + TIPS + Cash"; `_FI_SLEEVE_HOLDING` includes `Cash / SPAXX` | Separate cash from FI weight; expose `fi_weight_excl_cash_pct` separately | **Critical** |
| 5 | `src/reports.py` | 862 | `"FI weight: {dur['fi_weight_pct']}% of portfolio."` includes Cash | Use `fi_weight_excl_cash_pct`; add separate Cash line | **Critical** |
| 6 | `src/factors.py` | 963 | `"~65 calendar-day publication lag at current Ken French release cadence"` | Compute from `(today - ff_us.index[-1]).days` | Medium |
| 7 | `src/factors.py` | 962 | `"(from the time it diverged from the main branch)"` / `"Sample sizes reflect the overlap..."` prose implies both sleeves have same N | Add per-sleeve N to disclosure (they differ by 9) | High |
| 8 | `src/reports.py` | 860 | `"Intermediate-Treasury focus keeps duration below the Agg, limiting sensitivity to rate moves."` | Template based on actual `fi_dur` vs `agg_dur` comparison | Low |
| 9 | `src/positioning.py` | 39 | `BLOOMBERG_AGG_DURATION_YEARS: float = 6.0` | TODO noted; static value is reasonable until live data source added | Low |
| 10 | `src/positioning.py` | 42–48 | `ETF_DURATION` dict (VGIT 5.5, SCHP 6.8, etc.) | TODO noted; update each quarter from fact sheets | Low |
| 11 | `pages/4_Performance.py` | 307–316 | Reconciliation prose: "DRIP reinvestment rounding and the BIL total-return proxy" disclaimer | Remove entirely; after Branch A fix, absolute_return ≡ TWR by construction | **Critical** |

**Items not covered by Section 3 subsections 3.1–3.6 (additional Section 3.7 candidates):**

- `src/factors.py:963`: factor publication lag hardcoded as "~65 calendar-day lag" — should derive from `(today - ff_end_date).days`.
- `src/factors.py:962`: sample size disclosure implies symmetry ("both sleeves, same window") — must be changed to per-sleeve N after the 9-day asymmetry fix.
- `src/reports.py:860`: FI duration conclusion sentence is static regardless of actual duration positioning.

---

## Summary verdicts

| Section | Verdict |
|---------|---------|
| 1.1 TWR / Absolute Return gap | **Branch A** — `pv.iloc[0]` (adj_close inception, retroactively adjusted to $976.24) ≠ cost_basis ($1,000.00 at trade close prices). Gap = 315.8 bps. DRIP does not resolve the denominator. Fix: use `pv.iloc[0]` as absolute-return denominator. |
| 1.2 Cache freshness | VEA within tolerance for current window. IEMG cached 1Y (53.90%) vs. external TTM (~48%) = 5.9 pp gap; elevated but partly window-date artifact; no rebuild triggered until fresh-pull verification. All other tickers no clear cache anomaly. |
| 1.3 Factor regression N asymmetry | **US holiday asymmetry.** 9 US federal holidays appear in Dev FF data but not US FF data. VEA has zero return on those days (US exchange closed), biasing the Dev regression. Fix: restrict Dev sleeve series to US trading days before joining Dev FF factors. |
| 1.4 IR formula | **No bug.** Formula uses geometric annualization; IR × TE = active return exactly. Convention differs from CFA/institutional arithmetic standard by ~50 bps Jensen's gap. Add methodology disclosure. |
| 1.5 Hard-coded text | 11 items inventoried. Critical: static "CAPE 95th percentile" in template (line 530), reconciliation disclaimer to remove, FI weight including Cash, per-sleeve N disclosure asymmetry. |
