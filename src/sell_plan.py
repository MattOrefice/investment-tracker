"""The Liquidity page's sell plan, and the household allocation before and after it.

PLANS ONLY. Every function here reads DataFrames it is given and returns new ones: no
trade is placed, no row is written, no database is opened.

The plan answers one question: to raise an amount the owner enters, which holdings in
TAXABLE accounts are sold, and in what order. Tax-advantaged accounts are never picked.
The page says why, beside the amount.

    1. cash and money market (a sleeve of ``cash``), largest first;
    2. positions at a loss, the deepest loss as a share of cost first;
    3. positions at a gain, the lowest embedded gain as a share of cost first.

Whole positions are sold until the next one would raise more than is still needed, and
that one is sold in part. A partial sale's cost basis and gain are pro rata to the
position's totals in the positions file: the lots actually sold decide the real figure.

A position at a loss whose ticker is also held in ANOTHER account is skipped and flagged
rather than sold. A purchase of the same ticker in that account within the wash-sale
window would disallow the loss (IRS Publication 550, the page cites it). Only the same
ticker is checked; another fund that may be substantially identical is not.

The allocation groups every household holding into eight broad classes, before and after
a set of sales, beside the policy's targets. Blended funds are looked through with their
``fund_compositions`` rows (``household.look_through_position``). Sale proceeds leave
the household: the after-weights are of what remains.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from src.household import look_through_position

# ── Groups, in the order the plan sells them ──────────────────────────────────
GROUP_CASH = "cash"
GROUP_LOSS = "loss"
GROUP_GAIN = "gain"
GROUP_ORDER: tuple[str, ...] = (GROUP_CASH, GROUP_LOSS, GROUP_GAIN)
GROUP_LABEL: dict[str, str] = {
    GROUP_CASH: "Cash and money market",
    GROUP_LOSS: "At a loss",
    GROUP_GAIN: "Lowest gain as a share of cost",
}

# The wash-sale window: a purchase this many days before or after a sale at a loss.
# IRS Publication 550 (2025), "Wash Sales": "A wash sale occurs when you sell or trade
# stock or securities at a loss and within 30 days before or after the sale you: ...".
WASH_SALE_WINDOW_DAYS: int = 30

# Fidelity's export carries a line for cash still settling, with a value and no
# security. It is cash in the account, so the allocation counts it as cash, and the
# plan never picks it: it is not a holding that can be sold.
PENDING_ACTIVITY = "pending activity"

# ── The eight broad classes, in the order the page shows them ─────────────────
US_STOCKS = "US stocks"
INTL_STOCKS = "International stocks"
THEMES = "Themes and single stocks"
HEDGED = "Hedged equity"
BONDS = "Bonds"
REAL_ASSETS = "Real assets"
ALTERNATIVES = "Alternatives"
CASH = "Cash"
BROAD_CLASSES: tuple[str, ...] = (US_STOCKS, INTL_STOCKS, THEMES, HEDGED, BONDS,
                                  REAL_ASSETS, ALTERNATIVES, CASH)

# Every sleeve a holding can carry, mapped to its broad class. NO DEFAULT: an unlisted
# sleeve raises, because a silent bucket is indistinguishable from a decision (the rule
# SLEEVE_TAX_CHARACTER follows). tests/test_sell_plan.py pins these keys, with
# BLEND_SLEEVES, to the sleeve universe.
BROAD_CLASS_OF_SLEEVE: dict[str, str] = {
    "us_large_core": US_STOCKS,
    "us_large_growth": US_STOCKS,
    "us_large_quality": US_STOCKS,
    "us_large_value": US_STOCKS,
    "us_mid_cap": US_STOCKS,
    "us_small_core": US_STOCKS,
    "us_small_value": US_STOCKS,
    "us_sector_tech": US_STOCKS,
    "us_sector_healthcare": US_STOCKS,
    "intl_all_exus": INTL_STOCKS,
    "intl_developed": INTL_STOCKS,
    "intl_quality": INTL_STOCKS,
    "intl_large_value": INTL_STOCKS,
    "intl_small_value": INTL_STOCKS,
    "emerging_markets": INTL_STOCKS,
    "thematic": THEMES,
    "single_stock": THEMES,
    "hedged_equity": HEDGED,
    "core_fi_treasury": BONDS,
    "tips": BONDS,
    "core_fi_credit": BONDS,
    "high_yield_fi": BONDS,
    "high_yield_muni": BONDS,
    "floating_rate": BONDS,
    "multi_sector_fi": BONDS,
    "real_assets_reit": REAL_ASSETS,
    "real_assets_commodities": REAL_ASSETS,
    "real_assets_gold": REAL_ASSETS,
    "liquid_alt": ALTERNATIVES,
    "crypto": ALTERNATIVES,
    "cash": CASH,
}

# Blends are never classed as themselves: they are looked through to the sleeves their
# fund_compositions rows name. A blend with no such rows raises.
BLEND_SLEEVES: frozenset[str] = frozenset({"multi_asset", "target_date"})

# A policy class with no in-SAA security filed under it takes its broad class from its
# root. Only the roots whose children all share one broad class are here: an Equity
# child has to be read from its securities, since US and international both sit under it.
ROOT_BROAD_CLASS: dict[str, str] = {
    "Income": BONDS,
    "Real Assets": REAL_ASSETS,
    "Cash": CASH,
}

_PICK_COLUMNS = ["account", "pseudonym", "symbol", "group", "shares", "value",
                 "cost_basis", "realized_gain", "partial"]
_WASH_COLUMNS = ["account", "pseudonym", "symbol", "value", "loss", "also_held_in"]
_NO_BASIS_COLUMNS = ["account", "pseudonym", "symbol", "value"]


@dataclass(frozen=True)
class SellPlan:
    """What to sell, and what it raises. ``shortfall`` is what the taxable holdings the
    plan may sell could not cover; it is 0.0 when the amount is met."""
    amount: float
    picks: pd.DataFrame
    raised: float
    realized_gain: float
    shortfall: float
    wash_sale_skips: pd.DataFrame
    no_basis_skips: pd.DataFrame


def _is_pending(symbol: str) -> bool:
    return str(symbol).strip().lower() == PENDING_ACTIVITY


def _aggregate(positions_df: pd.DataFrame) -> pd.DataFrame:
    """One row per (pseudonym, symbol): a symbol listed twice in one account is one
    position. A column with no value in any of its rows stays missing, not 0."""
    return (
        positions_df
        .groupby(["pseudonym", "symbol"], as_index=False, sort=False)
        .agg(quantity=("quantity", lambda s: s.sum(min_count=1)),
             current_value=("current_value", "sum"),
             cost_basis_total=("cost_basis_total", lambda s: s.sum(min_count=1)),
             total_gain_loss=("total_gain_loss", lambda s: s.sum(min_count=1)))
    )


def _sleeve_of(securities_df: pd.DataFrame) -> dict[str, str]:
    sec = securities_df.dropna(subset=["sleeve_category"])
    return dict(zip(sec["ticker"].astype(str), sec["sleeve_category"].astype(str)))


def _names(accounts_df: pd.DataFrame) -> dict[str, str]:
    return {str(p): str(n or p) for p, n in
            zip(accounts_df["pseudonym"], accounts_df["display_name"])}


def build_sell_plan(
    amount: float,
    positions_df: pd.DataFrame,
    accounts_df: pd.DataFrame,
    securities_df: pd.DataFrame,
    *,
    everywhere_df: "pd.DataFrame | None" = None,
) -> SellPlan:
    """The plan to raise ``amount`` from the taxable accounts in ``positions_df``.

    ``accounts_df`` needs pseudonym, tax_treatment and display_name; ``securities_df``
    needs ticker and sleeve_category. ``everywhere_df`` is every position the wash-sale
    check reads, in every account, the household's or not; it defaults to
    ``positions_df``. A loss position is skipped when its ticker has a positive value in
    any other account there.
    """
    amount = max(0.0, float(amount or 0.0))
    names = _names(accounts_df)
    taxable = set(accounts_df.loc[accounts_df["tax_treatment"] == "taxable", "pseudonym"])
    sleeve = _sleeve_of(securities_df)

    held = _aggregate(everywhere_df if everywhere_df is not None else positions_df)
    held = held[held["current_value"] > 0]
    accounts_holding: dict[str, set[str]] = {}
    for p, s in zip(held["pseudonym"], held["symbol"]):
        accounts_holding.setdefault(str(s), set()).add(str(p))

    pos = _aggregate(positions_df)
    pos = pos[pos["pseudonym"].isin(taxable) & (pos["current_value"] > 0)
              & ~pos["symbol"].map(_is_pending)]

    candidates, wash, no_basis = [], [], []
    for r in pos.itertuples(index=False):
        symbol, pseudonym = str(r.symbol), str(r.pseudonym)
        row = {"account": names.get(pseudonym, pseudonym), "pseudonym": pseudonym,
               "symbol": symbol, "value": float(r.current_value)}
        if sleeve.get(symbol) == "cash":
            candidates.append({**row, "group": GROUP_CASH, "quantity": r.quantity,
                               "cost_basis": float("nan"), "gain": 0.0, "share": 0.0})
            continue
        if pd.isna(r.cost_basis_total) or pd.isna(r.total_gain_loss) or r.cost_basis_total <= 0:
            no_basis.append(row)
            continue
        gain, cost = float(r.total_gain_loss), float(r.cost_basis_total)
        if gain < 0:
            others = accounts_holding.get(symbol, set()) - {pseudonym}
            if others:
                wash.append({**row, "loss": gain,
                             "also_held_in": ", ".join(sorted(names.get(o, o) for o in others))})
                continue
            group = GROUP_LOSS
        else:
            group = GROUP_GAIN
        candidates.append({**row, "group": group, "quantity": r.quantity,
                           "cost_basis": cost, "gain": gain, "share": gain / cost})

    rank = {g: i for i, g in enumerate(GROUP_ORDER)}
    # Cash largest first; losses and gains by gain as a share of cost, lowest first.
    # Ties: the larger position first, then account and ticker, so the order is total.
    candidates.sort(key=lambda c: (rank[c["group"]],
                                   c["share"] if c["group"] != GROUP_CASH else 0.0,
                                   -c["value"], c["account"], c["symbol"]))

    picks, remaining = [], amount
    for c in candidates:
        if remaining <= 0.005:
            break
        whole = c["value"] <= remaining + 0.005
        f = 1.0 if whole else remaining / c["value"]
        value = c["value"] if whole else remaining
        picks.append({
            "account": c["account"], "pseudonym": c["pseudonym"], "symbol": c["symbol"],
            "group": c["group"],
            "shares": (float(c["quantity"]) * f if pd.notna(c["quantity"]) else float("nan")),
            "value": round(value, 2),
            "cost_basis": (round(c["cost_basis"] * f, 2) if pd.notna(c["cost_basis"])
                           else float("nan")),
            "realized_gain": round(c["gain"] * f, 2),
            "partial": not whole,
        })
        remaining -= value

    picks_df = pd.DataFrame(picks, columns=_PICK_COLUMNS)
    raised = round(float(picks_df["value"].sum()), 2) if not picks_df.empty else 0.0
    realized = round(float(picks_df["realized_gain"].sum()), 2) if not picks_df.empty else 0.0
    return SellPlan(
        amount=round(amount, 2),
        picks=picks_df,
        raised=raised,
        realized_gain=realized,
        shortfall=round(max(0.0, amount - raised), 2),
        wash_sale_skips=pd.DataFrame(wash, columns=_WASH_COLUMNS),
        no_basis_skips=pd.DataFrame(no_basis, columns=_NO_BASIS_COLUMNS),
    )


def whole_position_sales(positions_df: pd.DataFrame, pseudonym: str,
                         symbols: "list[str]") -> pd.DataFrame:
    """Sales of whole positions, in the shape ``allocation_table`` takes: every listed
    symbol in the one account, at its full value. Raises on a symbol the account does
    not hold, rather than planning the sale of nothing."""
    pos = _aggregate(positions_df)
    pos = pos[pos["pseudonym"] == pseudonym]
    missing = [s for s in symbols if s not in set(pos["symbol"])]
    if missing:
        raise ValueError(f"{pseudonym} holds no {missing}; nothing to sell.")
    sel = pos[pos["symbol"].isin(symbols)]
    return pd.DataFrame({"pseudonym": sel["pseudonym"], "symbol": sel["symbol"],
                         "value": sel["current_value"].astype(float)}).reset_index(drop=True)


def _after_sales(positions: pd.DataFrame, sales: "pd.DataFrame | None") -> pd.DataFrame:
    """``positions`` (aggregated) with each sale's value taken off its position."""
    after = positions.copy()
    if sales is None or sales.empty:
        return after
    for s in sales.itertuples(index=False):
        hit = (after["pseudonym"] == s.pseudonym) & (after["symbol"] == s.symbol)
        if hit.sum() != 1:
            raise ValueError(f"a sale of {s.symbol} in {s.pseudonym} matches "
                             f"{int(hit.sum())} positions, not one.")
        held = float(after.loc[hit, "current_value"].iloc[0])
        if float(s.value) > held + 0.01:
            raise ValueError(f"a sale of {s.value:,.2f} of {s.symbol} in {s.pseudonym} "
                             f"is more than the {held:,.2f} held.")
        after.loc[hit, "current_value"] = held - float(s.value)
    return after


