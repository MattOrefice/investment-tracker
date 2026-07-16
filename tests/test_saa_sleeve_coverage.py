"""Guard: every strategic SAA sleeve is actually reachable through the allocation.

WHY THIS EXISTS
---------------
`compute_household_allocation` derives the set of SAA sleeves from the SECURITIES
join, not from asset_classes directly:

    saa_secs           = securities[is_in_saa == 1]           (household.py:151)
    saa_join           = saa_secs.merge(saa_targets, on=asset_class_id, how="left")
    saa_name_to_target = saa_join ...                          (household.py:164)
    saa_output_names   = set(saa_name_to_target)               (household.py:167)

So a sleeve with a live target but NO is_in_saa carrier does not become a
zero-exposure row — the backfill at household.py:244 iterates `saa_name_to_target`,
which never contained it. The sleeve VANISHES from the frame: no row, no drift,
no `no_exposure` flag, while its target still sits in asset_classes. The only
assertion inside that function (household.py:207) compares DOLLAR sums, which
still reconcile because the money simply relabels itself off-SAA.

That is a silent, total loss of a sleeve. These tests assert on the RETURNED
FRAME so it cannot happen quietly.

WHY THE SECURITIES FRAME IS RECONSTRUCTED
-----------------------------------------
demo.db carries `sleeve_category` and `is_in_saa` as NULL on all 21 securities —
the household mapping is personal-mode only (loaded from the CSV into tracker.db),
and demo's schema is deliberately additive-and-inert. Reading demo.db alone would
make `saa_secs` empty and this guard would fail on arrival for the wrong reason.

So the frame is rebuilt from the two committed sources that CI does have:
  - demo.db securities  -> the real asset_class_id (seeded by seed_securities.py)
  - data/seed/securities_household.csv -> the real sleeve_category / is_in_saa
That is the same shape personal mode assembles, from data in the repo. Both sides
of every assertion are read live; nothing here hardcodes a sleeve list or a count.
"""
import pathlib
import sys

import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

_REPO = pathlib.Path(__file__).resolve().parent.parent
_HOUSEHOLD_CSV = _REPO / "data" / "seed" / "securities_household.csv"


def _strategic_asset_classes() -> pd.DataFrame:
    """The DB side, read live: every sub-class carrying a real target."""
    from src.db import get_connection

    with get_connection() as conn:
        return pd.read_sql_query(
            "SELECT asset_class_id, name, target_weight FROM asset_classes "
            "WHERE parent_id IS NOT NULL AND target_weight > 0",
            conn,
        )


def _securities_frame() -> pd.DataFrame:
    """demo.db's real asset_class_id + the committed CSV's real household mapping."""
    from src.db import get_connection

    with get_connection() as conn:
        sec = pd.read_sql_query("SELECT ticker, name, asset_class_id FROM securities", conn)

    csv = pd.read_csv(_HOUSEHOLD_CSV)
    csv["is_in_saa"] = (
        csv["is_in_saa"].astype(str).str.strip().str.lower().eq("true").astype(int)
    )
    merged = sec.merge(
        csv[["symbol", "sleeve_category", "is_in_saa"]],
        left_on="ticker", right_on="symbol", how="left",
    )
    merged["is_in_saa"] = merged["is_in_saa"].fillna(0).astype(int)
    return merged


def _allocate(securities_df: pd.DataFrame, asset_classes: pd.DataFrame) -> pd.DataFrame:
    """Run the real allocation over an equal-dollar position in every SAA ticker.

    Equal dollars: this guard is about which sleeves are REACHABLE, not about
    weights, so the position sizes are deliberately uniform and meaningless.
    """
    from src.household import compute_household_allocation

    saa = securities_df[securities_df["is_in_saa"] == 1]
    positions = pd.DataFrame(
        [{"pseudonym": "acct_x", "symbol": t, "current_value": 1000.0}
         for t in saa["ticker"]]
    )
    accounts = pd.DataFrame(
        [{"pseudonym": "acct_x", "tax_treatment": "taxable", "included_in_household": 1}]
    )
    compositions = pd.DataFrame(
        columns=["fund_symbol", "underlying_sleeve", "weight", "as_of_date", "source"]
    )
    return compute_household_allocation(
        positions, accounts, securities_df, compositions, asset_classes,
        mode="look_through", scope="total",
    )


