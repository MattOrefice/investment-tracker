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
by the same tool). `ff_factors_global.csv` was deleted: Ken French ceased
daily Global 5-factor publication in June 2019, before this portfolio's
inception, so nothing could ever load it.

`prices_hyg.parquet` was retired in #386. It held HYG's history for the
fixed-income regression's CREDIT proxy, ended 2026-05-05, and nothing refreshed
it, so Q2 2026's regression read a proxy held flat for the quarter's last 56 days.
HYG now comes from the price layer, like every other ETF; the Risk page's credit
factor already read it there.

Hand-kept inputs outside this directory, with no refresh tool:
`data/etf_metadata.json` (fact-sheet figures for the style box, each stamped with
its source date; a quarter lock takes it only when every stamp falls inside the
quarter, otherwise the style box renders as pending) and `data/forward_eps.json`
(the S&P DJI forward-EPS estimate, a manual seam by design; the demo hides its
panel while no estimate is on file).

Staleness is **surfaced, not silently fixed**: loaders never fetch; the
Factor Profile, Macro, and SAA pages and the PDF render a staleness note when
a data frontier falls behind its threshold (`src/asof.py`:
`MARKET_DATA_STALE_DAYS_FACTORS` / `_VALUATION`, with the reasoning for the
thresholds commented at the constants). `tests/test_market_data_immutability.py`
enforces the no-runtime-writes guarantee against the `git ls-files data` set.