def _class_dollars(positions: pd.DataFrame, securities_df: pd.DataFrame,
                   compositions_df: pd.DataFrame) -> dict[str, float]:
    out = dict.fromkeys(BROAD_CLASSES, 0.0)
    for r in positions.itertuples(index=False):
        value = float(r.current_value)
        if value == 0:
            continue
        if _is_pending(r.symbol):
            out[CASH] += value
            continue
        parts = look_through_position(str(r.symbol), value, compositions_df, securities_df)
        for sleeve, dollars in zip(parts["sleeve"], parts["dollar_value"]):
            if sleeve in BLEND_SLEEVES:
                raise ValueError(
                    f"{r.symbol} is a blend ({sleeve}) with no fund_compositions rows, so "
                    f"it cannot be looked through to a broad class.")
            if sleeve not in BROAD_CLASS_OF_SLEEVE:
                raise ValueError(
                    f"{r.symbol}'s sleeve {sleeve!r} has no broad class in "
                    f"src/sell_plan.BROAD_CLASS_OF_SLEEVE. There is no default.")
            out[BROAD_CLASS_OF_SLEEVE[sleeve]] += float(dollars)
    return out


def policy_targets(asset_classes_df: pd.DataFrame,
                   securities_df: pd.DataFrame) -> dict[str, float]:
    """The policy's target weight, in percent, for each broad class the policy names.

    Each policy class (an ``asset_classes`` row with a parent) takes the broad class of
    the sleeves its in-SAA securities carry; one with none takes its root's
    (``ROOT_BROAD_CLASS``). A class whose securities span two broad classes, or a
    class with a positive target and no broad class, raises. A broad class no policy
    class maps to is absent from the result: the policy sets no target for it.
    """
    ac = asset_classes_df.set_index("asset_class_id")
    parent = {int(i): (None if pd.isna(p) else int(p)) for i, p in ac["parent_id"].items()}
    name = {int(i): str(n) for i, n in ac["name"].items()}

    def root(i: int) -> str:
        while parent[i] is not None:
            i = parent[i]
        return name[i]

    in_saa = securities_df[(securities_df["is_in_saa"] == 1)
                           & securities_df["sleeve_category"].notna()
                           & securities_df["asset_class_id"].notna()]
    out: dict[str, float] = {}
    for cid, p in parent.items():
        if p is None:
            continue
        target = float(ac.loc[cid, "target_weight"] or 0.0) * 100.0
        sleeves = set(in_saa.loc[in_saa["asset_class_id"].astype(int) == cid,
                                 "sleeve_category"].astype(str))
        unknown = sorted(s for s in sleeves if s not in BROAD_CLASS_OF_SLEEVE)
        if unknown:
            raise ValueError(f"policy class {name[cid]!r} holds sleeves with no broad "
                             f"class: {unknown}.")
        classes = {BROAD_CLASS_OF_SLEEVE[s] for s in sleeves}
        if len(classes) > 1:
            raise ValueError(f"policy class {name[cid]!r} spans {sorted(classes)}; a "
                             f"class has one broad class.")
        broad = next(iter(classes)) if classes else ROOT_BROAD_CLASS.get(root(cid))
        if broad is None:
            if target > 0:
                raise ValueError(f"policy class {name[cid]!r} has a {target:.1f}% target "
                                 f"and no broad class.")
            continue
        out[broad] = out.get(broad, 0.0) + target
    return out


