# Post-Launch Backlog

Items deferred from pre-launch polish pass (Phase 8s, 2026-05-05). None are blockers for README or public launch.

## UI / UX

- **Factor Profile: UMD factor label** — "Mom" appears in the Carhart momentum table column. Consider displaying as "UMD (Mom)" for clarity on first viewing.
- **Performance page period selector** — "Since Inception" is the only period with real data for a <1-year portfolio; the 1Y / 3Y / 5Y rows show dashes. Consider greying out unavailable rows rather than showing `—` to reduce visual noise.
- **Macro page Shiller CAPE** — Data ends Sep 2023 (Yale file last updated at that point). If Yale resumes publishing, the monthly refresh will pick it up automatically; no code change needed. Monitor.

## Testing

- **AppTest render tests skip locally (4 skipped)** — `test_performance_has_headline_metrics`, `test_period_returns_table_renders`, `test_methodology_expander_present`, `test_build_caption_renders` all skip when `tracker.db` is empty (local dev). They pass on Cloud with demo.db. No action needed; documented for clarity.
- **Factor regression render tests** — Cover demo.db path; no unit test covers the Newey-West HAC path with synthetic returns. Low priority given the math is validated by statsmodels.

## Data / Infrastructure

- **FRED BAMLH0A0HYM2 (ICE BofA HY OAS)** — Restricted to May 2023+. Percentile computation reflects only the available window. Caption notes this. If FRED restores history, percentile will auto-correct on next cache refresh.
- **Global 5-factor regression** — Ken French discontinued daily global factors June 2019. Tab shows informational message. If Ken French ever resumes publication (unlikely), the early-return guard in `src/factors.py` will need to be removed.
- **Yahoo Finance rate limits** — `src/prices.py` uses direct v8/chart API. If Yahoo changes the endpoint, price fetching will break silently. Consider adding a last-successful-fetch timestamp to the `prices` table and surfacing a staleness warning if > 2 trading days old.

## Attribution methodology

- **Phase 9.5 — Multi-level attribution.** Add a SAA-design-vs-naive-benchmark layer above the existing drift/selection BF. Decompose total active return into (a) SAA strategic tilts vs. 60/40 or pure-equity benchmark, then (b) drift/selection vs. SAA. Currently disclosed via methodology footnote (Phase 8t, Section 4) with pointer to Factor Profile for strategic-tilt evidence; full multi-level decomposition is a methodology build, not a pre-launch polish.

## README (separate phase)

- Full README rewrite is the final action before public launch — tracked separately.
