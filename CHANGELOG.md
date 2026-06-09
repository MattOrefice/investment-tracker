# Changelog

All notable changes to this project are documented below. Phases represent grouped
deliverables — analytical features, polish passes, and infrastructure work — rather
than arbitrary version bumps.

The live demo is at https://mattorefice-investment.streamlit.app/.

---

## Post-v1.0 patches

### Phase 25.0: SAA revision — 15% FI to 10% FI (retroactive)
*May 27, 2026*

Revised the Strategic Asset Allocation to reduce Fixed Income from 15% to 10%,
redistributing that 5% to Equity (72% → 78%). Parent split is now 78/10/10/2
(Equity / Income / Real Assets / Cash). All ten sub-class weights updated
(Core FI 9%→6%, TIPS 6%→4%, Equity sleeves each +1%).

Migration script (`tools/migrate_saa_phase25.py`) patches `asset_classes`,
`theses`, `securities` rationale, and rationale text in both `data/tracker.db`
and `data/demo.db`. Demo paper trades wiped and re-seeded at new SAA weights.
Affected source files: `src/seed_saa.py`, `src/asset_evaluation.py`,
`src/factors.py` (`_FI_WEIGHTS`, `_SAA_US`), `src/endowment_benchmarks.py`,
`src/seed_securities.py` (VGIT rationale), `src/reports.py`, pages fallback
defaults, `README.md`, and all relevant test fixtures.

### Table sort fix
*May 27, 2026*

Six tables across Performance and Capital Deployment pages now sort
numerically when column headers are clicked. Previous behavior: values
stored as pre-formatted strings produced lexical sort
(e.g., "+10.5" sorted after "+9.5"). Fix: raw floats with column_config
NumberColumn format strings preserving visual display.

Tables fixed:
  - Performance: Drift table (Target, Actual, Drift bps)
  - Capital Deployment: Deploy New Cash suggestions table
  - Capital Deployment: Projected weights table after cash deployment
  - Capital Deployment: Rebalancing Check drift table
  - Capital Deployment: Band-breach rebalancing buy suggestions

Tables kept STATIC by design (row order is structurally meaningful):
  - Performance: Period Returns (1M/3M/YTD/1Y/SI horizon sequence)
  - Factor Profile: Portfolio and Fixed Income regression tables
    (Alpha/Mkt-RF/SMB/HML/RMW factor-identity order)
  - Benchmark Attribution: Benchmark regression table (same)

---

## Repository Maintenance

### Commit attribution cleanup — Phase 52
*May 26, 2026*

Co-Authored-By trailers stripped from all commits reachable from main.
Project convention: AI-assisted implementation does not warrant co-author
attribution; the repo is single-author (Matt Orefice). One commit on main
carried the trailer (Phase 51 CHANGELOG commit); rewritten via
`git commit-tree` with identical tree. v1.0 tag recreated at the new HEAD.
Pre-strip state preserved on `origin/main-pre-attribution-strip-backup`
for 30 days.

Note: `origin/main-pre-rewrite-backup` (Phase 51 backup) still contains 7
commits with Co-Authored-By trailers. These are not reachable from main
but GitHub may still parse them. Delete that branch after June 25, 2026 to
fully resolve the Contributors count.

### Commit history reorganization — Phase 51
*May 26, 2026*

Commit history selectively rewritten for Phase 43–50 (33 sub-phase commits
collapsed into 8 prose-style commits). The Phase 0–42 history is unchanged.
Original history preserved on remote branch `main-pre-rewrite-backup` for 30
days post-rewrite. Tagged v1.0 at completion.

Rewrite strategy: `git commit-tree` used to build new commit objects from the
existing tree snapshots (no interactive rebase, no working-tree conflict risk).
Force-pushed to origin with `--force-with-lease`.

---

## Phase 50 — Closeout
*May 26, 2026*

Final loose-end cleanup after the Phase 44-49 analytical-page polish wave closed.

- **50A** — app.py landing page verified against current sidebar hierarchy and
  analytical structure. Six cards across two rows confirmed correct (SAA,
  Performance, Benchmark Attribution, Factor Profile, Macro, Asset Evaluation);
  context paragraph references 10-sleeve SAA accurately; recency signal uses
  `as_of_banner()` dynamic source. No changes required.
- **50B** — Correlations page Pandas4Warning silenced by adding explicit
  `sort=False` to the single `pd.concat` call at line 301 that previously
  relied on deprecated implicit default sorting behavior.
