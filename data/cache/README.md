# data/cache — committed market-data inputs

Every tracked file here is a **committed input**: loaders read it, nothing at
runtime writes it, and `git status` staying clean is part of the repo's
verification discipline. Refresh is a deliberate, committed, human step.

| File | Policy | Writer | Notes |
|---|---|---|---|
| `ff_factors_us.csv` | tracked input, tool-refreshed | `tools/refresh_market_data.py` | Ken French daily FF5, US. Publication lags ~4-6 weeks; refresh monthly-ish. |
| `ff_factors_developed_exus.csv` | tracked input, tool-refreshed | `tools/refresh_market_data.py` | Ken French daily FF5, Developed ex-US. |
| `ff_umd_us.csv` | tracked input, tool-refreshed | `tools/refresh_market_data.py` | Ken French daily momentum (UMD). |
| `prices_hyg.parquet` | **pinned** — deliberately frozen | none (writer removed long ago) | HYG adjusted-close history for the CREDIT factor proxy. Adjusted closes re-derive on every distribution, so this point-in-time snapshot is not reproducible from the network. Do not add a refresh path; replacing it is a deliberate re-baselining decision. |
| `ff_beme_breakpoints.csv` | **gitignored cache** | `src/factors.py` (auto) | The one pure-cache case: regenerated on demand, never committed. |

Also governed by the same policy, outside this directory:
`data/shiller_cape.csv` and `data/trailing_pe.csv` (tracked inputs, refreshed
by the same tool). `ff_factors_global.csv` was deleted: Ken French ceased
daily Global 5-factor publication in June 2019, before this portfolio's
inception, so nothing could ever load it.

Staleness is **surfaced, not silently fixed**: loaders never fetch; the
Factor Profile, Macro, and SAA pages and the PDF render a staleness note when
a data frontier falls behind its threshold (`src/asof.py`:
`MARKET_DATA_STALE_DAYS_FACTORS` / `_VALUATION`, with the reasoning for the
thresholds commented at the constants). `tests/test_market_data_immutability.py`
enforces the no-runtime-writes guarantee against the `git ls-files data` set.
