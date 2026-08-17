# Macro Regime Classifier

## Purpose

Labels the current macro environment using four canonical phases from institutional asset
allocation — **Recession**, **Early-cycle**, **Mid-cycle**, **Late-cycle** — **or declines
to label it** when too few signals are available. The label is not guaranteed: see
[When there is no verdict](#when-there-is-no-verdict); the classifier returns a
`RegimeVerdict`, not a bare string.

This label is a heuristic signal, not a forecast. It summarizes the current position in the
business cycle to help contextualize portfolio positioning decisions — e.g., whether factor
tilts (quality, value) are appropriate for the current regime, or whether the bond sleeve's
duration is well-sized. It does not trigger automatic rebalancing.

---

## Signals Used

| Signal | FRED Series | Frequency | Role |
|---|---|---|---|
| NBER Recession Indicator | USREC | Monthly | Primary recession flag |
| Yield Curve (10Y–2Y) | T10Y2Y | Daily | Curve shape / tightening signal |
| Unemployment Rate | UNRATE | Monthly | Labor market tightness |

### Why these three?

- **USREC** is the authoritative NBER recession indicator. It is lagged (NBER announces
  recessions after the fact), but it eliminates false positives from noisy leading indicators.
- **T10Y2Y** captures the curve's slope. Inversions (< 0) have preceded every US recession
  since 1955 with a 6–24 month lag. Steepness also signals early-cycle recoveries.
- **UNRATE** distinguishes early-cycle (labor healing) from late-cycle (labor tight, no slack).

---

## Classification Rules

Rules are applied in priority order. The first matching rule wins.

```
1. Recession:    USREC = 1
                 → label = "Recession"

2. Early-cycle:  USREC = 0
                 AND UNRATE > 5.5%   (labor market not yet recovered)
                 AND T10Y2Y > -0.25  (curve not freshly inverted)
                 → label = "Early-cycle"

3. Late-cycle:   USREC = 0
                 AND T10Y2Y < -0.25  (curve inverted — classic late-cycle signal)
                 OR UNRATE < 4.2%    (labor very tight, implying late expansion)
                 → label = "Late-cycle"

4. Mid-cycle:    Default (USREC = 0, curve normal, labor moderate)
                 → label = "Mid-cycle"
```

## When there is no verdict

The classifier returns `label=None` when the signals present cannot support a
classification. Missing signals are treated as **neutral within a rule** — a defensible
modelling choice for one absent input among three — but **neutrality has a floor**: with
too few signals present, "neutral" stops being a modest assumption and becomes the entire
basis of the answer. `Mid-cycle` is the default branch, so without that floor an empty
argument list returns a confident mid-cycle verdict.

Sufficiency is **per-branch**, because the branches differ in kind. **Recession** reads
`USREC` alone and one signal is *complete*: NBER's indicator is definitionally the answer,
not evidence toward it. **Early-cycle**, **Late-cycle** and **Mid-cycle** are heuristic
combinations and require at least two of three — in particular
`curve_ok = t10y2y is None or t10y2y > -0.25` means an absent curve actively supplies half
of the Early-cycle test, so a lone unemployment reading would otherwise decide it. A
missing signal there *votes* rather than abstains, which is why a flat "at least one
present" floor does not catch it.

The verdict carries `present` and `missing` so a caller can state what the label rests on.
Consumers must handle `label=None`; it is not an error state and must not be rendered as a
regime.

### What this changed, and how narrowly

Of the thirteen signal combinations pinned in `tests/test_macro.py`, exactly **one**
changed behaviour: `(None, None, None)`, which returned `"Mid-cycle"` and now returns no
verdict. The fully-populated case — every historical backtest point and the normal render
— is untouched.

The measured render before the floor: with `macro_cache` emptied and the network blocked,
`pages/3_Macro.py` displayed a full-colour **"Current Regime: Mid-cycle"** badge with
interpretive prose while all three inputs were unavailable.

### Thresholds (rationale)

- **UNRATE > 5.5%** for Early-cycle: post-WWII average unemployment is ~5.7%; values above
  this level during an expansion typically signal the economy is still in recovery mode.
- **T10Y2Y < -0.25** for Late-cycle: minor inversion noise is filtered; a spread below -0.25
  represents a meaningful signal. The 0.25 tolerance matches common practitioner usage.
- **UNRATE < 4.2%** as an additional Late-cycle trigger: Fed's long-run neutral NAIRU estimate
  (as of 2024) is ~4.1–4.2%. Below this level, labor markets are "tight" by Fed standards.

### Known limitations

- USREC is declared retroactively (can lag by 6–18 months). During an unannounced recession,
  the classifier will not immediately fire "Recession."
- The T10Y2Y signal has a known lead time of 6–24 months before actual recession — an
  inversion flags Late-cycle risk, not imminent contraction.
- Rule 3 uses OR logic on T10Y2Y / UNRATE, which can produce Late-cycle during brief inversions
  even if other signals are benign.
- All signals are US-centric; this classifier does not apply to international sleeves.

---

## Output Labels and Portfolio Implications

| Label | Typical characteristics | Portfolio implications |
|---|---|---|
| Recession | Contracting output, rising unemployment | Duration helpful; quality factor tends to outperform; reduce cyclical exposure |
| Early-cycle | Recovery, high unemployment, steepening curve | Small-cap, value historically strongest; can add risk |
| Mid-cycle | Moderate growth, stable labor, normal curve | SAA weights appropriate; no tactical tilt warranted |
| Late-cycle | Tight labor, flat/inverted curve, credit stress | Quality, TIPS, and reduced duration; watch spreads |

---

## Disclaimer

This classifier is a rules-based heuristic built on three publicly available FRED series.
It is not a quantitative model, not a trading signal, and not investment advice.
Regime labels are backward-looking — they reflect observable data as of the last available
reading, not real-time conditions. The NBER recession indicator in particular is declared
retroactively. Use this panel as context for understanding portfolio positioning, not as
a basis for market-timing decisions.
