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


def a_or_an(number_text: str) -> str:
    """The article for a phrase that starts with a number: "an 8.7-year", "an 11-day",
    "a 7-year". By sound, so 8, 11, 18 and 80–89 take "an"."""
    digits = number_text.lstrip("+-").split(".")[0].replace(",", "")
    if not digits.isdigit():
        return "a"
    if digits.startswith("8") or digits in ("11", "18") or (
            len(digits) in (5, 6) and digits[:2] in ("11", "18")):
        return "an"
    return "a"


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
    s = (f"US equity valuations are {percentile_label(pct)}: CAPE is {value:.1f} as of "
         f"{as_of}, the {ordinal(pct)} percentile of the Shiller record")
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


def _list(items: "list[str]") -> str:
    """"a", "a and b", "a, b, and c": the site's serial comma."""
    if len(items) <= 2:
        return " and ".join(items)
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def blend_split_sentence(legs) -> str:
    """A sleeve's holdings against its blended benchmark, stated as fact and without a
    reason (audit item 15c): the split bought at inception, the split held today, the
    blend, and the largest overweight, which attribution reports as selection.
    ``legs`` is holdings.blend_split's list."""
    bought = _list([f"{l.holding} {l.inception_weight:.0%}" for l in legs])
    held = _list([f"{l.holding} {l.current_weight:.0%}" for l in legs])
    blend = _list([f"{l.leg} {l.leg_weight:.0%}" for l in legs])
    over = max(legs, key=lambda l: l.inception_weight - l.leg_weight)
    at_start = round((over.inception_weight - over.leg_weight) * 100)
    today = round((over.current_weight - over.leg_weight) * 100)
    text = (f"The sleeve was bought {bought} at inception and holds {held} today, against "
            f"a benchmark of {blend}.")
    if at_start == 0:
        return text + " The inception split matches the benchmark's."
    against = (f"its {over.leg} leg" if over.leg != over.holding
               else f"the benchmark's {over.leg} weight")
    now = (f"{today} today" if today > 0 else "none today" if today == 0
           else f"an underweight of {-today} today")
    return (text + f" That is a {at_start}-point overweight in {over.holding} against "
            f"{against} ({now}), which attribution reports as selection.")


def cape_record_span(series) -> "tuple[int, int]":
    """(first year, years spanned) of the committed CAPE series: 1871 and 155 as of
    September 2026. The Macro page said "since 1881" and "145-year" (#401)."""
    s = series.dropna()
    first = int(s.index[0].year)
    return first, int(s.index[-1].year) - first


def cape_forty_sentence(series, current: float) -> str:
    """Which earlier years CAPE reached 40 in, derived from the series (#401), with a
    trailing space; "" when it never did. The Macro page said "Only the dot-com bubble
    peak (1999–2001) has sustained CAPE above 40", where the series has no 2001 month at
    40 and, from May 2026, a current run at 40 too."""
    from src.shiller import earlier_years_at_or_above
    earlier = earlier_years_at_or_above(series.dropna(), 40.0)
    if not earlier:
        return ""
    _, years = cape_record_span(series)
    if current >= 40:
        return (f"Before the current run, CAPE reached 40 only in {year_ranges(earlier)} "
                f"in the full {years}-year Shiller record. ")
    return (f"CAPE has reached 40 only in {year_ranges(earlier)} in the full "
            f"{years}-year Shiller record. ")

