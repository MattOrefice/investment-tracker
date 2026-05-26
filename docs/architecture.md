# Architecture

Technical design reference for the investment analytics tracker. Three-minute read.

---

## Overview

A personal investment analytics system built to mirror an institutional allocator workflow. The application runs in two modes controlled by the `TRACKER_MODE` environment variable:

- **personal** (default): local only, reads from `data/tracker.db` (real holdings, gitignored)
- **demo**: public deployment on Streamlit Community Cloud, reads from `data/demo.db` (fake paper trades, committed)

The same codebase handles both modes. `src/config.py` resolves `DB_PATH` and API keys from `st.secrets` first (Cloud), then `.env` (local).

---

## Data flow

Four external data sources feed the system:

```
Yahoo Finance (v8/chart API)  →  src/prices.py  →  SQLite prices table
FRED API                      →  src/macro.py   →  SQLite macro_cache table (24h TTL)
Ken French Data Library       →  src/factors.py →  data/ff_factors_*.csv (7-day TTL)
Yale/Shiller CAPE dataset     →  src/shiller.py →  data/shiller_cape.csv (30-day TTL)
```

All reads from external sources are cached locally. `src/prices.py` calls the Yahoo Finance v8/chart endpoint directly via `requests` (not the `yfinance` library) with a browser User-Agent header to avoid 429 rate-limiting. Factor ZIPs from Ken French are downloaded on demand and unpacked to CSV; the cache is refreshed when the file is older than 7 days or the most recent factor date exceeds a 35-day publication lag.

The SQLite database (`src/db.py`) is the single source of truth for portfolio state. `get_connection()` is the only connection factory; all modules that touch the DB import from there.

---

## Module organization

`src/` contains pure Python logic with no Streamlit imports. Every public function is unit-testable in isolation.

**Portfolio state**

| Module | Responsibility |
|--------|---------------|
| `db.py` | `get_connection()`, schema definitions |
| `config.py` | `DB_PATH`, `IS_DEMO`, API key resolution |
| `prices.py` | Yahoo Finance fetch + SQLite cache; `get_prices()`, `bulk_refresh()` |
| `holdings.py` | Net shares, `get_portfolio_value_series()`, sleeve weights, inception date |
| `returns.py` | Daily-linked TWR, Modified Dietz, `annualize()`, `period_return()` |

**Attribution and factor analysis**

| Module | Responsibility |
|--------|---------------|
| `attribution.py` | Brinson-Fachler decomposition; algebra check assert (allocation + selection = active) |
| `benchmarks.py` | SAA-target-weighted blended benchmark; per-sleeve benchmark series |
| `factors.py` | FF5 per-sleeve regressions (US and Developed ex-US); benchmark-relative regression; style box; dynamic interpretations |

**Macro and research**

| Module | Responsibility |
|--------|---------------|
| `macro.py` | FRED integration (yield curve, Fed Funds, HY OAS, USREC); CAPE excess yield; dynamic interpretations |
| `shiller.py` | CAPE from Yale dataset with local CSV fallback |
| `asset_evaluation.py` | Pure functions for SAA candidate analysis: Sharpe contribution, correlation, drawdown, mean-variance tangency |
| `positioning.py` | Active tilts, effective duration, scenario analysis (pure functions, no DB access) |
| `rebalance.py` | Band-breach detection, contribution allocation (pure functions, no DB access) |

**Operations and output**

| Module | Responsibility |
|--------|---------------|
| `tax_lots.py` | Lot-level cost basis, holding period, harvest candidates |
| `drip.py` | Dividend tracking, reinvestment simulation |
| `reports.py` | PDF assembly: data collection, Jinja2 render, WeasyPrint (Linux) / xhtml2pdf (Windows) |
| `ui_helpers.py` | `render_footer()`, `render_sidebar_footer()` — shared Streamlit chrome |
| `asof.py` | `as_of_banner()` — shared date display used on every page |

---

## Pages as presentation

Streamlit pages in `pages/` are thin: they import from `src/`, arrange columns, and render outputs. No analytical logic lives in a page file. This keeps all computation unit-testable without a Streamlit runtime.

`app.py` is the landing page. Streamlit auto-discovers numbered files in `pages/` for the sidebar. The `IS_DEMO` flag from `src/config.py` gates write operations and shows a demo-mode banner; no page bypasses this check.

Plotly figures are constructed in `src/` functions that return `go.Figure` objects; pages call `st.plotly_chart()`. PDF export reuses the same data-assembly logic via `src/reports.py`.

---

## Testing strategy

205 tests across three layers, organized to catch different failure modes:

**Layer 1 — Math identities**
Unit tests against hand-calculated values. Cover TWR chain-linking, Modified Dietz mid-period deposits, Brinson-Fachler algebra (allocation + selection must equal active return within 0.001 bps), and factor regression plumbing. Located in `tests/test_returns.py`, `tests/test_attribution.py`, `tests/test_factors.py`.

**Layer 2 — Reasonability bounds**
Tests that assert outputs fall within plausible ranges given demo-db inputs — portfolio values between $5k and $500k, alpha between −5000 and +5000 bps, R² between 0 and 1, sleeve weights summing to ~1.0. Located in `tests/test_integration_*.py`. These catch regressions that pass math checks but produce nonsensical values.

**Layer 3 — Prose and structure guards**
Snapshot tests (`tests/test_interpretation_snapshots.py`) pin exact string output of the eight `interpret_*` functions in `src/macro.py` and `src/factors.py`. Any change to interpretation logic triggers a diff; placeholder bugs (unclosed `{variable}` strings) are caught immediately. Repository structure tests (`tests/test_repo_structure.py`) assert that required files exist and CI fixtures are wired correctly.

CI runs on every commit via GitHub Actions.

---

## Key design decisions

**Per-sleeve factor regressions, not a single portfolio regression.**
Running one FF5 regression on the full portfolio mixes US equity, international, and real-asset returns into a model that only spans US factors. In testing, this produced a spurious +826 bps significant alpha — the model was misspecifying international return variance as unexplained residual. Per-sleeve regressions with region-appropriate factor sets (US factors for VOO/SPHQ/VTV/AVUV, Developed ex-US factors for VEA) eliminate this misspecification. The design decision is documented in `src/factors.py`'s module docstring.

**SAA-target-weighted blended benchmark.**
Performance is measured against a custom benchmark built from SAA target weights applied to each sleeve's benchmark ticker (SPY, EFA, IEF, etc.). This is not the S&P 500. It separates the question "did we beat the S&P 500?" (answered on the Performance page) from "did we add value relative to our own policy benchmark?" (answered on Benchmark Attribution). The blended benchmark is constructed in `src/benchmarks.py` and used as the Bench-RF regressor in `src/factors.py`.

**Dynamic interpretations driven from live data.**
Each metric panel on the Macro and Factor Profile pages ends with a prose sentence generated by an `interpret_*` function. These functions take numeric values (current reading, percentile) and return calibrated text tied to specific portfolio sleeves. The text updates as data updates — no hardcoded commentary. Snapshot tests lock the output so rewording is intentional, not accidental.

**Demo-mode write protection.**
`IS_DEMO` from `src/config.py` is checked before any SQLite write in the application layer. The demo deployment on Streamlit Community Cloud serves `data/demo.db` as a read-only artifact. This lets the same codebase serve both a live personal tracker and a public resume artifact without conditional logic scattered across pages.