def allocation_table(
    positions_df: pd.DataFrame,
    securities_df: pd.DataFrame,
    compositions_df: pd.DataFrame,
    targets: "dict[str, float]",
    sales: "pd.DataFrame | None" = None,
) -> pd.DataFrame:
    """The household's weights by broad class before and after ``sales``.

    ``positions_df`` is the household (non-household accounts already dropped).
    ``sales`` has pseudonym, symbol and value; the proceeds leave the household.
    Returns one row per broad class, in BROAD_CLASSES order: broad_class, target_pct
    (missing where the policy sets none), before_value, before_pct, after_value,
    after_pct. Weights are percent of the household's total on each side.
    """
    pos = _aggregate(positions_df)
    before = _class_dollars(pos, securities_df, compositions_df)
    after = _class_dollars(_after_sales(pos, sales), securities_df, compositions_df)
    tb, ta = sum(before.values()), sum(after.values())
    rows = []
    for c in BROAD_CLASSES:
        rows.append({
            "broad_class": c,
            "target_pct": targets.get(c, float("nan")),
            "before_value": round(before[c], 2),
            "before_pct": (before[c] / tb * 100.0) if tb else float("nan"),
            "after_value": round(after[c], 2),
            "after_pct": (after[c] / ta * 100.0) if ta else float("nan"),
        })
    return pd.DataFrame(rows)