def _on_saa_names(alloc: pd.DataFrame) -> set:
    return set(alloc[~alloc["is_off_saa"]]["sleeve"])


def _on_saa_target_sum(alloc: pd.DataFrame) -> float:
    return float(alloc[~alloc["is_off_saa"]].drop_duplicates("sleeve")["target_weight"].sum())


# ── (a) every strategic sleeve is reachable ────────────────────────────────────

def test_every_strategic_sleeve_appears_on_saa_in_the_returned_frame():
    """The allocation must surface EVERY sleeve asset_classes targets — no more,
    no fewer. A sleeve that loses its is_in_saa carrier disappears silently
    (household.py:164 derives the sleeve set from securities, not asset_classes),
    so this compares the returned frame against the DB rather than a fixed list."""
    ac = _strategic_asset_classes()
    alloc = _allocate(_securities_frame(), ac)

    on_saa = _on_saa_names(alloc)
    expected = set(ac["name"])

    assert on_saa == expected, (
        f"On-SAA sleeves in the returned frame do not match asset_classes.\n"
        f"  missing from the frame (silently dropped): {sorted(expected - on_saa)}\n"
        f"  present but not a strategic target:        {sorted(on_saa - expected)}\n"
        f"A missing sleeve means no is_in_saa security carries its sleeve_category — "
        f"it renders nowhere, with no error, while its target still sits in the DB."
    )


# ── (b) the targets the frame exposes still sum to the DB's ────────────────────

def test_on_saa_target_weights_sum_matches_db_within_1bp():
    """The frame's on-SAA target weights must sum to the DB's. If a sleeve drops
    out, this silently falls short by exactly that sleeve's target — the frame
    keeps reconciling on DOLLARS (household.py:207) while the TARGETS no longer
    add up to the SAA."""
    ac = _strategic_asset_classes()
    alloc = _allocate(_securities_frame(), ac)

    frame_sum = _on_saa_target_sum(alloc)
    db_sum = float(ac["target_weight"].sum())

    assert abs(frame_sum - db_sum) < 1e-4, (
        f"On-SAA target weights sum to {frame_sum:.10f} but asset_classes sums to "
        f"{db_sum:.10f} (delta {abs(frame_sum - db_sum):.2e}). A shortfall of one "
        f"sleeve's target means that sleeve is unreachable through the securities join."
    )


# ── the guard's own liveness ───────────────────────────────────────────────────

def test_guard_fails_when_a_sleeve_loses_its_is_in_saa_carrier():
    """Proves the two guards above can actually FAIL.

    A guard that has only ever passed is worth nothing — see
    test_sleeve_weights_sum_to_one, which normalizes its input and therefore
    asserts only that division works. This applies the exact regression the guards
    exist to catch (the sole intl_developed carrier loses is_in_saa) and asserts
    that BOTH guards break. If this test ever fails, the guards have gone vacuous.
    """
    ac = _strategic_asset_classes()
    sec = _securities_frame()

    carriers = sec[(sec["sleeve_category"] == "intl_developed") & (sec["is_in_saa"] == 1)]
    assert not carriers.empty, (
        "No is_in_saa security carries intl_developed — the mutation cannot be "
        "applied, which means the guards above are already unprotected."
    )

    mutated = sec.copy()
    mutated.loc[carriers.index, "is_in_saa"] = 0
    alloc = _allocate(mutated, ac)

    # (a) must break: the sleeve vanishes entirely rather than showing zero exposure
    on_saa = _on_saa_names(alloc)
    expected = set(ac["name"])
    assert on_saa != expected, (
        "Guard (a) did NOT fail under mutation — dropping the sole intl_developed "
        "carrier left the on-SAA sleeve set intact. The guard is vacuous."
    )
    dropped = expected - on_saa
    assert dropped, "Expected a sleeve to disappear from the frame; none did."

    # It vanishes — it does NOT come back as a zero-exposure row
    assert not any(alloc["sleeve"].isin(dropped)), (
        f"{sorted(dropped)} still has a row; the failure mode this guards is a "
        f"TOTAL disappearance, not a zero-exposure row."
    )

    # (b) must break: targets fall short by exactly the dropped sleeve's weight
    frame_sum = _on_saa_target_sum(alloc)
    db_sum = float(ac["target_weight"].sum())
    assert abs(frame_sum - db_sum) >= 1e-4, (
        "Guard (b) did NOT fail under mutation — the target sum still matched. "
        "The guard is vacuous."
    )
    lost = float(ac[ac["name"].isin(dropped)]["target_weight"].sum())
    assert abs((db_sum - frame_sum) - lost) < 1e-9, (
        f"The shortfall ({db_sum - frame_sum:.10f}) should equal the dropped "
        f"sleeve's target ({lost:.10f})."
    )


