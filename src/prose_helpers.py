"""Shared qualitative-label helpers for interpretive prose across pages.

Centralizes threshold logic so that changes to label tiers propagate
consistently to all panels that use them.
"""


# ── Labels: a classification of a computed figure, stated in words ────────────

# Every family of label the quarterly report states in running text, and the classes
# each can state. A class is what the label SAYS about a figure (the level a t-statistic
# reaches, which side of its benchmark a duration falls); the words that say it are
# passed beside it and are free to change. A locked quarter's labels are held to the
# committed golden by class (#525, tests/locked_figures.py): moving a cut-off moves a
# class and fails it, rewording a label does not.
LABEL_CLASSES = {
    # The strictest level a t-statistic reaches (SIGNIFICANCE_CUT_OFFS below).
    "significance": ("1%", "5%", "10%", "none"),
    # The sign of a loading or an alpha.
    "sign": ("positive", "negative", "zero"),
    # Whether a return beat or trailed the one it is set against, where the sentence
    # prints the gap as a magnitude; or, when that gap rounds to 0 bps, neither (#426).
    "versus_benchmark": ("out", "under", "in_line"),
    # The CAPE percentile's band (reports._cape_regime_class).
    "cape_regime": ("elevated", "above_average", "moderate", "below_average"),
    # The FI sleeve's duration against the benchmark's (reports._build_positioning_section).
    "duration_vs_benchmark": ("in_line", "below", "above"),
    # The sleeve's weight against its benchmark weight, in the top allocation effect.
    "allocation_weight": ("overweight", "underweight"),
    # What each factor sentence reads from its loading (src/factors.py).
    "us_market_reading": ("near_full", "not_near_full", "not_shown"),
    "tilt_reading": ("shown", "contrary", "not_shown"),
    "term_reading": ("duration", "contrary", "not_shown"),
    "fi_alpha_reading": ("unexplained", "not_from_zero"),
    # The portfolio's loading on its own benchmark, and what a significant intercept
    # is read as.
    "benchmark_beta": ("near_full", "departing"),
    "active_return_reading": ("selection",),
}

# Set only by tests/locked_figures.py, while it renders a report to read its labels.
_label_reader = None


def label(family: str, cls: str, words: str) -> str:
    """``words``, declared as stating class ``cls`` of the label family ``family``.

    Returns ``words``. Every word, clause or sentence the report chooses by testing a
    computed figure goes through here, so the class it states can be read off a
    locked quarter's render whatever the words are. An undeclared family or class
    raises: the classes are the list above, and a new one is added there."""
    if cls not in LABEL_CLASSES[family]:
        raise ValueError(f"{cls!r} is not a class of the label family {family!r}: "
                         f"{LABEL_CLASSES[family]}")
    return words if _label_reader is None else _label_reader(family, cls, words)


# Two-tailed critical values for |t|, strictest first. They match the thresholds in
# the Factor Profile methodology disclosure.
SIGNIFICANCE_CUT_OFFS = (("1%", 2.58), ("5%", 1.96), ("10%", 1.65))
SIGNIFICANCE_WORDS = {
    "1%": "statistically significant at the 1% level",
    "5%": "statistically significant at the 5% level",
    "10%": "marginally significant (10% level)",
    "none": "not statistically distinguishable from zero",
}


def significance_level(t_stat: float) -> str:
    """The strictest level a t-statistic reaches: "1%", "5%", "10%", or "none"."""
    t_abs = abs(t_stat)
    for level, cut_off in SIGNIFICANCE_CUT_OFFS:
        if t_abs >= cut_off:
            return level
    return "none"


def significance_label(t_stat: float) -> str:
    """Return a qualitative significance description based on a t-statistic: the words
    for the level it reaches (significance_level)."""
    level = significance_level(t_stat)
    return label("significance", level, SIGNIFICANCE_WORDS[level])


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
                            earlier_years: "list[int]", read: str = "") -> str:
    """The SAA thesis's valuation sentence, from the CAPE reading and its history.

    ``earlier_years`` is shiller.earlier_years_at_or_above(series, value). A level
    reached before in more than three separate stretches is not rare enough to name.
    ``read`` is shiller.read_clause for the reading: when and where it was read (#478
    I06), stated beside the month it describes.
    """
    s = (f"US equity valuations are {percentile_label(pct)}: CAPE is {value:.1f} as of "
         f"{as_of}{f' ({read})' if read else ''}, the {ordinal(pct)} percentile of the "
         f"Shiller record")
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