- **50C** — CHANGELOG entry documenting Phase 50 closeout.

Status of deferred items after Phase 50:
- Commit history reorganization remains backburner (force-push incompatible
  with active phase work — active phase work is now genuinely settled).

---

## Phase 48.1 — CI cleanup and deployment convention
*May 25, 2026*

Fixed three failing tests that had kept CI red since Phase 45A. Captured the
three-proof deployment convention that all subsequent phases must satisfy.

- **48.1** — Repaired `AppTest.write` API breakage in Benchmark Attribution render tests
  (Streamlit 1.57.0 removed `.write`; `st.write(str)` now appears in `.markdown`);
  corrected Performance page pin from "isolates implementation alpha" to
  "isolate implementation alpha from SAA-design effects" (pin was written against
  commit-message wording, page code always had the 'to isolate' form) (cc3c580..HEAD)

### Render test convention (effective Phase 48.3)

Render tests that pin conditional content must exercise the conditional
branch where the content renders, not skip when the content is absent.
A skip guard that fires when the asserted text is missing is the test
disarming its own assertion. Either set up the fixture to enter the
conditional branch, or delete the test with explanation. Do not leave
skipping ghosts.

### Deployment convention (effective Phase 48.1)

Every phase closeout requires all three proofs before declaring done:

1. **Push verification** — `git log origin/main..HEAD` is empty (local commits landed)
2. **Cloud deployment** — incognito screenshot confirms the live demo renders correctly
3. **CI green** — GitHub Actions run on the latest commit shows all checks passing

These three checks are non-overlapping. Phases 45A–48 shipped with proofs 1 and 2
green but proof 3 red. The CI failures were not surfaced for multiple phases because
test-skip guards masked them locally (data-dependent tests skip when no portfolio data
is present, but fail in CI against demo.db). Going forward, explicitly open the
GitHub Actions run after every push and confirm the green checkmark before closing.

---

## Fidelity transaction-CSV import
_2026-06-09_

Added a Fidelity transaction-CSV importer to the Trade Log: lot-level parsing
(each fill logged separately for tax-lot fidelity), non-trade classification
(dividends/reinvestments excluded with a summary), unknown-ticker pre-filter,
and composite-key multiset dedup so re-importing a cumulative export never
double-logs. Reuses the existing guarded write path; no schema change.

---

## Macro percentile caption fix + NFCI financial-conditions panel
_2026-06-08_

Corrected the global percentile caption to accurately describe the windowed
(macro indicators) vs full-history (valuation, credit, factor-regime,
value-spread) basis — the per-panel labels were already correct; only the
global header over-claimed. Added an NFCI (Chicago Fed National Financial
Conditions Index) panel: a composite financial-conditions read with a
full-history percentile and sign-aware interpretation, the one genuine
axis-gap beyond the existing rate-vol and credit components. No existing
percentile logic changed.

---

## Phase 38c — SAA table display polish
_2026-06-08_

Display-only formatting of the Sleeve Allocation table: bands render as
whole percents (3/2) and numeric columns are tightened. No change to
target values, band values, or the 100.0% sum.

---

## Phase 38b-2 — Brinson-Fachler ex-cash + operational cash-drag term
_2026-06-08_

Brinson-Fachler attribution now computes on an invested (ex-cash) basis
matching the benchmark, with operational cash drag reported as an explicit
term: strategic active (ex-cash) + cash drag = total active. The total active
return and TWR are unchanged — only the decomposition is new; the strategic
figure rises only because the operational float drag is now a separate line.
The BF↔Stage-2 reconciliation is bridged by the drag term and holds. Two-stage
unchanged (Stage 2 = strategic ex-cash + cash drag). Retroactive, matching
Phase 25/38a. SI: total active +529.5 bps = strategic +595.7 + cash drag −66.2.

---

## Phase 38b-1 — Household basis clarification
_2026-06-08_

Documented the Household View's deliberate whole-household basis
(cash and off-SAA in the denominator, drift informational) vs the
demo SAA pages' ex-cash basis, and guarded that household cash
routes to the off-SAA bucket. No denominator or weight change;
clarification and test only.

---

## Phase 38a — Ex-cash SAA core
_2026-06-08_