# ── The "one is_in_saa ticker per sleeve" rule, guarded in CI ──────────────────
# tests/test_securities_mapping.py pins the SAA ticker set against tracker.db, but
# it is personal-mode only (_skip_if_no_tracker_db) so CI never runs it — the
# natural guard for an SAA ticker change never fires where it would be caught.
# These run in CI against the committed CSV, and are structural rather than a
# second hand-maintained frozenset: a literal set cannot fail on a ticker it
# does not mention.

def _csv_saa_rows() -> pd.DataFrame:
    csv = pd.read_csv(_HOUSEHOLD_CSV)
    csv["is_in_saa"] = (
        csv["is_in_saa"].astype(str).str.strip().str.lower().eq("true")
    )
    return csv[csv["is_in_saa"]]


def test_exactly_one_saa_carrier_per_sleeve_category():
    """No sleeve_category may have two is_in_saa tickers.

    household.py:153 does .drop_duplicates("sleeve_category") on the is_in_saa
    rows, so a second carrier is silently discarded — and WHICH one survives is
    row order, not a decision. The deploy would then buy an arbitrary ticker.
    Nothing else catches this.
    """
    saa = _csv_saa_rows()
    dupes = saa[saa.duplicated("sleeve_category", keep=False)]
    assert dupes.empty, (
        "Multiple is_in_saa tickers share a sleeve_category:\n"
        f"{dupes[['symbol', 'sleeve_category']].to_string(index=False)}\n"
        "household.py:153 drop_duplicates() would keep whichever comes first in "
        "the CSV and silently discard the rest."
    )


def test_every_strategic_sleeve_has_exactly_one_saa_ticker():
    """Each strategic sleeve must have exactly one is_in_saa carrier.

    Zero carriers is the failure that deletes a sleeve from the allocation
    entirely (see the guards above). Read live on both sides: the CSV supplies
    is_in_saa, the DB supplies asset_class_id and which sleeves are strategic.
    """
    from src.db import get_connection

    with get_connection() as conn:
        strategic = {
            r["asset_class_id"]: r["name"]
            for r in conn.execute(
                "SELECT asset_class_id, name FROM asset_classes "
                "WHERE parent_id IS NOT NULL AND target_weight > 0"
            ).fetchall()
        }
        ticker_to_class = {
            r["ticker"]: r["asset_class_id"]
            for r in conn.execute("SELECT ticker, asset_class_id FROM securities").fetchall()
        }

    counts = {name: 0 for name in strategic.values()}
    for sym in _csv_saa_rows()["symbol"].str.strip():
        cls = ticker_to_class.get(sym)
        if cls in strategic:
            counts[strategic[cls]] += 1

    uncovered = sorted(n for n, c in counts.items() if c == 0)
    assert not uncovered, (
        f"Strategic sleeves with no is_in_saa ticker: {uncovered}. Such a sleeve "
        f"is unreachable through the securities join and vanishes from the "
        f"allocation frame with no error."
    )
