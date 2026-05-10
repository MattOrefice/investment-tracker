# Post-Launch Backlog

Items deferred from pre-launch polish pass (Phase 8s, 2026-05-05). None are blockers for README or public launch.

## UI / UX

- **Factor Profile: UMD factor label** — "Mom" appears in the Carhart momentum table column. Consider displaying as "UMD (Mom)" for clarity on first viewing.
- **Performance page period selector** — "Since Inception" is the only period with real data for a <1-year portfolio; the 1Y / 3Y / 5Y rows show dashes. Consider greying out unavailable rows rather than showing `—` to reduce visual noise.
- **Macro page Shiller CAPE** — Data ends Sep 2023 (Yale file last updated at that point). If Yale resumes publishing, the monthly refresh will pick it up automatically; no code change needed. Monitor.

## Testing

- **AppTest render tests skip locally (4 skipped)** — `test_performance_has_headline_metrics`, `test_period_returns_table_renders`, `test_methodology_expander_present`, `test_build_caption_renders` all skip when `tracker.db` is empty (local dev). They pass on Cloud with demo.db. No action needed; documented for clarity.
- **Factor regression render tests** — Cover demo.db path; no unit test covers the Newey-West HAC path with synthetic returns. Low priority given the math is validated by statsmodels.

## Macro Indicators (deferred, not on FRED or requires separate sourcing)

- **ISM Manufacturing PMI** — Not available on FRED (IHS Markit / Institute for Supply Management is proprietary). Would require a paid data provider or web-scraping. Candidate for addition if a free FRED-equivalent source is found.
- **DXY (US Dollar Index)** — Not on FRED as DXY directly; approximate via DTWEXBGS (Broad Real Effective Exchange Rate) or ICE/Intercontinental Exchange data. Relevant to the International Developed and Emerging Markets thesis (dollar tailwind mean-reversion).
- **VIX (CBOE Volatility Index)** — FRED carries VIXCLS daily from 1990-01-02. Good fear/complacency gauge; would complement the HY OAS credit stress panel. Low-effort add.
- **ICSA (Initial Unemployment Claims)** — FRED carries weekly data from 1967. Leading recession indicator and useful alongside UNRATE for labor health. Low-effort add.

## Data / Infrastructure

- **FRED BAMLH0A0HYM2 (ICE BofA HY OAS)** — Restricted to May 2023+. Percentile computation reflects only the available window. Caption notes this. If FRED restores history, percentile will auto-correct on next cache refresh.
- **Global 5-factor regression** — Ken French discontinued daily global factors June 2019. Tab shows informational message. If Ken French ever resumes publication (unlikely), the early-return guard in `src/factors.py` will need to be removed.
- **Yahoo Finance rate limits** — `src/prices.py` uses direct v8/chart API. If Yahoo changes the endpoint, price fetching will break silently. Consider adding a last-successful-fetch timestamp to the `prices` table and surfacing a staleness warning if > 2 trading days old.

## README (separate phase)

- Full README rewrite is the final action before public launch — tracked separately.

## Security

- **Demo mode connection-level write guard** — `src/db.py get_connection()` opens SQLite in read-write mode regardless of `IS_DEMO`. A connection-level fence (`sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)` in demo mode) would be defense-in-depth against any future code path that inserts a write without an `IS_DEMO` guard. Blocked by the fact that price, dividend, macro, and quarter-snapshot caches all write via `get_connection()` in demo mode; making those writes go through a separate writable connection requires refactoring every cache write path. Implement if a clean two-connection pattern (read-only app conn / writable cache conn) is introduced. Tracked from Phase 8k write-guard audit (2026-05-08).
