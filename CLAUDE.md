# Investment Analytics Tracker — Project Context

## Mission
Personal investment analytics system mirroring an institutional allocator workflow. Built for learning + interview credibility. Starting from ~$10k cash in a Fidelity taxable brokerage.

## Tech stack
Python 3.11+, Streamlit, SQLite, pandas, yfinance, fredapi, plotly, pytest.
Aesthetic: endowment quarterly report — sober, institutional, white space.

## Architecture
- `app.py` — Streamlit homepage entry point
- `pages/` — Streamlit auto-discovers numbered pages for sidebar nav
- `src/` — pure Python logic (no Streamlit imports), unit-testable
- `data/tracker.db` — SQLite, gitignored (contains real holdings)
- `tests/` — pytest

## Schema (SQLite, see src/db.py)
- `accounts` — multi-account from day one (Personal Fidelity seeded; Mom's account + retirement slot in later)
- `asset_classes` — SAA taxonomy: target weight, tolerance band, rationale, benchmark ticker
- `securities` — ticker → asset class mapping (drives attribution math)
- `theses` — independent of trades. A thesis is a *view*; one thesis can spawn multiple trades. This is the structural choice that turns this from a tracker into an allocator's journal.
- `trades` — buy/sell, optionally linked to a thesis
- `prices` — daily yfinance cache to avoid re-pulling history

## Phase plan
0. Foundation ← current
1. SAA framework
2. Security research / candidate comparison
3. Thesis + trade log
4. Performance + BHB attribution
5. Macro dashboard (CAPE, 2/10, US vs intl)
6. Quarterly PDF report
7. Polish (tests, README, methodology)

Do not build ahead of the current phase.

## Working style
- User is a Python vibe coder — applies code from AI, prefers reading to writing
- Explain non-obvious design decisions briefly (1-2 sentences), don't lecture
- Verify by running, not by inspection
- Suggest commit messages at meaningful checkpoints
- Push back on requests that contradict the phase plan or schema

## Allocator philosophy (informs design choices)
- Thesis-driven, not stock-picking
- Every trade documents: macro view, conviction (1-5), horizon, exit conditions
- Performance attribution separates allocation effect from selection effect (BHB)
- 85% growth / 15% fixed income SAA, slightly conservative for late-20s, deliberately
- Tax-aware: minimize turnover, flag qualified vs ordinary, watch wash sales
- Benchmark: S&P 500 total return; secondary: 60/40 and endowment composites

## Strategic Asset Allocation (locked, Phase 1)

Top-level: Growth 72% / Income 15% / Real Assets 10% / Cash 3%
Tolerance band rule: ±3% for sleeves ≥10%, ±2% for sleeves <10%

### Sleeves

**1. US Large Core — 16% (±3%) — benchmark SPY**
Anchor exposure to US large-cap equity. Cap-weighted S&P 500 representing the most efficient, best-governed, highest-quality earnings stream in global markets. The 16% weight is deliberately not the largest US sleeve — Quality at 14% comes close — because most US large exposure should express a factor view rather than passive cap-weight. Core's job is to be the un-opinionated anchor: when factor tilts go through inevitable multi-year underperformance windows, Core ensures presence in the broad equity rally. Would increase if factor premia compress further or if conviction in active factor tilts erodes.

**2. US Large Quality — 14% (±3%) — benchmark QUAL**
Largest factor tilt in the portfolio, deliberately so. Quality (high ROIC, low leverage, stable earnings) is the only factor that has strengthened post-academic-publication, because it's not statistical arbitrage — it's a structural preference for better businesses that doesn't get arbitraged away. Empirically, quality has delivered equity-like returns with materially lower drawdowns, which matters in a 30+ year compounding window where avoiding deep drawdowns dominates terminal wealth. 14% reflects high conviction without being so concentrated that a factor regime change would severely damage the portfolio. Would reduce if quality screens become dominated by a single sector to the point of losing diversification.

**3. US Large Value — 8% (±2%) — benchmark IWD**
Smaller, contextual factor bet on growth-vs-value mean reversion. The Russell 1000 Value vs. Growth spread is at the deepest valuation gap since 2000. Mean-reversion case is real; the historical base rate over 10-year windows favors value at these spreads. But value has had multiple "this time it'll work" moments since 2010 that didn't deliver, so the position is sized to express the view without betting the portfolio on it. 8% out of 38% total US Large equals 21% of US large-cap exposure — a tilt, not a thesis. Would increase if the spread widens further or if real rates normalize; would reduce if growth's earnings advantage compounds another 5+ years.

**4. US Small Cap — 7% (±2%) — benchmark IWM**
Size factor exposure, sized modestly because the evidence is the weakest. Small-cap historically delivered a ~1-2% premium over large, but the premium has been weak post-publication and arguably absent for the last 15 years. Small-caps offer genuine diversification: more domestic-economy-leveraged, more interest-rate-sensitive, and less correlated with mega-cap tech concentration. 7% is enough to matter if the size premium reasserts (especially with valuation discounts vs. large-cap at multi-decade lows) without anchoring the portfolio to a factor with shaky empirical support. Would increase if real rates normalize and small-cap quality screens produce attractive opportunities.

**5. International Developed — 19% (±3%) — benchmark EFA**
Largest non-US sleeve, expressing two distinct views. First, valuation: developed international CAPE is around 18 vs. US at 28, and starting valuation is the dominant driver of long-run returns over 10+ year windows. Second, regime diversification: the dollar has been a 15-year tailwind for US-domiciled investors, and historical mean reversion suggests that tailwind isn't permanent. 19% is meaningfully overweight typical US-investor home bias (10-15%) but underweight global market cap (~40%); it expresses a real view without being a contrarian moonshot. Would increase if the valuation gap widens or the dollar weakens materially; would reduce if European or Japanese structural reform stalls further.

**6. Emerging Markets — 8% (±2%) — benchmark EEM**
Higher-growth, higher-volatility diversifier. EM equities offer demographic tailwinds, structurally cheaper valuations, and exposure to growth profiles that don't exist in developed markets. The 8% weight reflects respect for the asymmetric risk — EM has had 50%+ drawdowns multiple times and includes meaningful country-specific governance risk (China especially). Modestly long EM at attractive valuations is preferable to chasing it after a rally. Would increase if EM ex-China valuations become exceptionally cheap; would reduce if China governance risk materially worsens or if EM index construction concentrates further into a single country.

**7. Core Fixed Income (Intermediate Treasuries) — 9% (±2%) — benchmark IEF**
Duration as recession ballast, sized for an aggressive growth portfolio. Classical 60/40 doctrine assumed Treasuries reliably hedged equity drawdowns; 2022 disproved that under inflationary regimes. But in deflationary or recessionary drawdowns — which remain the more common equity tail risk — intermediate Treasuries still work. 9% in a 72% growth portfolio is intentionally thin: not relying on FI for return, relying on it for drawdown buffering and rebalancing-into-equity-weakness optionality. Would increase if real yields exceed 3% (making FI competitive on a return basis) or if horizon shortens. Would reduce if inflation regime persists and nominal duration stops hedging anything.

**8. TIPS — 6% (±2%) — benchmark TIP**
Inflation-hedged real-yield exposure, sized for a young investor's actual risk. The biggest FI risk at 27 isn't a market crash — it's having returns silently destroyed by an inflationary decade. TIPS hedge that risk directly via CPI linkage. 6% is 40% of the FI sleeve, heavier than typical institutional allocations (usually 20-30%), reflecting that the horizon is long enough that real-return preservation dominates nominal. Post-2022 also reinforced that nominal Treasuries don't always hedge stocks the way 60/40 doctrine claimed — TIPS at least hedge inflation reliably. Would increase if real yields rise above 2.5%; would reduce if horizon shortens or if confidence in disinflation persisting grows.

**9. Real Assets (REITs/Commodities blend) — 10% (±2%) — benchmark 50% VNQ + 50% DJP**
Inflation-correlated diversifier with different risk drivers than equity or duration. Public REITs and commodities aren't perfect substitutes for the private real estate and natural resource exposure endowments hold, but at $10k they're the only honest implementation. 10% is large enough to actually move the portfolio's behavior in inflationary regimes (which 2-3% wouldn't) without being so large that REIT/commodity volatility — both can have 30%+ drawdowns — overwhelms the equity sleeves. Would increase if I gain access to private real estate or if commodities enter sustained backwardation; would reduce in deflationary regimes where these assets stop earning their diversification benefit.

