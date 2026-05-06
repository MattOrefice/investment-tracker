# Phase 10 Prose Audit

Full classification of all interpretive prose across the eight page files. Each paragraph is labeled:

- **Dynamic** — already pulls values from live data; no refactoring needed
- **Static-OK** — historical facts, methodology explanations, academic consensus; will not rot
- **Static-stale-risk** — contains hard-coded numbers or event references that diverge from the data shown to the user; refactoring target for Sections 2–3

---

## pages/4_Performance.py

| Lines | Label | Content summary | Action |
|-------|-------|-----------------|--------|
| 269–274 | **Static-stale-risk** | `"28% of the portfolio is non-equity (Income + Real Assets + Cash), 27% is non-US equity"` — hard-coded SAA fractions that should be computed from `asset_classes` | Refactor: derive from DB |
| 287–296 | Dynamic | Reconciliation note — `_cost_basis`, `current_val`, `_unrealized`, TWR all computed live | No action |
| 748–760 | **Static-stale-risk** | Methodology paragraph cites `"VTV at 8%"`, `"AVUV at 7%"`, `"8% EM"`, `"10% real assets"`, `"6% TIPS"`, `"~72/28 equity-vs-other-assets risk posture"` — all SAA targets pulled from DB at runtime but not threaded into this prose | Refactor: derive from DB |
| 551–555 | Dynamic (post-S0) | Window caption — uses `_naive_label` (just made dynamic) | No action |
| 572–577 | Dynamic | Algebra reconciliation caption — fully computed | No action |
| 640–644 | Dynamic (post-S0) | Sleeve chart caption — uses `_naive_short` | No action |

---

## pages/5_Macro.py

### CAPE panel

| Lines | Label | Content summary | Action |
|-------|-------|-----------------|--------|
| 261 | Dynamic | `"CAPE in the {_ordinal(cape_pctile)} percentile historically"` — percentile computed live | No action |
| 261 end: `{pctile_label}` | **Static-stale-risk** | `pctile_label` is a string like `"elevated"` hardcoded from a percentile bracket; should come from a shared `percentile_label()` helper | Refactor: `src/prose_helpers.py` |
| 262–263 | **Static-stale-risk** | `"the 2024–2026 stretch is the second such instance"` — specific event window that will rot; also CAPE data ends Sep 2023 per backlog, so "2026" is already wrong | Refactor: remove date range, generalize claim |
| 264–267 | Static-OK | Interpretive framing: `"Periods of extreme valuation have preceded materially below-average decade-ahead returns"` — academic consensus, not data-dependent | No action |

### Yield Curve panel

| Lines | Label | Content summary | Action |
|-------|-------|-----------------|--------|
| 451–452 | Static-OK | `"inversions have preceded each of the last seven recessions with a 12–18 month lead time"` — established historical record; count correct at time of writing | Low priority; acceptable |
| 452–453 | **Static-stale-risk** | `"The 2022–2023 inversion"` — past event reference embedded in future-facing interpretation; will read strangely if another inversion occurs | Refactor: soften to `"the most recent inversion"` |
| 454 | Static-OK | `"Gray shading marks NBER-dated recessions"` — purely methodological | No action |

### Fed Funds panel

| Lines | Label | Content summary | Action |
|-------|-------|-----------------|--------|
| `_ff_interpretation()` L98 | Dynamic | Rate interpretation computed live from current FF and recent change | No action |

### HY OAS panel

| Lines | Label | Content summary | Action |
|-------|-------|-----------------|--------|
| 524 | Dynamic | `"{_ordinal(hy_pctile)} percentile since {hy_since}"` — computed live | No action |
| 561 | Dynamic | `"HY spreads at the {_ordinal(hy_pctile)} percentile {hy_framing}"` | No action |
| `hy_framing` | **Static-stale-risk** | `hy_framing` is a hardcoded string derived from percentile brackets (similar to `pctile_label` in CAPE); should use shared `percentile_label()` helper | Refactor: `src/prose_helpers.py` |

