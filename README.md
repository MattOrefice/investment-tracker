# Personal Investment Analytics System

**Multi-asset portfolio analytics with institutional-grade attribution and risk decomposition.**

Matt Orefice, CFA · [Live Demo](https://mattorefice-investment.streamlit.app/) · [LinkedIn](https://www.linkedin.com/in/matthew-orefice-cfa-83536b190/) · [GitHub](https://github.com/MattOrefice/investment-tracker)

![CI](https://github.com/MattOrefice/investment-tracker/actions/workflows/ci.yml/badge.svg)

A Python-based portfolio analytics platform tracking a thesis-driven multi-asset strategic asset allocation across ten sleeves. Implements daily-linked TWR (GIPS-compliant), two-stage Brinson-Fachler attribution, per-sleeve Fama-French 5-factor regressions with Newey-West HAC standard errors, and a three-layer integrity test suite enforced via GitHub Actions CI. Deployed as a live Streamlit demo and locked-quarter PDF report.

---

## Current snapshot

*Prices update daily. Snapshot as of May 7, 2026 (371 calendar days since inception May 1, 2025).*

| Metric | Value | Window |
|--------|-------|--------|
| Cumulative TWR | +32.9% | Since Inception |
| vs. Custom Blended SAA Benchmark | +482 bps | Since Inception |
| vs. S&P 500 | −18 bps | Since Inception |
| YTD 2026 Return | +11.9% (S&P 500: +8.1%) | Jan 1 – May 7, 2026 |
| Sharpe Ratio | 2.87 | Since Inception, annualized (RF: 4.32%) |
| Information Ratio | 3.63 | Since Inception, vs. Custom Blended |
| Q1 2026 Active Return | +189 bps vs. blended; +735 bps vs. S&P 500 | Q1 2026 (locked) |

The primary performance benchmark is the custom SAA-blended basket, not the S&P 500. SI active return vs. S&P 500 is intentionally disclosed alongside: a near-zero SI spread against the S&P 500 is an expected outcome for a portfolio whose 72% equity sleeve closely mirrors broad market beta with modest factor tilts.

See [methodology](#methodology) for return computation and benchmarking approach.

---

## What the system does

Tracks a paper-trade portfolio deployed May 1, 2025 against a target SAA across ten sleeves: US Large Core (16%), US Large Quality (14%), US Large Value (8%), US Small Cap (7%), International Developed (19%), Emerging Markets (8%), Real Assets (10%), Core Fixed Income (9%), TIPS (6%), and Cash (3%). Computes daily-linked TWR, Brinson-Fachler attribution, per-sleeve factor regression decomposition, macro context indicators, and risk-adjusted metrics. Generates a locked-quarter PDF report and serves a live Streamlit demo.

The demo database uses paper trades from May 2025 against the same SAA and methodology as the personal portfolio. The purpose is to make the analytical framework inspectable without exposing real position data.

---

## Methodology

### Returns

Daily-linked TWR chains sub-period returns: `TWR = ∏(1 + r_t) − 1`, where `r_t = (V_t − V_{t−1} − CF_t) / V_{t−1}`. Cash flows treated at beginning of period. For this portfolio (single initial deposit, no subsequent external cash flows), daily-linked TWR and Modified Dietz converge within 1 bp — verified by the identity test suite.

### Three-layer attribution stack

**Stage 1 — SAA design effect.** The custom SAA-blended benchmark return is computed as the target-weight average of per-sleeve benchmark proxies (SPY, QUAL, IWD, IWM, EFA, EEM, IEF, TIP, 50% VNQ + 50% DBC, BIL), normalized to $1 at inception. This stage isolates how much of total active return derives from the SAA design itself versus the S&P 500 or 60/40 baselines.

**Stage 2 — Brinson-Fachler implementation effect.** Active return relative to the SAA-blended benchmark is decomposed per sleeve into allocation effect and selection effect using the BF (1985) framework. Allocation effect = `(w_p − w_b)(r_b − r_b,total)`; selection effect = `w_p(r_p − r_b)`. The two effects sum to active return within 1 bp — enforced by an algebraic identity test. Stage 1 + Stage 2 reconcile to total active return within 1 bp.

**Per-sleeve Fama-French 5-factor regressions.** Each equity sleeve is regressed against its region-appropriate FF5 factor set — US factors for the US equity sleeves (VOO, SPHQ, VTV, AVUV), Developed ex-US factors for the international sleeve (VEA). Running per-sleeve regressions avoids the model-misspecification that occurs when non-US and real-asset returns flow unspanned into a single full-portfolio alpha estimate. Newey-West HAC standard errors (lag = `floor(4 × (T/100)^(2/9))`) correct for heteroskedasticity and serial correlation.

**Benchmark-relative regression (institutional alpha).** Portfolio excess returns are regressed on the custom SAA-blended benchmark return plus HML, SMB, and RMW style factors. The regression intercept is the active return component not explained by benchmark beta or style tilts — the definition used in institutional attribution frameworks (cf. PRINCO, JPM IDD methodology).

### Integrity testing

Three layers:

**Layer 1 — Identities.** Mathematical relationships that must hold by construction: BF effects sum to active return, Stage 1 + Stage 2 = Total, sleeve weights sum to 100%, cumulative TWR equals the absolute return for the lump-sum single-cashflow case. These are caught at construction time, not during QA.

**Layer 2 — Bounds.** Reasonability checks with tolerance: Sortino ≥ Sharpe (must hold when annualized return > RF), VaR/CVaR within expected regime ranges, CAPE readings within a plausible historical range, IR × TE within Jensen's gap of geometric annualized active return. Bound failures indicate data pipeline issues or computational errors.

**Layer 3 — Prose-vs-data.** Every numerical citation in interpretive prose is templated from its source data, with tests asserting prose equals computed value. CAPE percentile in commentary equals CAPE percentile in the table. PDF methodology drift threshold prose derives from the SAA rule constant, not from data inference (Real Assets at 10% target weight is a boundary exception assigned to the 200 bps tier — naive `MIN()` on the DB returns 14%, not the correct 10% rule threshold). 322 tests across the three layers run on every push to main via GitHub Actions.

### Data lineage

| Source | Data | Caching |
|--------|------|---------|
| Yahoo Finance | Daily adjusted-close prices (all holdings and benchmarks) | SQLite cache; locked on quarterly report date to prevent retroactive adj_close revisions from shifting historical numbers |
| FRED | T10Y2Y, DFF, BAMLH0A0HYM2 (HY OAS), USREC | SQLite cache with 24h TTL |
| Robert Shiller / Yale | CAPE (monthly) | Local CSV with monthly refresh |
| Ken French Data Library (Dartmouth) | FF5 factors (US, Developed ex-US) | Downloaded and cached per regression run |

**Real Assets benchmark disclosure.** The portfolio holds PDBC (Invesco Optimum Yield Diversified Commodity Strategy; C-corp structure, no K-1 issued). The benchmark uses DBC (Invesco DB Commodity Index Tracking Fund; K-1-issuing). Selection effect in the Brinson-Fachler attribution captures the DBC–PDBC return spread. This asymmetry is documented rather than hidden.

### Methodology decisions worth flagging

**EM factor regression deferred.** Ken French does not publish daily EM factor data. Monthly EM factors at the current sample size (≈12 months) yield insufficient observations for stable inference. EM factor coverage is scoped to `docs/post_launch_backlog.md` pending 3+ years of history.

**CMA excluded from benchmark-relative regression.** For a passive multi-ETF implementation, CMA primarily captures ETF-level capital expenditure differences rather than a deliberate active investment decision. Including it adds collinearity without informational gain.

**Zero-return rows excluded from vol and Sharpe computation.** The original implementation forward-filled weekend prices, producing zero-return rows that diluted standard deviation and inflated risk-adjusted metrics by approximately 15–25%. The bug was identified during Phase 11 integrity testing and corrected. See `docs/phase_11_diagnostic.md` for the full write-up.

**Benchmark vs. holding distinction is intentional.** Benchmark tickers (SPY, QUAL, IWD, IWM, EFA, EEM, IEF, TIP, DJP/DBC, BIL) are institutional convention; holding tickers (VOO, SPHQ, VTV, AVUV, VEA, IEMG, VGIT, SCHP, VNQ, PDBC, SPAXX) are selected for after-tax efficiency in a taxable account. Blended weighted-average ER of holdings is ~10 bps. The rationale for each divergence is documented in the Research page.

---

## Tech stack and repo structure

### Stack

Python 3.11 · Streamlit · SQLite · pandas · NumPy · statsmodels (OLS, NW-HAC) · Plotly + kaleido (static chart export for PDF) · WeasyPrint (PDF rendering on Linux/Cloud) · xhtml2pdf (Windows local fallback) · yfinance · fredapi · Jinja2 · pytest · GitHub Actions

### Repository layout

```
src/                  Core logic (no Streamlit imports; fully unit-testable)
  attribution.py      Brinson-Fachler decomposition, two-stage reconciliation
  benchmarks.py       SAA-blended benchmark, per-sleeve benchmark series
  factors.py          FF5 regressions, benchmark-relative regression, style box
  holdings.py         Net shares, portfolio value series, sleeve weights
  macro.py            FRED integration (yield curve, Fed Funds, HY OAS, USREC)
  prices.py           Yahoo Finance fetcher with SQLite cache
  reports.py          PDF generator: data assembly, Jinja2 render, WeasyPrint/xhtml2pdf
  returns.py          Daily-linked TWR, Modified Dietz, annualization, period slicing
  shiller.py          CAPE from Yale dataset with local CSV fallback

pages/                Streamlit pages (auto-discovered by app.py)
  1_SAA.py            SAA allocation chart, per-sleeve rationale, drift placeholder
  2_Research.py       ETF selection: benchmark vs. holding comparison, ER breakdown
  3_Trade_Log.py      Trade entry form, investment/position thesis browser, themes
  4_Performance.py    TWR, BF attribution, cumulative chart, drift table, PDF export
  5_Macro.py          CAPE, yield curve, Fed Funds, HY OAS with NBER recession shading
  6_Reports.py        Quarterly report archive and download
  7_Factor_Profile.py Per-sleeve FF5 regressions, benchmark-relative alpha, style box

tests/                322 tests across three integrity layers
  test_identity_layer1.py    Layer 1: math identities
  test_bound_layer2.py       Layer 2: reasonability bounds
  test_prose_consistency.py  Layer 3: prose-vs-data guards
  test_returns.py            Unit tests for TWR and period slicing
  test_attribution.py        Unit tests for BF decomposition
  (additional per-module tests)

templates/            PDF report (Jinja2 HTML + CSS)
docs/                 Methodology diagnostics, test inventory, prose inventory,
                      CI setup, operational runbooks, phase diagnostics
tools/                push-and-verify.sh (pre-push test gate)
scripts/              rebuild_prices_cache.py (maintenance utility)
```

### Running locally

```bash
git clone https://github.com/MattOrefice/investment-tracker.git
cd investment-tracker
python -m venv .venv

# macOS / Linux
source .venv/bin/activate

# Windows (PowerShell)
.\.venv\Scripts\Activate.ps1

pip install -r requirements.txt

# Run the demo (paper-trade portfolio, no real holdings needed)
TRACKER_MODE=demo streamlit run app.py          # macOS / Linux
$env:TRACKER_MODE="demo"; streamlit run app.py  # Windows PowerShell
```

To generate a quarterly PDF: open the demo, navigate to **Performance**, and click **Generate Quarterly Report**. WeasyPrint is used on Linux/Cloud; xhtml2pdf is the fallback on Windows.

To run the test suite:

```bash
TRACKER_MODE=demo python -m pytest -m "not slow"    # macOS / Linux
$env:TRACKER_MODE="demo"; python -m pytest -m "not slow"  # Windows PowerShell
```

The personal-mode portfolio (`TRACKER_MODE=personal`, `data/tracker.db`) is gitignored and only exists locally. The demo portfolio (`data/demo.db`) is committed and is what the Streamlit Cloud deployment uses.

### CI/CD

GitHub Actions runs `pytest -m "not slow"` (322 tests, ~90 seconds) under `TRACKER_MODE=demo` on every push and pull request to `main`. The `tools/push-and-verify.sh` wrapper runs the same suite locally before every push, so failures are caught before they reach the CI queue. See `docs/ci_setup.md` for branch protection and secrets configuration.

### Planned enhancements

- **Sleeve correlation matrix** with rolling-window view — tests whether the diversification thesis (Real Assets, Intl Developed, EM providing low correlation to US equity) holds across regime changes, particularly the 2022 inflation shock.
- **EM factor regression** once portfolio history reaches 3+ years and monthly FF factor observations become sufficient for stable inference.
- **Tax-aware reporting layer** — qualified vs. unqualified dividend tracking, wash-sale awareness, estimated after-tax return. Particularly relevant for the Real Assets sleeve (REIT distributions are predominantly non-qualified income).

See `docs/post_launch_backlog.md` for the full backlog.

### License

MIT. See `LICENSE`.

---

*Open to allocator-side and investment due diligence roles. Reach out via [LinkedIn](https://www.linkedin.com/in/matthew-orefice-cfa-83536b190/).*
