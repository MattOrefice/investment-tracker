"""Shared qualitative-label helpers for interpretive prose across pages.

Centralizes threshold logic so that changes to label tiers propagate
consistently to all panels that use them.
"""


def significance_label(t_stat: float) -> str:
    """Return a qualitative significance description based on a t-statistic.

    Uses two-tailed critical values: |t|>=2.58 (1%), |t|>=1.96 (5%),
    |t|>=1.65 (10%). Matches the thresholds in the Factor Profile
    methodology disclosure.
    """
    t_abs = abs(t_stat)
    if t_abs >= 2.58:
        return "statistically significant at the 1% level"
    if t_abs >= 1.96:
        return "statistically significant at the 5% level"
    if t_abs >= 1.65:
        return "marginally significant (10% level)"
    return "not statistically distinguishable from zero"


def year_ranges(years: "list[int]") -> str:
    """[1999, 2000] -> "1999–2000"; [1929, 1999, 2000] -> "1929 and 1999–2000"."""
    spans: list[list[int]] = []
    for y in sorted(set(years)):
        if spans and y == spans[-1][1] + 1:
            spans[-1][1] = y
        else:
            spans.append([y, y])
    parts = [f"{a}" if a == b else f"{a}–{b}" for a, b in spans]
    if len(parts) <= 2:
        return " and ".join(parts)
    return ", ".join(parts[:-1]) + f", and {parts[-1]}"


def ordinal(n: float) -> str:
    """99.1 -> "99th"; 1 -> "1st"; 12 -> "12th"."""
    n = int(round(n))
    if 11 <= (n % 100) <= 13:
        return f"{n}th"
    return f"{n}{['th', 'st', 'nd', 'rd', 'th'][min(n % 10, 4)]}"


def cape_valuation_sentence(value: float, as_of: str, pct: float,
                            earlier_years: "list[int]") -> str:
    """The SAA thesis's valuation sentence, from the CAPE reading and its history.

    ``earlier_years`` is shiller.earlier_years_at_or_above(series, value). A level
    reached before in more than three separate stretches is not rare enough to name.
    """
    s = (f"Strategic asset allocation reflects US equity valuations that are "
         f"{percentile_label(pct)}: CAPE {value:.1f} as of {as_of}, the "
         f"{ordinal(pct)} percentile of the Shiller record")
    if not earlier_years:
        return s + ", above every earlier reading."
    ys = sorted(set(earlier_years))
    if 1 + sum(1 for a, b in zip(ys, ys[1:]) if b != a + 1) > 3:
        return s + "."
    return s + f", a level reached before only in {year_ranges(ys)}."


def percentile_label(pct: float) -> str:
    """Return a qualitative label for a historical percentile position.

    Direction-agnostic: the caller is responsible for interpreting whether
    a high or low percentile represents the 'concerning' direction for a
    given indicator (e.g. high HY-spread percentile = stress, low = tight).
    """
    if pct > 90:
        return "historically extreme"
    if pct > 75:
        return "very high historically"
    if pct > 55:
        return "elevated historically"
    if pct > 40:
        return "near the historical median"
    if pct > 25:
        return "below the historical median"
    return "historically low"
