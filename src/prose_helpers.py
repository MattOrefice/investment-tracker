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