Reclassified cash from a strategic sleeve to operational residual: the 9
non-cash sleeves rescale to 100% and all strategic weights, drift, and
deployment math are measured ex-cash (invested-value denominator), with
operational SPAXX shown separately as an untargeted figure. Reconciled the
two target sources behind an agreement test and closed the mixed-denominator
hazard (deployment dollars and the three drift tables verified consistent).
Retroactive policy revision, matching Phase 25; price/return history
untouched. Household and Brinson-Fachler cash-asymmetry alignment deferred
to 38b.

---

## Phase 37 — Market Snapshot regime dashboard
_2026-06-08_

Refocused the Market Snapshot as a recent-moves equity-regime
view: removed the one-day cross-asset tape and added regional
leadership (US/Intl/EM), a stocks-vs-bonds risk-on/off trend,
and a broad-market trend read (S&P vs 200-day MA). Size & Value
and Sector Movers unchanged.

---

## Phase 36 — Market Snapshot visual polish
_2026-06-08_

Display-only polish of the Market Snapshot page: promoted the
cross-asset tape to a top summary line, added SPY/AGG/UUP and
size/value supporting visuals, defaulted the sector view to YTD,
tightened layout, and cleared a deprecation warning. No computed
numbers changed.

---

## Maintenance — Performance drift table consolidation
_2026-06-05_

Sourced the Performance page drift table from the canonical compute_drift
helper instead of an inline recomputation, removing duplication.
Display-invariant; the table now agrees with the Capital Deployment
Rebalancing Check sleeve-for-sleeve.

---

## Phase 35 — Market Snapshot page
_2026-06-05_

Added a public Market Snapshot page dated to the last exchange close:
trailing-window size-vs-large and value-vs-growth relative performance tied to the
SAA tilts, SPDR sector movers ranked by a selectable window, and a deterministic
figures-only market line (no narrative/LLM). As-of derived from the actual latest
price date. Linked from the landing grid under Markets & Macro. First
outward-looking market view; kept honest (last-close-dated, figures-only).

---

## Phase 33 — Tax-aware rebalancing-band status
_2026-06-05_

Made SAA tolerance-band status explicit and surfaced the tax-aware rationale for
the buy-only rebalancer: drift is corrected with new contributions, not by selling
overweight sleeves (which would realize capital gains). Added a "why buy-only" note
and per-sleeve corrective-action text on Capital Deployment, a band-status verdict
naming the closest-to-breach sleeve by headroom, and an at-a-glance band-status
line on the SAA page. No corrective sells; no manufactured demo drift.

---

## Phase 32 — Candidate correlation screen
_2026-06-05_

Generalized the Asset Evaluation page into a reusable candidate screen: enter any
ticker to see per-sleeve correlation, average correlation to the SAA sleeves, a
rolling candidate-to-sleeves line, and a diversifies-vs-doubles-down verdict. QQQ
reads as a US large-growth double-down; GLD as a genuine diversifier. The Bitcoin
case study is unchanged.

---

## Phase 31 — Rolling sleeve correlations
_2026-06-05_

Added an average-pairwise-correlation-over-time view to the Correlations page:
mean pairwise rolling correlation across the SAA sleeves with a dispersion band,
an extended-history toggle (reaches 2008), and an equity-vs-bond-equity
decomposition. Shows diversification compressing in stress — equity sleeves
converge toward +1 while bonds decouple. Computation reusable
(asset_evaluation.py) for the upcoming candidate-correlation work.

---

## Phase 30 — Factor valuation spread
_2026-06-05_

Added a value-vs-growth valuation spread to the Factor Regime section: log of
the high/low NYSE book-to-market percentiles (Ken French BE/ME breakpoints), with
a fixed full-history percentile and banded interpretation. Distinguishes whether
value is cheap or expensive vs history — the mean-reversion signal, complementary
to the existing trailing-performance read. Size valuation omitted with rationale.
Existing 5-factor loader untouched.

---

## Phase 29 — Factor Regime on Macro page
_2026-06-04_

Added a Factor Regime section to the Macro page: trailing-12-month size (small
vs large) and style (value vs growth) factor performance, overlaying Fama-French
long-short premia with long-only ETF proxies around a zero reference. Includes an
adjustable lookback window, historical percentile per series (fixed full-history
denominator), and dynamic interpretation prose conditioned on sign and
percentile. Surfaces whether the SAA's small-cap and value tilts are currently
rewarded.

---

