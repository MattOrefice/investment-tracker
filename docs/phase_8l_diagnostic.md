# Phase 8l Diagnostic — Deploy Reconciliation

**Date:** 2026-05-05  
**Branch:** main  
**Local HEAD:** 38eac9f — Phase 8k Section 2: five UI polish items (2A-2E)  
**origin/main HEAD:** 38eac9f (identical — branch is up to date with remote)

---

## Section 1.1 — Git Log Analysis

### Local commit history (last 5 relevant)

```
38eac9f Phase 8k Section 2: five UI polish items (2A-2E)        ← most recent commit
8b24f25 Phase 8k: fix stale CAPE — multpl.com primary source, freshness warning, Force refresh disk clear
8ce05d7 Phase 8j.1: quarter-snapshot price lock for deterministic PDF generation
7d9367c Phase 8j.1: Fix methodology disclosure regression from B2
3b10f08 Phase 8j: pre-interview audit — 18 polish items across A/B/C buckets
```

### Remote origin/main

Identical to local. No divergence. `git status` reports: "Your branch is up to date with 'origin/main'."

### Phase 8k subsection coverage in commit history

| Subsection | Description | In commit history? |
|---|---|---|
| 2A-2E | Five UI polish items | YES — commit 38eac9f |
| 2F | PDBC badge on Research | **NO** |
| 3A | FI Factor Model (TERM/CREDIT) | **NO** |
| 3B | ECY panel on Macro page | **NO** |
| 3C | Endowment comparison on SAA | **NO** |
| 3D | Carhart MOM supplementary regression | **NO** |
| 3E | FF Global panel for Intl Developed | **NO** |
| 3F | VaR(95%) / CVaR(95%) on Performance | **NO** |
| 3G | 95% CI on all alpha estimates | **NO** |

---

## Section 1.2 — Code Presence Check

`git status` output at time of diagnostic shows all Phase 8k Section 3 work as **unstaged local modifications and untracked new files** — never staged, never committed.

### Per-subsection code presence

| Subsection | File(s) | State | Evidence |
|---|---|---|---|
| 2F | `pages/2_Research.py` | PRESENT LOCALLY, NOT ON ORIGIN | `grep` finds `"Most distinctive rationale"` at line 203 |
| 3A | `src/factors.py`, `pages/7_Factor_Profile.py` | PRESENT LOCALLY, NOT ON ORIGIN | `regress_fi_sleeve` at `src/factors.py:691`; imported at `pages/7_Factor_Profile.py:14` |
| 3B | `pages/5_Macro.py`, `src/macro.py` | PRESENT LOCALLY, NOT ON ORIGIN | `"Excess CAPE Yield"` panel found at `pages/5_Macro.py:288` |
| 3C | `pages/1_SAA.py`, `src/endowment_benchmarks.py` | PRESENT LOCALLY, NOT ON ORIGIN | import at `pages/1_SAA.py:7`; `src/endowment_benchmarks.py` exists as untracked file |
| 3D | `src/factors.py` | PRESENT LOCALLY, NOT ON ORIGIN | `_UMD_URL`, `_UMD_CACHE`, `_FF5_MOM_FACTORS` at lines 66-76; `_parse_momentum_csv_text` at line 304 |
| 3E | `pages/7_Factor_Profile.py` | PRESENT LOCALLY, NOT ON ORIGIN | `run_intl_global_regression` imported at line 15; used at line 61, 71, 127, 132 |
| 3F | `src/performance.py` | PRESENT LOCALLY, NOT ON ORIGIN | `var_95`, `cvar_95` computed at lines 78-92 |
| 3G | `pages/7_Factor_Profile.py`, `pages/8_Benchmark_Attribution.py` | PRESENT LOCALLY, NOT ON ORIGIN | `alpha_ci_str` imported and used in alpha rows of both files |

New untracked files (never committed):
- `src/endowment_benchmarks.py`
- `tests/test_endowment.py`
- `tests/test_performance.py`

---

## Diagnostic Conclusion

**Branch B/C hybrid.** The Phase 8k Section 3 and 2F work was executed — code was written to local files and tests were run against those local modifications (yielding a true 205-test pass count against the local working tree). However, the changes were **never staged or committed**. No `git add` + `git commit` sequence was issued after the code was written.

**How 205 tests passed without the rendered work reaching the live demo:** The test runner executed against the local working tree, which contained all the new code in unstaged modified files. `pytest` does not require a clean working tree — it imports from the local filesystem. The tests were therefore testing real, correct implementations. The failure was not in the code or the tests; it was in the release step: the commit was never created, so origin/main and the Streamlit Cloud deployment remained at commit `38eac9f` (Phase 8k Section 2 only).

**Remediation:** Commit each subsection individually per the Phase 8l spec, push to origin/main, and verify rendered output per Section 4 protocol before declaring Phase 8l closed.

---

## Action Plan

1. Commit this diagnostic → push → confirm on origin/main
2. Commit 2F (PDBC badge) → push → verify Research page
3. Commit 3B (ECY panel) → push → verify Macro page  
4. Commit 3C (Endowment comparison + new file) → push → verify SAA page
5. Commit 3A (FI Factor Model + new test file) → push → verify Factor Profile
6. Commit 3D (Carhart MOM) → push → verify Factor Profile supplementary table
7. Commit 3E (FF Global) → push → verify Factor Profile International Developed
8. Commit 3F (VaR/CVaR + new test file) → push → verify Performance page
9. Commit 3G (CI on alpha estimates) → push → verify Factor Profile + Benchmark Attribution
10. Commit Section 3 polish items (3.1, 3.2, 3.3)
