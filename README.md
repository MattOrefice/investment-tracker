# Investment Analytics Tracker

A personal investment analytics system built on an institutional allocator framework: strategic asset allocation across ten sleeves, thesis-driven execution, GIPS-compliant time-weighted returns, Brinson-Fachler attribution, and automated quarterly PDF reporting.

**Live demo:** https://mattorefice-investment.streamlit.app/

To generate a sample quarterly report: open the demo, navigate to **Performance**, and click "Generate Quarterly Report." The report is produced by WeasyPrint on Streamlit Cloud and includes static Plotly charts rendered via kaleido.

---

## What it does

- **Time-weighted returns** — daily-linked chain-linked TWR (GIPS-compliant); Modified Dietz available for comparison. Both methods converge to within 1 bp for this portfolio (single initial cash flow, no subsequent external flows).
- **Brinson-Fachler attribution** — allocation and selection effects per sleeve, with a reconciliation check ensuring the sum of effects equals active return within 1 basis point.
- **Custom-blended benchmark** — SAA target-weight basket of per-sleeve benchmark proxies (SPY, QUAL, IWD, IWM, EFA, EEM, IEF, TIP, 50% VNQ / 50% DBC, BIL), normalized to $1 at inception. Separates diversification decisions from security selection.
- **Drift monitoring** — two-threshold tolerance: a sleeve is flagged when absolute drift exceeds 200 bps *or* relative drift exceeds 20% of target weight.
- **Macro context** — CAPE (Robert Shiller / Yale), 2/10 yield curve spread, Fed Funds rate, ICE BofA HY OAS, sourced from FRED and Yale. Historical percentiles provided for each indicator.
- **Quarterly PDF report** — six-page WeasyPrint-rendered report with static chart export (kaleido). Covers executive summary, holdings, period returns, BF attribution with auto-generated selection effect commentary, macro context, and methodology.
- **Dual mode** — personal mode (local SQLite with real holdings, gitignored) and demo mode (paper-trade database committed to the repo, deployed to Streamlit Cloud).

## Methodology

**Returns.** Daily-linked TWR chains sub-period returns as the product of (1 + r_t) minus 1, where r_t = (V_t - V_{t-1} - CF_t) / V_{t-1}. Cash flows assumed at beginning of each day. This is the GIPS-recommended approach for portfolios with frequent external cash flows; for this portfolio with a single initial deposit, results match Modified Dietz to within 1 bp.

**Attribution.** Brinson-Fachler (1985) decomposes active return into two components. Allocation effect = (w_p - w_b)(r_b - r_b,total): the contribution from over- or underweighting a sleeve relative to the benchmark, scaled by that sleeve's benchmark return versus the total benchmark return. Selection effect = w_p(r_p - r_b): the contribution from holding a security that returned differently from the sleeve benchmark. Portfolio weights are beginning-of-period market values; benchmark weights are SAA targets. The sum reconciles to active return within 1 basis point.

**Macro.** CAPE implied 10-year real return uses a regression calibrated to Shiller's long-run data: r ≈ -0.070 × ln(CAPE/16) + 0.066. Historical percentiles contextualize current readings relative to the full data window.

## Stack

Python 3.11, Streamlit, SQLite, pandas, NumPy, Plotly + kaleido (static chart export), WeasyPrint (PDF rendering on Linux/Cloud), xhtml2pdf (Windows fallback), yfinance, FRED API (fredapi), Jinja2, pytest.

## About

Matt Orefice — CFA charterholder (April 2026). Previously in portfolio analytics at MissionSquare Retirement. Built this system during a transition to buy-side allocator and investment due diligence roles, applying CFA-level investment analysis to a personal portfolio tracking system built from scratch.

GitHub: https://github.com/MattOrefice

## Local setup

```bash
git clone https://github.com/MattOrefice/investment-tracker.git
cd investment-tracker
python -m venv .venv

# macOS / Linux
source .venv/bin/activate

# Windows (PowerShell)
.\.venv\Scripts\Activate.ps1

pip install -r requirements.txt

cp .env.example .env
# Edit .env and add your FRED API key
# Free key at: https://fredaccount.stlouisfed.org/apikeys

streamlit run app.py
```

The app defaults to `TRACKER_MODE=personal` and expects `data/tracker.db`. To run the demo portfolio (paper trades, no real holdings):

```bash
# macOS / Linux
TRACKER_MODE=demo streamlit run app.py

# Windows (PowerShell)
$env:TRACKER_MODE="demo"; streamlit run app.py
```

## Roadmap

**Completed (Phases 1-6):**

- Phase 1: SAA framework — ten sleeves across four parent categories (Equity 72% / Income 15% / Real Assets 10% / Cash 3%), tolerance bands, full written rationale
- Phase 2: ETF selection — benchmark vs. holding distinction documented per sleeve; weighted average ER ~10 bps
- Phase 3: Thesis and trade log — two-tier investment/position thesis system with theme tags; every trade links to a documented view
- Phase 4: Performance and attribution — GIPS TWR, BF attribution, custom blended benchmark, drift analysis
- Phase 5: Macro dashboard — CAPE, yield curve, Fed Funds, HY spreads with FRED integration and historical percentiles
- Phase 6: Quarterly PDF report — six-page WeasyPrint report, kaleido static chart rendering, auto-generated attribution commentary

**Post-Phase 7 directions (not built):**

- Factor exposure analysis (Fama-French loadings, quality/value/momentum tilts)
- Multi-account aggregation
- Transaction cost and tax drag estimation
- Additional benchmark dimensions (endowment composites, NACUBO)