## Phase 28 — Household performance and benchmarks
_2026-05-29_

Added account-level performance display to the personal-mode Household View:
Fidelity's time-weighted and money-weighted returns per account (toggle),
a household-vs-benchmark 1Y comparison, and polish (full as-of date, removed
redundant off-SAA chart). Returns are recorded from Fidelity, displayed not
computed. Demo mode unchanged.

- **28.0** — Account Performance section: per-account TWR/MWR table (7 accounts;
  3 not in Fidelity's export show as not reported), as-of date caption fix,
  redundant off-SAA chart removed
- **28.1** — TWR/MWR radio toggle with real Fidelity figures, household-vs-benchmark
  1Y block (6 benchmarks), diversification framing observation

---

## Phase 27 — Methodology note
_2026-05-29_

Added a personal-mode methodology note to the Household View page
documenting the household asset-location analysis. Demo mode unchanged.

- Collapsed expander at the bottom of the Household View page
- `methodology_note_markdown()` helper in `src/household.py`; 4 content-guard tests added

---

## Phase 26 — Household View improvements
_2026-05-28_

Builds on Phase 25 with editorial framing and analytical depth on the
personal-mode Household View page. Demo mode unchanged.

- **26.0** — Advisor vs SAA strategic comparison section
- **26.1** — Footer deduplication, sleeve display names, drift sort,
  actionable/observed reframing, top-N tax-drag ranking, sleeve
  substitution mapping, concentration panel
- Marked 22 live-data macro render tests with `@pytest.mark.live_data`
  and excluded from default suite

---

## Phase 25 — Personal-mode household aggregation layer
*May 27–28, 2026*

Adds a personal-mode household aggregation layer; demo mode unchanged. Ingests a
multi-account Fidelity CSV export, looks through target-date and allocation funds into
underlying sleeves, and aggregates across all accounts against the SAA with drift
reporting.

- **25.1** — Fidelity CSV ingestion module (`src/ingestion/fidelity.py`) (607effb)
- **25.2** — Accounts metadata table; pseudonymization and `managed_by` flags (431fc8f)
- **25.3** — Household securities sleeve mapping and loader (c4fe5cc)
- **25.4** — Fund composition look-through for target-date and allocation funds (75980dd)
- **25.5** — Household allocation aggregation with look-through, scope filtering, and
  SAA drift (a8073bc)
- **25.6** — Household View page: scope/look-through toggles, off-SAA reporting, and
  tax-location flags; page registered in `st.navigation` only when
  `TRACKER_MODE=personal` (2628775, 3778fde)

---

## Phase 24 — Landing page treatment and consolidation
*May 13, 2026*

Rebuilt the landing page as an institutional entry point and reordered the sidebar
to surface the four analytical centerpieces. Extended the README with a hero
screenshot, expanded methodology section, and implementation details.

- **24** — Twelve pages renumbered to put Performance, Macro, Factor Profile, and
  Asset Evaluation at positions 2–5; landing page rebuilt with byline, context
  paragraph, and four "start here" cards (328c628)
- **24.1** — Hero screenshot of Benchmark Attribution page captured via Playwright
  and embedded in README (cee87ee, superseded by 24.2)
- **24.2** — Hero image swapped to the Macro Dashboard (c505569)
- **24.3** — Asset Evaluation promoted from position 12 to position 5; landing card 4
  swapped from Factor Profile to Asset Evaluation; sixth README methodology bullet
  added (8ced911)
- **24.4** — Collapsed "How to read this page" expanders added to Factor Profile,
  Asset Evaluation, and Benchmark Attribution; date stamp audit confirmed existing
  consistency across all pages (8b4ee09)
- **24.5** — Snapshot tests pinning the exact output of all 8 dynamic interpretation
  functions; 20 tests with per-branch coverage; bug-catching demo confirmed (b414112)

## Phase 23 — Dynamic macro interpretations and repo polish
*May 11–13, 2026*

Added threshold-and-branch interpretation functions for every macro and factor panel,
converting static prose into dynamic text derived from live data values. Cleaned the
git commit history and polished the README for public presentation.

- **23** — Dynamic interpretations for ECY, HY spreads, yield curve, GDP growth,
  and US vs. International return spread; Fama-French factor glossary panel; US vs.
  International return spread added to Macro dashboard; Tax Lots tooltip (755ac3f)
- **23.2** — Fix ECY/HY interpretation unit mismatch (percent vs. decimal); extended
  placeholder guard to catch both curly-brace and square-bracket unrendered
  templates (cde49cf)
- **23.3** — Naming consistency across pages; unemployment rate delta field; US vs.
  Intl annotation labels (2f0a1b8)
- **23.4** — Persistent contact footer (`render_sidebar_footer`) added to the sidebar
  on every page; CI guard test confirms all pages call it (11d1a4d)
- **23.5** — Stripped AI-tool attribution trailers from all 192 historical commits
  via `git-filter-repo`; commit identity normalized to Matthew Orefice throughout
- **23.6** — README rebuilt with header polish, methodology section (SAA-as-policy,
  BHB, FF5, regime, tax-aware, candidate asset evaluation), and implementation
  details (f04250d, 8e070ab, 823e4f5)

## Phase 22 — Write protection and Capital Deployment UX
*May 11, 2026*

Added a global write guard blocking demo-mode data mutations; introduced an Execute
and Log button on Capital Deployment to record approved trades directly from the
allocation-suggestion workflow.

- **22** — Global write guard (`DEMO_WRITE_GUARD`) raises on any DB write in demo
  mode; Execute and Log button with confirmation modal on Capital Deployment (24ffb77)
- **22.1** — Tolerance constant extracted; button guard fix; trade form always visible
  regardless of write-guard state (8ca5106)

## Phase 21 — Contribution allocator
*May 11, 2026*

Implemented a contribution allocator that translates a new cash deposit into
per-sleeve purchase suggestions based on current drift from SAA targets.

- **21** — Contribution allocator with editable suggestions per sleeve; deploy-and-
  execute workflow (60bcb62)
- **21.2** — Sum-invariant constraint fix (suggestions always sum to deposit amount);
  removed debug expander; added production test (3d0aa85)

## Phase 20 — Buy-only rebalancer
*May 11, 2026*

Added a cash-deploy rebalancing tool that identifies band-breach sleeves and sizes
purchase orders to restore each sleeve to within its SAA tolerance band.

- **20** — Buy-only rebalancing tool; band-breach detection; purchase order sizes
  derived from target weight and current portfolio value (aebd9f6)
- **20.1** — Fix underweight predicate to use band-breach semantics (actual drift
  exceeds tolerance band, not merely below target) (f9f016d)

## Phase 19 — DRIP visibility controls
*May 11, 2026*

Added toggle controls to show or hide DRIP lots on the Trade Log and Tax Lots pages,
reducing visual noise when reviewing deliberate trades.

- **19** — DRIP visibility toggle on Trade Log and Tax Lots (cbaa16e)
- **19.1** — Trade Log action case normalization; DRIP lots inherit the position
  thesis of the parent holding (0c4e6cc)

## Phase 18 — DRIP timing alignment
*May 10, 2026*

Corrected DRIP lot cost basis to use the payment-date closing price rather than the
ex-dividend date, matching the actual execution price of automatic reinvestment.

- **18** — DRIP timing aligned to payment-date close price (e36a7a9)

## Phase 17 — DRIP persistence
*May 10, 2026*

Moved DRIP dividend reinvestment lots from in-memory computation to persistent
SQLite storage, making the lot record durable across restarts and enabling
lot-level cost basis tracking.

- **17** — DRIP lots persisted to database; in-memory DRIP removed from holdings
  calculation and Brinson-Fachler attribution (0d68a58)

## Phase 16 — Tax-loss harvest recommendations
*May 10, 2026*

Added a harvest candidate identification section to the Tax Lots page, surfacing
positions eligible for tax-loss harvesting alongside 30-day wash-sale window
awareness.

- **16** — Harvest candidates panel: unrealized loss threshold, lot-level eligibility,
  wash-sale guard (a3b4086)

## Phase 15 — Tax lot inventory
*May 10, 2026*

Built a dedicated Tax Lots page with lot-level cost basis, holding period
classification (short-term vs. long-term), and per-lot realized and unrealized
gain summary.

- **15** — Tax lot inventory page: per-lot G/L, holding period, lot-level detail
  (0aa2a82)
- **15.1** — Sleeve filter; harvest pool panel; build hash gate to prevent duplicate
  lot seeding across app restarts (c4b7802)

## Phase 14 — PDF polish and README rewrite
*May 10, 2026*

Fixed PDF rendering edge cases (orphan tails at page boundaries, bullet glyph
incompatibility across platforms) and rewrote the README for a public
recruiter-facing audience.

- **14** — Orphan tail fix; bullet glyph guards for cross-platform PDF rendering;
  README rewrite (2788a4d)

## Phase 13 — Disclaimer hardening and README launch polish
*May 7–10, 2026*

Single-sourced the quarterly report legal disclaimer through a module-level constant
so all PDF paths render identical text; tightened PDF CSS to keep the disclaimer on
its own final page; rewrote the README for the public GitHub launch.

- README rewrite — archived prior README; rebuilt for recruiter audience with project
  framing, methodology summary, and phase narrative (39a8d07)
- **13** — Legal disclaimer single-sourced via `REPORT_DISCLAIMER` in `src/reports.py`
  (eecf982)
- **13.1** — Tighten disclosure CSS to land the five-sentence disclaimer on the final
  page (d3ab9a3)
- **13.2** — Shrink methodology font size to recover page space for the disclaimer
  (0033a55)

## Phase 12 — Integrity test suite and PDF completion
*May 7–9, 2026*

Built a three-layer integrity test suite (math identities, reasonability bounds,
prose-vs-data guards) and wired continuous integration via GitHub Actions. Completed
the quarterly PDF report with an Asset Evaluation section and templated prose derived
from live database values.

- **12 Sections 0–5** — Layer 1 identity tests (BF effects sum to active return,
  sleeve weights sum to 100%, TWR equals absolute return for the lump-sum case);
  Layer 2 reasonability bounds (Sortino ≥ Sharpe, VaR/CVaR within expected range,
  IR × TE within Jensen's gap); Layer 3 prose-vs-data guards; pytest config; GitHub
  Actions CI workflow (4cef3dd → b0ca484)
- **12.1** — Extended prose inventory; templated FI weight captions, parent weights,
  Real Assets benchmark, and drift thresholds from DB rather than hardcoded strings
  (7bba4ee → bf9fdd6)
- **12.2** — Diagnosed CI failure in `config.py` secrets handling; templated drift
  threshold from tolerance band constant; documented CI setup in `docs/ci_setup.md`
  (48efa3f → f751fb4)
- **12 Items 1–9, closeout** — PDF fixes: style box caption unit (z-score →
  fractional deviation), FI scenario trigger, CAPE implied return added, Asset
  Evaluation section in PDF, page count reduced by 3, BTC conclusion single-sourced;
  page layout tightened to reclaim the final page (01c48a8 → 0e16844)
- **8j.1 addendum** — Late Phase 8 fix committed during Phase 12 window: multi-series
  chart top margin, PDF Benchmark Attribution chart height, style box caption width
  (186f61a, May 8)

## Phase 11 — Data integrity diagnostic and prose template refactors
*May 7, 2026*

Diagnosed a series of data integrity issues against live market data and fixed the
root causes; replaced static percentile text and factor commentary with dynamic
templates derived from live computation.

- **11 Sections 0–4** — Push-and-verify wrapper; integrity diagnostic; reconciliation
  base fix (use adj_close series start, not Jan 1); Dev sleeve federal-holiday
  exclusion from regression calendar; IEMG cache verification; dynamic CAPE percentile
  prose; IR methodology disclosure; factor publication lag computed dynamically
  (833d533 → 7ea1d6c)
- **11 follow-ups** — Filter calendar-day zeros from risk metric computation; remove
  duplicate EM sleeve label; correct IR prose direction and exponent (85b3aeb →
  d098ffd)

## Phase 10 — Attribution math fixes and prose consolidation
*May 6, 2026*

Fixed two Brinson-Fachler attribution bugs affecting return alignment; audited
and consolidated static prose strings into reusable helpers, reducing the surface
area for stale text.

- **10 Sections 0–4** — Prose audit classification; static-stale-risk prose refactors;
  `prose_helpers.py` with significance and percentile label consolidation;
  prose-vs-table consistency tests (0f0cc3c → 481accb)
- **10.1** — Fix two-stage attribution to use price-series portfolio return, not
  beginning-of-period weights (29789f9)
- **10.2** — Align BF sleeve returns to total return for all reporting windows
  (bb477ac)

## Phase 9 — Two-stage Brinson attribution
*May 6, 2026*

Decomposed Brinson-Fachler active return into a SAA design effect (systematic tilts
from policy weights) and an implementation effect (holding vs. benchmark within each
sleeve), enabling more precise attribution of active return sources.

- **9** — Two-stage BHB decomposition; naive benchmark toggle; design effect and
  implementation effect calculations (4ca2924)

## Phase 8 — Comprehensive analytics build-out
*May 3–6, 2026 (with late addendum May 8)*

A major multi-sub-phase build-out spanning per-sleeve Fama-French 5-factor
regressions, equity style box, benchmark attribution regression, risk metrics
(VaR/CVaR), ECY panel, Endowment comparison, Active Positioning page, rolling sleeve
correlations, regime classifier, and the Bitcoin Asset Evaluation case study.
Deployed to Streamlit Cloud and resolved cloud-specific rendering and caching issues.

- **8a** — Portfolio rebase; Active Positioning page with duration and scenario
  analysis (21ac087)
- **8b** — Equity style box: 4-dot cell, label density controls, cover TWR fix
  (0a09f48)
- **8c** — Fama-French 5-factor regression with Newey-West HAC standard errors;
  per-sleeve regional factor universes (US and Developed ex-US) (a20471d, 54923de)
- **8e** — Continuous-coordinate style box with fundamentals-driven placement using
  four valuation metrics normalized to SPY (ea28cbb)
- **8f/8g** — Benchmark attribution regression:
  R_p − RF ~ (R_b − RF) + HML + SMB + RMW; prose and significance labels (3a2dd2f)
- **8h** — Compact PDF layout targeting 9–11 pages (8de3653)
- **8i** — Quarter-start date fix: prior-quarter-end used as base price, not Jan 1;
  propagated fix to cover page, blended series, and BHB prose (9bda473)
- **8j** — Pre-interview audit (18 polish items across A/B/C priority buckets);
  quarter-snapshot price lock for deterministic PDF generation (62dec42)
- **8k** — VaR(95%) and CVaR(95%) on Performance page; ECY (Excess CAPE Yield) panel
  on Macro; Endowment comparison panel on SAA; five UI polish items; CAPE freshness
  warning (8k commits, May 5)
- **8l/8m/8n** — Factor model enhancements (FI TERM/CREDIT, Carhart momentum, Global
  factors, confidence intervals); per-panel error states; FRED and Ken French fetch
  retry with exponential backoff; pre-bundled factor cache for Streamlit Cloud cold
  start; landing page and demo banner standardization (8l–8n commits, May 5)
- **8o** — AppTest render pilot (221 tests); deployed SHA footer on every page
  (4496534)
- **8p** — Fixed flat portfolio value bug on Streamlit Cloud caused by duplicate
  price-date index and stale `@st.cache_data` serving a pre-fix $30 value (dfbbfb7)
- **8q–8u** — Global Factors discontinued disclosure; Performance reconciliation note;
  risk metrics layout; MissionSquare reference; risk-adjusted metrics extended to
  five-window selector; window-collapse bug fix (c33e522 → 3b27668)
- **Asset Evaluation** — Bitcoin case study page: univariate statistics, full-sample
  and rolling correlations, regime-conditional correlation by NBER cycle phase,
  unconstrained and constrained MV contribution, drawdown sensitivity table, decision
  framework (7851fe1)
- **Macro enhancements** — HY OAS continuity fix; window-anchored percentiles; rolling
  sleeve correlation matrix page; regime classifier panel (f9e1495, 7f0d8fb)
- **Demo write protection** — Hidden trade form, hidden Macro force refresh, and guard
  tests in demo mode (cea8014)

## Phase 7 — Public-facing polish and deployment prep
*May 3, 2026*

Rewrote the README for a recruiter and hiring-manager audience; fixed chart axis
labels across Performance, Holdings, and Correlations pages; wired Streamlit Cloud
deployment configuration.

- **7** — README rewrite for public-facing audience; chart axis fixes (Holdings
  Y-labels, Cumulative Return Y-ticks, drift chart); home page banner; SECURITY.md;
  `.env.example`; LinkedIn project entry drafts (b7d774e)
- Streamlit Cloud deployment — `requirements.txt` modernized to `>=` pins; WeasyPrint
  system deps in `packages.txt`; secrets template; `demo.db` committed to repo
  (3ccdfa7)

## Phase 6 — Quarterly PDF report
*May 3, 2026*

Implemented quarterly PDF report generation with a WeasyPrint (Linux/Cloud) and
xhtml2pdf (Windows) dual-backend approach; 8-section Jinja2 template covering cover,
executive summary, holdings, performance, attribution, macro, theses, and methodology.

- **6** — `src/reports.py` PDF generator; Jinja2 HTML + CSS template; Plotly chart
  rendering via kaleido with 25-second daemon timeout; "Generate Quarterly Report"
  button with period selector and download (0a14883)
- **6 polish** — 14 layout, logic, and content fixes for cloud PDF rendering: cover
  date, macro fallback, thesis cleanup, visual formatting (3f68b19 → e12ff59)

## Phase 5 — Macro dashboard
*May 2, 2026*

Built the macro dashboard integrating FRED data (yield curve, Fed Funds, HY OAS,
NBER recession indicator) and Shiller CAPE from Yale, with historical percentile
context, NBER recession shading, and a rules-based regime classifier.

- **5** — FRED integration with 24-hour SQLite cache; Shiller CAPE (Yale dataset,
  local CSV fallback); five-panel dashboard; CAPE implied 10-year real return formula;
  NBER recession shading; force-refresh button (630a482)

## Phase 4 — Performance tracking and attribution
*May 1, 2026*

Implemented daily-linked TWR and Modified Dietz return calculation; built
Brinson-Fachler per-sleeve attribution; seeded paper trades ($10k across 10 ETFs)
and wired the Performance page with headline metrics, cumulative return chart,
and attribution breakdown.

- **4 Session 1** — Yahoo Finance fetcher with SQLite price cache; paper trade seed
  on 2025-05-01 using floor(target / price) whole shares; `src/holdings.py`
  (54c15cb)
- **4 Session 2** — Daily-linked TWR and Modified Dietz (`src/returns.py`);
  Brinson-Fachler attribution (`src/attribution.py`); custom SAA-blended benchmark
  (`src/benchmarks.py`); Performance page with cumulative chart, BF attribution
  table, and drift analysis (e4953e4)
- **4 polish** — SPAXX via BIL proxy for weights consistency; DBC commodity
  benchmark; color and framing polish (96e8702)

## Phase 3 — Thesis and trade log
*May 1, 2026*

Built a two-tier thesis system linking investment theses (strategic views per SAA
sleeve) to position theses (per-holding rationale), with theme tags, lifecycle states
(active / closed / invalidated), and a trade entry form with dynamic thesis filtering.

- **3 schema** — `theses` table extended with level, parent_thesis_id, target_sleeves,
  invalidation conditions, expected return scenario, and post-mortem fields; `themes`
  join table; 12 investment theses and 11 position theses pre-seeded; 5 starter themes
  (f2541cd)
- **3 UI** — Trade log with dynamic ticker-to-thesis filtering; investment thesis
  browser with theme pills and status badges; theme aggregation view (b9a57ef)
- **3 polish** — Thesis sort and title display; tax efficiency theme tags; button
  color; status column cleanup (2e6cf16)

## Phase 2 — ETF research and securities seeding
*April 30–May 1, 2026*

Locked ETF picks for all 10 SAA sleeves with written rationale documenting the
benchmark-vs-holding distinction; seeded the securities table with holdings and
benchmarks; built the Research page with blended ER header and per-holding rationale
expanders.

- **2** — 10 holding picks (VOO, SPHQ, VTV, AVUV, VEA, IEMG, VGIT, SCHP, VNQ,
  PDBC) with documented rationale; `securities` table seeded; Research page built
  (a1f87f8)
- **2 polish** — Growth → Equity parent category rename (institutional taxonomy);
  sort order fix; benchmark ER display; blended weighted-average ER metric; layout
  consistency (1f5187a → 91f96ac)

## Phases 0–1 — Project foundation and SAA framework
*April 29, 2026*

Initial commit establishing the project architecture (Streamlit multi-page app,
SQLite schema, dual-mode personal/demo design via `TRACKER_MODE` env var); locked
the 10-sleeve strategic asset allocation with target weights, tolerance bands, and
per-sleeve investment rationale.

- **0–1** — Project scaffolding; `src/config.py` dual-mode foundation; SQLite schema
  (`accounts`, `asset_classes`, `securities`, `theses`, `trades`, `prices`);
  10-sleeve SAA seeded (Equity 72% / Income 15% / Real Assets 10% / Cash 3%);
  SAA page with allocation chart and rationale expanders (1ee9da3)
