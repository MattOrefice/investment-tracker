# data/cache — committed market-data inputs

Every tracked file here is a **committed input**: loaders read it, nothing at
runtime writes it, and `git status` staying clean is part of the repo's
verification discipline. Refresh is a deliberate, committed, human step.

| File | Policy | Writer | Notes |
|---|---|---|---|
| `ff_factors_us.csv` | tracked input, tool-refreshed | `tools/refresh_market_data.py` | Ken French daily FF5, US. Publication lags ~4-6 weeks; refresh monthly-ish. |
| `ff_factors_developed_exus.csv` | tracked input, tool-refreshed | `tools/refresh_market_data.py` | Ken French daily FF5, Developed ex-US. |
| `ff_umd_us.csv` | tracked input, tool-refreshed | `tools/refresh_market_data.py` | Ken French daily momentum (UMD). |
| `ff_beme_breakpoints.csv` | **gitignored cache** | `src/factors.py` (auto) | The one pure-cache case: regenerated on demand, never committed. |

Also governed by the same policy, outside this directory:
`data/shiller_cape.csv` and `data/trailing_pe.csv` (tracked inputs, refreshed
by the same tool). Each CAPE row holds the month it describes, the value, the
date it was read (`read_on`) and the source that answered (`multpl`, or `yale`
for the fallback); a quarter lock takes its last month only from a reading made
after that month's last NYSE session. `ff_factors_global.csv` was deleted: Ken French ceased
daily Global 5-factor publication in June 2019, before this portfolio's
inception, so nothing could ever load it.

`data/backtest/monthly_returns.csv` is the policy backtest's history (#667): one row
per month from February 2006 to the last complete month, one column per series. Each
cell is a monthly total return, empty before the series begins. The series are every
policy fund, every proxy, the benchmark legs no fund or proxy supplies (AGG, for the
app's 60/40), and DGS3MO, the 3-month bill series `src/risk_free.py` averages.
`src/policy_backtest.py` reads it, never fetches. Its only writer is
`tools/refresh_backtest_history.py`. The writer takes each
series' returns from one fetch of its whole history, so a split can never put two scales
in one series, and it refuses a series with a gap. `data/backtest/series.json` is
hand-kept beside it. It names each fund's proxy, the last month the proxy stands in and
why (the fund's inception, or SPHQ's and IDHQ's change to a quality index), with the
issuers' citations. Nothing in either file reaches data/demo.db or data/tracker.db.

`prices_hyg.parquet` was retired in #386. It held HYG's history for the
fixed-income regression's CREDIT proxy, ended 2026-05-05, and nothing refreshed
it, so Q2 2026's regression read a proxy held flat for the quarter's last 56 days.
HYG now comes from the price layer, like every other ETF; the Risk page's credit
factor already read it there.

Hand-kept inputs outside this directory, with no refresh tool:
`data/etf_metadata.json` (fund durations only: each bond fund's duration, and AGG's
for the Bloomberg US Agg, with its measure, source and as-of date; a quarter lock
takes it only when every as-of falls inside the quarter, otherwise the report's
duration line renders as pending; the equity style box it once fed was withdrawn
in #477) and `data/forward_eps.json`
(the S&P DJI forward-EPS estimate, a manual seam by design; the demo hides its
panel while no estimate is on file).

Staleness is **surfaced, not silently fixed**: loaders never fetch; the
Factor Profile, Macro, and SAA pages and the PDF render a staleness note when
a data frontier falls behind its threshold (`src/asof.py`:
`MARKET_DATA_STALE_DAYS_FACTORS` / `_VALUATION`, with the reasoning for the
thresholds commented at the constants). `tests/test_market_data_immutability.py`
enforces the no-runtime-writes guarantee against the `git ls-files data` set.
