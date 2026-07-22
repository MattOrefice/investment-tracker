"""Forward P/E: live S&P 500 price over a manually-maintained forward EPS.

The numerator is live (^GSPC via the existing price pipeline). The
denominator — next-12-month consensus EPS — has NO free, stable,
machine-readable source: FRED carries no forward-estimate series, the retail
quote APIs expose no forward fields for the index, and S&P DJI's official
estimates workbook (sp-500-eps-est.xlsx) is bot-blocked to automated fetch. So
the estimate lives in data/forward_eps.json, entered manually from the workbook
WITH its own as-of date, and this module enforces the honesty rules around
that seam:

  - fresh (<= STALE_WARN_DAYS):        display normally
  - aging (<= STALE_SUPPRESS_DAYS):    display with a staleness warning
  - stale (> STALE_SUPPRESS_DAYS):     SUPPRESS the figure — a forward P/E
    computed from a months-old estimate is a fabricated-current number
    wearing a real one's clothes, the same principle as refusing to invent
    the estimate outright.

A malformed file raises ValueError (fail loud); an absent file or the null
template returns None (the page renders how-to-populate instructions).
"""
from datetime import date, datetime
from pathlib import Path
import json

_ROOT     = Path(__file__).resolve().parent.parent
_EPS_JSON = _ROOT / "data" / "forward_eps.json"

STALE_WARN_DAYS     = 45
STALE_SUPPRESS_DAYS = 90

# The index whose price forms the numerator. The EPS estimate in the workbook
# is stated in index dollars, so the price must be the index level, not SPY.
PRICE_TICKER = "^GSPC"


def load_forward_eps(path: Path | None = None) -> dict | None:
    """Validated {'eps': float, 'as_of': date, 'source': str}, or None.

    None means "no estimate entered" (missing file, or the committed null
    template) — a legitimate state the page explains. A file that ATTEMPTS to
    carry an estimate but fails validation raises ValueError instead of
    degrading to None: a typo'd estimate silently vanishing would look
    identical to never having entered one.
    """
    p = path or _EPS_JSON
    if not p.exists():
        return None
    raw = json.loads(p.read_text(encoding="utf-8"))

    eps   = raw.get("forward_eps")
    as_of = raw.get("as_of")
    if eps is None and as_of is None:
        return None  # committed template state

    if eps is None or as_of is None:
        raise ValueError(
            "forward_eps and as_of must be set together "
            f"(got forward_eps={eps!r}, as_of={as_of!r})"
        )
    try:
        eps_f = float(eps)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"forward_eps is not a number: {eps!r}") from exc
    if not eps_f > 0:
        raise ValueError(f"forward_eps must be positive, got {eps_f}")
    if not 10.0 <= eps_f <= 10_000.0:
        raise ValueError(
            f"forward_eps={eps_f} is outside any plausible index-dollar range "
            "— the estimate must be S&P 500 index EPS (e.g. 305.0), not a "
            "per-share figure for a fund"
        )
    try:
        as_of_d = datetime.strptime(str(as_of), "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"as_of must be YYYY-MM-DD, got {as_of!r}") from exc
    if as_of_d > date.today():
        raise ValueError(f"as_of {as_of_d} is in the future — typo?")

    return {
        "eps":    eps_f,
        "as_of":  as_of_d,
        "source": str(raw.get("source") or "unspecified source"),
    }


def staleness_days(eps_info: dict, today: date | None = None) -> int:
    """Age of the estimate in days."""
    return ((today or date.today()) - eps_info["as_of"]).days


def forward_pe_state(eps_info: dict, today: date | None = None) -> str:
    """'ok' | 'stale_warn' | 'suppressed' for a validated estimate."""
    days = staleness_days(eps_info, today)
    if days > STALE_SUPPRESS_DAYS:
        return "suppressed"
    if days > STALE_WARN_DAYS:
        return "stale_warn"
    return "ok"


def compute_forward_pe(price: float, eps_info: dict) -> float:
    """price / forward EPS — the division the page renders explicitly."""
    return price / eps_info["eps"]
