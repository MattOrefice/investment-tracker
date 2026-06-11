"""Display anchor C (last settled trading day) for period-return surfaces.

The Performance page clips every displayed period series (portfolio value +
benchmarks) to the last SETTLED trading day, so a live mid-session partial
intraday bar is never used as a period-window endpoint — which would swing the
displayed 1M/3M returns by the half-day move (~1:1) and make the surfaces
mutually inconsistent. C always excludes today; on a settled window it equals
last_real_price_date, so the deterministic BF reconciliation test (which anchors
on last_real) is unaffected.
"""
import datetime

from src.holdings import last_real_price_date, last_settled_price_date

INC = "2025-05-01"

# A safely-settled historical end: a week ago is always past the current session
# and within committed data (never a freshly-fetched partial bar), so these
# assertions are deterministic and immune to sibling-test cache pollution.
_SETTLED_END = (datetime.date.today() - datetime.timedelta(days=8)).isoformat()


def test_settled_anchor_excludes_today():
    """C is always strictly before today, so an in-progress (partial intraday)
    today-bar can never be a displayed period-window endpoint."""
    c = last_settled_price_date(INC)
    assert c < datetime.date.today().isoformat()


def test_settled_equals_real_on_settled_window():
    """On a settled historical window (end well before today), C == last_real.

    This is the invariant that keeps the BF reconciliation test unchanged: it
    anchors on last_real over committed (settled) data, where C and B coincide.
    """
    assert last_settled_price_date(INC, _SETTLED_END) == last_real_price_date(INC, _SETTLED_END)


def test_settled_anchor_is_deterministic():
    """C for a fixed settled end does not vary across calls (no wall-clock or
    partial-bar dependence)."""
    assert last_settled_price_date(INC, _SETTLED_END) == last_settled_price_date(INC, _SETTLED_END)