**10. Cash / SPAXX — 3% (±2%) — benchmark BIL**
Operational liquidity, not strategic dry powder. At 27 with a 30+ year horizon, holding meaningful cash is performance drag — every 1% of cash at long-run equity returns of ~7% real costs ~$2.4k in terminal wealth per $10k of base capital over 30 years. 3% handles rebalancing friction (funding tax-inefficient sleeves without forced sales), small drawdowns without selling at the bottom, and occasional opportunistic deployment. SPAXX yields ~4-5% currently, so the drag is muted. Would increase closer to retirement or with shorter-duration liabilities; would reduce toward 1-2% if cash yields collapse below 2%.

### Parent categories (for top-level attribution)
- Growth: 72% (sleeves 1-6)
- Income: 15% (sleeves 7-8)
- Real Assets: 10% (sleeve 9)
- Cash: 3% (sleeve 10)

### Implementation notes for the project
- Real Assets benchmark is a blend (50% VNQ + 50% DJP) — Phase 4 attribution code must handle this
- Quality > Value tilt is deliberate; momentum is excluded for tax reasons (high turnover)
- Tolerance bands force rebalance review when actual drifts beyond target ± band
- Rationales should be displayed verbatim on the SAA Streamlit page (they're the interview content)

## Phase plan (updated)
0. Foundation — complete
1. SAA framework — complete
2. Security research / candidate comparison ← current
3. Thesis + trade log
4. Performance + BHB attribution
5. Macro dashboard (CAPE, 2/10, US vs intl)
6. Quarterly PDF report
7. Polish (tests, README, methodology)

## Session log

### Phase 1 complete (2026-04-29)
- SAA locked: 10 sleeves across 4 parent categories (Growth 72% / Income 15% / Real Assets 10% / Cash 3%), weights sum to 100%
- `asset_classes` table seeded via `src/seed_saa.py` (idempotent); sub-class naming note: sleeve 9 is "REITs & Commodities" to avoid UNIQUE collision with the "Real Assets" parent category
- SAA page (`pages/1_SAA.py`) renders: stacked bar chart, sleeve table, per-sleeve rationale expanders, Phase 4 drift placeholder
- Phase 2 (Security Research) is next: candidate ETF comparison per asset class

## Dual-mode design (planted Phase 1, deployed Phase 4+)

The same codebase runs in two modes, controlled by env var `TRACKER_MODE`:

- **personal** (default): runs locally on user's laptop, uses `data/tracker.db` with real holdings, gitignored, never deployed.
- **demo**: runs on a public URL (target: Streamlit Community Cloud), uses `data/demo.db` with fake trades reflecting the same SAA, password-protected, deliberately public. Acts as resume artifact and shareable demo.

### Demo data philosophy
Demo mode uses **the user's own SAA** (the locked Phase 1 taxonomy) with **fake trade data dated 6-12 months back**, so the demo tells the user's story end to end: her allocation framework, her thesis-driven trades, her attribution math. NOT Yale/PRINCO allocations — the demo must show original allocator thinking, not borrowed authority.

Future enhancement (Phase 5+): add a side panel comparing user's SAA to published endowment allocations (Yale, PRINCO, NACUBO) for context. The user's SAA stays primary; the comparison is supporting material.

### Privacy boundaries
- `data/tracker.db` is gitignored and stays local
- `data/demo.db` is committed (it's deliberately fake)
- API keys (FRED, etc.) live in `.env`, gitignored, never committed
- Account name "Personal Fidelity" is fine for personal mode; demo mode uses generic "Demo Brokerage"

### Auth on public URL
Streamlit's built-in password protection (single shared password). Configured via `.streamlit/secrets.toml` on the deployment host. Not committed to repo.

### Repo visibility
GitHub **public**. README is treated as a deliverable — recruiters will read it. Should explain the project's purpose, the dual-mode design, the SAA philosophy, and link to the live demo. To be polished in Phase 7.

### Deployment timing
Target deployment: end of Phase 4 (after performance attribution works). Earlier phases are too hollow; Phase 4 gives the demo enough analytical meat to be worth showing.

### Implementation status
- src/config.py — ✅ created Phase 1
- data/demo.db + seed script — ❌ Phase 4
- Streamlit Community Cloud deployment — ❌ Phase 4
- Auth password — ❌ Phase 4
- Polished README — ❌ Phase 7

When building any new feature, both modes must keep working. Pages should query the database via src/db.py (which uses src/config.py for path), never hardcoded.

## Phase 1 — COMPLETE

- SAA locked: 10 sleeves across Growth/Income/Real Assets/Cash, summing to 100%
- asset_classes table seeded
- pages/1_SAA.py rendering correctly with allocation chart, sleeve table, and rationale expanders
- Dual-mode foundation planted (src/config.py)

**Next: Phase 2 — Security Research / Candidate ETF Comparison**