### US vs. International panel

| Lines | Label | Content summary | Action |
|-------|-------|-----------------|--------|
| 650–651 | Dynamic | `"US outperformance vs. international is at the {_ordinal(ratio_pctile)} percentile of its 20-year history"` — percentile computed live | No action |
| 651 `{us_label}` | **Static-stale-risk** | `us_label` is hardcoded from percentile brackets; should use shared `percentile_label()` helper | Refactor: `src/prose_helpers.py` |
| 654 | **Static-stale-risk** | `"sleeve's 19% weight"` — Intl Developed target weight, should be pulled from `asset_classes` | Refactor: derive from DB |

---

## pages/6_Positioning.py

| Lines | Label | Content summary | Action |
|-------|-------|-----------------|--------|
| 66–74 | **Static-stale-risk** | `"VGIT: 5.5 yrs, SCHP: 6.8 yrs per Vanguard/Schwab Q1 2026"` — ETF fact-sheet values with explicit quarter stamp; will rot every quarter | Refactor: add a `duration_years` column to `securities` table, or note as best-effort-static with a "as of" date in the UI |
| Duration formula/methodology | Static-OK | Dollar duration and DV01 math; doesn't change | No action |

---

## pages/7_Factor_Profile.py

| Lines | Label | Content summary | Action |
|-------|-------|-----------------|--------|
| Factor/alpha table (L92–107) | Dynamic | All values rendered from live regression results dict | No action |
| `alpha_ci_str()` output | Dynamic | CI bounds computed from regression SE | No action |
| L150–152 | Static-OK | `"A near-zero Mom loading is expected given the portfolio's tax-aware construction"` — structural explanation, not a data claim | No action |
| L152 | Static-OK | `"Alpha change vs. FF5 above reflects covariance between sleeve returns and momentum"` — methodological note | No action |
| L247–262 | Static-OK | Interpretation section — methodology explanation (CI width, HAC, significance thresholds) | No action |
| `significance_label` usage | **Static-stale-risk** | Various places construct phrases like `"statistically significant"` / `"not yet significant"` from inline threshold checks; this pattern is duplicated between US and FI regression blocks | Refactor: `src/prose_helpers.py` `significance_label(t_stat)` |

---

## pages/1_SAA.py, pages/2_Research.py, pages/3_Trade_Log.py, pages/8_Factor_Profile.py

No interpretive prose with hard-coded data citations found. All labels and values in these pages are either static UI labels (column headers, tab names) or fully dynamic (pulled from DB at render time).

---

## Refactoring targets summary (Sections 2–3)

### Section 2 targets (f-string template refactors)

| Priority | File | Item |
|----------|------|------|
| High | `pages/4_Performance.py` L269–271 | Replace `"28%... 27%..."` with DB-derived values |
| High | `pages/4_Performance.py` L748–751 | Replace `"VTV at 8%... AVUV at 7%..."` with DB-derived SAA targets |
| Medium | `pages/5_Macro.py` L262–263 | Remove `"2024–2026"` date range; generalize |
| Medium | `pages/5_Macro.py` L452–453 | Replace `"The 2022–2023 inversion"` with `"The most recent inversion"` |
| Medium | `pages/5_Macro.py` L654 | Replace `"19% weight"` with DB-derived Intl Developed target |
| Low | `pages/6_Positioning.py` L74 | Add `"as of Q1 2026"` clarification or pull from DB |

### Section 3 targets (`src/prose_helpers.py`)

| Function | Used by |
|----------|---------|
| `significance_label(t_stat: float) -> str` | `pages/7_Factor_Profile.py` — consolidate duplicated `"statistically significant"` / `"not yet significant"` logic |
| `percentile_label(pct: float) -> str` | `pages/5_Macro.py` — `pctile_label` (CAPE), `hy_framing` (HY OAS), `us_label` (US vs Intl) |

---

*Audit completed 2026-05-06 — Phase 10 Section 1.*
