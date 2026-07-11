"""Authored action groups for the Asset Location page.

Six decisions, not thirty-two rows. Scores and prose are AUTHORED here (the
repository owner's judgement) — they are NOT computed from the data and there is
deliberately no scoring formula. The underlying rows still come from
``build_location_register`` (PR A); this module only groups and narrates them.

Dollar figures inside prose are TEMPLATED from live data via named placeholders
({value}, {embedded_gain}, {annual_benefit}, {count}) — never hardcoded. If a
placeholder a group's prose uses cannot resolve against the current CSV,
``render_prose`` RAISES rather than emitting a blank or "$0".

Placeholder resolution (per group, against the newest positions CSV):
  {value}          Σ current_value of the group's symbols in its accounts
                   (for deploy_roth_cash: the Roth's idle cash)
  {count}          number of those holdings
  {embedded_gain}  Σ embedded_gain of those holdings (compute_embedded_gain)
  {annual_benefit} Σ annual_benefit of the group's matching REGISTER rows
                   (symbols ∩ case_filter ∩ accounts)
value/count/embedded_gain are position-based (the whole holding); annual_benefit
is register-based (only the mislocated rows). This asymmetry is intentional and
matches the authored prose.
"""
from __future__ import annotations

import re

import pandas as pd

from src.household import compute_embedded_gain, compute_sleeve_by_account

# ── Authored prose (verbatim; do not edit for style) ───────────────────────────

_DEPLOY_ROTH_CASH_PROS = (
    "Zero tax, zero friction, one session. {value} is this year's contribution "
    "sitting in a money market inside your most valuable account. Every day it "
    "sits is compounding you don't get back, in the one wrapper where growth is "
    "never taxed. Both target sleeves are household-underweight against your own "
    "revealed targets, so the trade closes an allocation gap and a location gap "
    "at once."
)
_DEPLOY_ROTH_CASH_CONS = (
    "Concentrates the Roth into two high-variance factor bets. Small-cap value "
    "underperformed broad equity for most of the 2010s; emerging markets have had "
    "worse decades. You will open this account in some future year and see it "
    "trailing the S&P 500, and that will be the design working, not failing. If "
    "you can't hold that, hold less of it."
)

_CLEAR_ROTH_PROS = (
    "Free — trades inside a shelter aren't taxable events. This moves {value} "
    "across {count} holdings out of capped-upside and zero-yield assets and into "
    "equity, with the equivalents bought inside the Traditional IRA to keep "
    "household exposure flat. Covered-call funds cap the upside you're paying "
    "premium Roth space to capture. Gold yields nothing and compounds at roughly "
    "inflation; thirty-two years of never-taxed growth is wasted on it. REIT "
    "income is ordinary and belongs in pre-tax."
)
_CLEAR_ROTH_CONS = (
    "JHEQX is a mutual fund — check Fidelity's transaction fee and any short-term "
    "redemption charge before selling. And you give up the downside hedge those "
    "positions provide. That is the point, but it is a real change in the "
    "account's risk profile, and you should decide it deliberately rather than as "
    "a side effect of a location argument."
)

_LOSS_SIDE_PROS = (
    "These {count} holdings sit at a net embedded gain of {embedded_gain} — a "
    "loss. Selling owes nothing and harvests a small deduction. It removes "
    "{annual_benefit} of recurring annual drag on {value} of assets, and, more "
    "importantly, it removes credit risk from holdings you had been treating as a "
    "safety buffer. Floating-rate bank loans and multisector income are "
    "equity-correlated in exactly the drawdowns where you would want them stable."
)
_LOSS_SIDE_CONS = (
    "The wash-sale rule. Rebuying substantially identical funds within 30 days "
    "either side permanently destroys the loss — it cannot attach to a basis "
    "inside an IRA. Buy a different bond fund in the Traditional IRA, not the same "
    "ticker. Separately, that account has finite capacity: it can absorb roughly "
    "{trad_ira_equity} of its own equity, which this consumes almost entirely. "
    "Note that {group_2_title} also draws on this account."
)

_GAIN_SIDE_PROS = (
    "{annual_benefit} of recurring annual drag on {value} of assets, and the same "
    "credit-risk argument as the loss side. The embedded gain across the whole "
    "block is only {embedded_gain}, which is small relative to what it buys."
)
_GAIN_SIDE_CONS = (
    "Your 0% capital-gains headroom for 2026 is {headroom_total}. These "
    "realizations consume {headroom_consumed}, leaving {headroom_remaining} — so "
    "the whole block fits inside the 0% bracket and costs only Pennsylvania's flat "
    "3.07%. But the headroom shrinks with every dollar of unemployment income, "
    "depends on income you cannot know until December, and evaporates the day you "
    "start a job. A genuine wait-and-see."
)

_THEMATIC_PROS = (
    "{count} unmanaged positions worth {value}, carrying roughly 0.45% in average "
    "fees against 0.07% for broad index. Most are slivers you could not state a "
    "forward thesis for. The intellectual case for cleaning it up is real."
)
_THEMATIC_CONS = (
    "The excess fee is about $76 a year. Unwinding realizes {embedded_gain} of "
    "gain, costing several hundred dollars against a narrow 0% headroom you would "
    "rather spend on the income assets. You have decided to keep the sector bets. "
    "Household-wide the thematic book is 11.8% of your equity — a real allocation, "
    "though each position is a rounding error. The correct action is to log "
    "this as accepted, capped at its current weight — not to fix it. A logged "
    "decision is not drift."
)

_THEMATIC_CAPTION = (
    "This group covers {population_count} holdings worth {population_value}. Only "
    "{row_count} appear below — the register lists mislocations, and most of this "
    "book is correctly located in taxable. The case for cleanup is fees and "
    "sprawl, not location."
)

_ROLLOVER_PROS = (
    "The single largest lever in the household. It converts {workplace_plan_value} of "
    "self-allocating target-date money into pre-tax space that can actually hold "
    "your bonds — which is what finally lets fixed income leave your taxable "
    "account entirely. Your investable pre-tax capacity today is {pretax_capacity}, and it "
    "is already exhausted. This raises it to {pretax_capacity_after}."
)
_ROLLOVER_CONS = (
    "Rolling into a Traditional IRA creates a pre-tax IRA balance, triggers the "
    "pro-rata rule, and quietly forecloses clean backdoor Roth contributions once "
    "buy-side income lifts you past the direct limits. Rolling into your next "
    "employer's plan avoids that, and you can move the existing Traditional IRA in "
    "too. But you cannot know whether that plan accepts roll-ins, or whether its "
    "menu is any good, until you have it. Waiting is not procrastination here; it "
    "is the correct move — and it is irreversible enough to run past a CPA."
)


# Groups 7–9 are register-backed COVERAGE groups (they exist so every register
# row is claimed by some group). Prose is AUTHORED by the repository owner —
# verbatim; do not edit for style. Dollar figures are templated ({value}/{count}/
# {annual_benefit}/{embedded_gain}/{trad_ira_equity}/{pretax_capacity}/
# {pretax_capacity_threshold}); render_prose raises on any unresolvable one.
_TOD_INCOME_PROS = (
    "{count} ordinary-income holdings worth {value} sit in the TOD account. Three "
    "of them — a hedged-equity mutual fund, a liquid-alternatives fund, and a global "
    "allocation fund — throw ordinary income into your highest-taxed wrapper, at a "
    "cost of {annual_benefit} a year. None of which you selected, none of which you "
    "would buy today. Relocating them to pre-tax space would eliminate that drag "
    "entirely."
)
_TOD_INCOME_CONS = (
    "There is no pre-tax space. The Traditional IRA holds {trad_ira_equity} of "
    "equity and is already spoken for twice over. And GHYIX is a municipal fund — "
    "its interest is federally exempt, so taxable is exactly where it belongs; "
    "sheltering it would waste the shelter. The rest carry embedded gains of "
    "{embedded_gain} against a narrow 0% headroom you would rather spend on the "
    "income assets above. This book is externally managed and frozen by decision, "
    "not by oversight. Logged as accepted."
)

# {population_count}/{population_value} span all matched holdings (matched_symbols);
# {row_count} is the register-row count shown in the expander. The gap is GHYIX: a
# federally-exempt muni that generates no A/B action, so it is counted but not listed.
_TOD_INCOME_CAPTION = (
    "This group covers {population_count} holdings worth {population_value}. Only "
    "{row_count} appear below — GHYIX is a federally exempt municipal fund, so "
    "there is no federal tax to save and moving it into a pre-tax account would "
    "convert exempt interest into ordinary income. It is correctly located and "
    "generates no action."
)

_SAA_TAXABLE_PROS = (
    "{count} holdings worth {value} — treasuries, TIPS, commodities, and a REIT — "
    "sit in a taxable account, generating {annual_benefit} of ordinary income and "
    "phantom income annually. The framework says these are the sleeves that most "
    "deserve shelter. The register is right to flag them."
)
_SAA_TAXABLE_CONS = (
    "But it is flagging a capacity constraint, not a mistake. These are the SAA's "
    "own fixed-income and real-asset sleeves, and the framework is right that a "
    "pre-tax account would hold them better. Investable pre-tax capacity is "
    "{pretax_capacity}, exhausted at a policy book of roughly "
    "{pretax_capacity_threshold}; the book is already larger than that, and the "
    "Traditional IRA is spoken for twice over by the relocations above. So the "
    "better home exists and is full. The drag is {annual_benefit} a year — the "
    "honest price of running a coherent SAA inside the only wrapper you control. "
    "Accepted until the 401(k) rollover creates space, which is the argument that "
    "makes that rollover the household's largest lever."
)

_STRANDED_EQUITY_PROS = (
    "{count} equity positions worth {value} sit in taxable accounts whose sleeves "
    "rank higher in a Roth than in taxable — small-cap value and emerging markets, "
    "the two sleeves carrying the largest long-run risk premia. Case D is "
    "comparative: it fires only when a sheltered account wants a sleeve more than "
    "taxable does. These four qualify."
)
_STRANDED_EQUITY_CONS = (
    "Two of them resolve themselves. The Roth deploy above buys AVUV and IEMG "
    "directly, which is the correct response to the flag — you build the sheltered "
    "position rather than relocating the taxable one. DFAE and XSOE are "
    "emerging-markets exposure inside the frozen TOD book: observed, not "
    "actionable, and carrying {embedded_gain} of embedded gain. Note also that "
    "holding EM in taxable preserves the foreign tax credit, which an IRA forfeits "
    "entirely — so the case for moving them is weaker than the ranking alone "
    "suggests. Evaluate, don't act."
)


# ── Authored group config (scores/statuses are fixed, never computed) ──────────
# status order for the page: act_now, evaluate, blocked, accepted.
STATUS_ORDER = ["act_now", "evaluate", "blocked", "accepted"]

ACTION_GROUPS: list[dict] = [
    {
        "key": "deploy_roth_cash", "title": "Deploy idle Roth cash",
        "score": 10, "status": "act_now",
        "action": "Buy {deploy_targets} with the {value} of idle Roth cash — "
                  "about {deploy_split} each.",
        "symbols": None, "case_filter": None, "accounts": None,   # informational
        "pros": _DEPLOY_ROTH_CASH_PROS, "cons": _DEPLOY_ROTH_CASH_CONS,
    },
    {
        "key": "clear_roth_non_equity", "title": "Clear misplaced holdings from the Roth",
        "score": 9, "status": "act_now",
        "action": "Sell the {count} misplaced income and non-equity holdings in the "
                  "Roth and rebuy broad equity there — free, no tax.",
        "symbols": ["JEPI", "JEPQ", "HELO", "JHEQX", "USRT", "IAU", "IDGT"],
        "case_filter": ["C"], "accounts": ["Roth IRA"],
        "pros": _CLEAR_ROTH_PROS, "cons": _CLEAR_ROTH_CONS,
    },
    {
        "key": "relocate_loss_side", "title": "Relocate the loss side (free)",
        "score": 9, "status": "act_now",
        "action": "Sell BFRIX, HLIPX, and JEPI in taxable at a loss, then rebuy the "
                  "exposure in the Traditional IRA with a different bond fund.",
        "symbols": ["BFRIX", "HLIPX", "JEPI"],
        "case_filter": ["A", "B"], "accounts": ["Individual Taxable (TOD)"],
        "pros": _LOSS_SIDE_PROS, "cons": _LOSS_SIDE_CONS,
    },
    {
        "key": "relocate_gain_side", "title": "Relocate the gain side",
        "score": 5, "status": "evaluate",
        "action": "Optional — move {value} of holdings (GBOSX, FIWDX, JEPQ, USRT) "
                  "to the Traditional IRA; realizes only {embedded_gain} in gains, "
                  "fits the 0% bracket. Wait-and-see.",
        "symbols": ["GBOSX", "FIWDX", "JEPQ", "USRT"],
        "case_filter": ["A", "B"], "accounts": ["Individual Taxable (TOD)"],
        "pros": _GAIN_SIDE_PROS, "cons": _GAIN_SIDE_CONS,
    },
    {
        "key": "thematic_sprawl", "title": "Thematic sprawl",
        "score": 2, "status": "accepted",
        "action": "Leave as-is — logged as an accepted 11.8% thematic tilt, capped "
                  "at its current weight.",
        "symbols": ["ARKK", "BOTZ", "CIBR", "EMQQ", "FINX", "FRNW", "IBB", "ICLN",
                    "IDGT", "PAVE", "ROBO", "IWC", "QQQJ", "XLK", "XLV", "UFO",
                    "JTEK", "QQQ", "IBIT"],
        "case_filter": ["B", "D"], "accounts": ["Individual Taxable (TOD)"],
        "pros": _THEMATIC_PROS, "cons": _THEMATIC_CONS,
        # {count}/{value} measure the whole sprawl (matched symbols), but the
        # expander lists only the 2 mislocation rows — so a caption is required.
        "population": "matched_symbols", "caption": _THEMATIC_CAPTION,
        "allow_literals": True,   # "$76" excess-fee estimate, not computed
    },
    {
        "key": "rollover_401k", "title": "401(k) rollover",
        "score": 3, "status": "blocked",
        "action": "When you start your next job, roll the 401(k) into that plan — "
                  "not into a Traditional IRA. Blocked until then.",
        "symbols": None, "case_filter": None, "accounts": None,   # informational
        "pros": _ROLLOVER_PROS, "cons": _ROLLOVER_CONS,
    },
    # ── Coverage groups (7–9): every register row must belong to some group ─────
    # These exist so assert_full_coverage passes over the FULL register, not a
    # narrated subset. Prose is scaffolded (see above) pending authored copy.
    {
        "key": "frozen_tod_income", "title": "Frozen TOD income assets",
        "score": 5, "status": "accepted",
        "action": "Leave as-is — no better account exists until the 401(k) rollover "
                  "frees pre-tax space.",
        # Ordinary-income holdings in the frozen TOD book (case A/B). GAOSX is the
        # multi-asset fund with a credit sleeve; MCO is NOT here (correctly located).
        # GHYIX is a federally-exempt muni: build_location_register drops it from
        # cases A/B, so it stays in the population ({count}/{value}, via
        # matched_symbols) but is NOT a register row — the caption states that gap.
        "symbols": ["BILPX", "GAOSX", "GHYIX", "JHEQX"],
        "case_filter": ["A", "B"], "accounts": ["Individual Taxable (TOD)"],
        "pros": _TOD_INCOME_PROS, "cons": _TOD_INCOME_CONS,
        "population": "matched_symbols", "caption": _TOD_INCOME_CAPTION,
    },
    {
        "key": "saa_sleeves_taxable", "title": "SAA sleeves in taxable",
        "score": 1, "status": "accepted",
        "action": "Leave as-is — these belong in a shelter, but pre-tax space is "
                  "full. Revisit after the 401(k) rollover.",
        # The SAA's own fixed-income/real-asset sleeves in the self-directed
        # account (case A). Accepted: pre-tax capacity is exhausted, so there is
        # nowhere better until the 401(k) rollover creates space.
        "symbols": ["PDBC", "SCHP", "VGIT", "VNQ"],
        "case_filter": ["A"], "accounts": ["Individual Taxable (Self-Directed)"],
        "pros": _SAA_TAXABLE_PROS, "cons": _SAA_TAXABLE_CONS,
    },
    {
        "key": "predeploy_stranded_equity", "title": "Taxable small-cap/EM — already handled",
        "score": 4, "status": "evaluate",
        "action": "No action — the Roth deploy above already builds these positions. "
                  "The taxable lots stay put.",
        # Case-D equity a shelter wants more than taxable does. AVUV/IEMG are what
        # the Roth deploy buys directly; DFAE/XSOE are EM in the frozen TOD book —
        # observed, not actionable. (Key kept as predeploy_stranded_equity; the
        # title is plain-language, the jargon lived only in the header.)
        "symbols": ["AVUV", "IEMG", "DFAE", "XSOE"],
        "case_filter": ["D"],
        "accounts": ["Individual Taxable (Self-Directed)", "Individual Taxable (TOD)"],
        "pros": _STRANDED_EQUITY_PROS, "cons": _STRANDED_EQUITY_CONS,
    },
]

# Groups rendered without an "Underlying positions" expander (no register rows).
INFORMATIONAL_KEYS = frozenset({"deploy_roth_cash", "rollover_401k"})


# ── Roth deploy answer ─────────────────────────────────────────────────────────
# Eligibility is structural: only sleeves in the roth_ira priority map can be
# deployed into, so cash / fixed income / real assets / hedged equity /
# international never appear — they are simply not in that map. No exclusion list,
# no alias; the ticker and the label both resolve from one sleeve key.


def _saa_ticker_by_sleeve(securities_df: pd.DataFrame) -> dict[str, str]:
    saa = securities_df[securities_df["is_in_saa"] == 1]
    return dict(zip(saa["sleeve_category"], saa["ticker"]))


def _roth_idle_cash(sba: pd.DataFrame, accounts_df: pd.DataFrame) -> tuple[str, float]:
    """Return (roth_pseudonym, idle_cash) for the Roth holding the most cash."""
    roth_pseudos = set(accounts_df[accounts_df["tax_treatment"] == "roth_ira"]["pseudonym"])
    cash = sba[(sba["sleeve_category"] == "cash") & (sba["pseudonym"].isin(roth_pseudos))]
    if cash.empty:
        # A Roth with no cash sleeve at all -> idle cash is genuinely zero.
        pseudo = next(iter(roth_pseudos), "")
        return pseudo, 0.0
    row = cash.loc[cash["current_value"].idxmax()]
    return str(row["pseudonym"]), float(row["current_value"])


def build_roth_deploy_answer(
    positions_df: pd.DataFrame,
    accounts_df: pd.DataFrame,
    securities_df: pd.DataFrame,
    n_sleeves: int = 2,
) -> dict:
    """Answer 'where does the idle Roth cash go', using the roth_ira account-type
    priority map. Ticker AND label resolve from ONE sleeve key (no alias): the
    Roth's rank-1 sleeve is us_small_value → its is_in_saa ticker AVUV → the label
    "US Small Value". Only sleeves in the roth map (and that have an SAA ticker to
    buy) can appear, so cash / fixed income / real assets / hedged equity /
    international never surface.

    Returns {idle_cash, sleeves, table} with table columns [ticker, sleeve, dollar].
    """
    from src.location_config import SLEEVE_PRIORITY_BY_ACCOUNT_TYPE
    roth_priority = SLEEVE_PRIORITY_BY_ACCOUNT_TYPE["roth_ira"]

    sba = compute_sleeve_by_account(positions_df, accounts_df, securities_df)
    _, idle_cash = _roth_idle_cash(sba, accounts_df)

    ticker_by_sleeve = _saa_ticker_by_sleeve(securities_df)
    # Deploy candidates: roth-map sleeves that have an is_in_saa ticker to buy,
    # ranked by the roth map. No aliasing — the same key yields ticker and label.
    eligible = [
        s for s, _ in sorted(roth_priority.items(), key=lambda kv: kv[1])
        if s in ticker_by_sleeve
    ]
    top = eligible[:n_sleeves]

    per = idle_cash / len(top) if top else 0.0
    rows = [
        {"ticker": ticker_by_sleeve[sleeve], "sleeve": sleeve, "dollar": round(per, 2)}
        for sleeve in top
    ]
    table = pd.DataFrame(rows, columns=["ticker", "sleeve", "dollar"])
    return {"idle_cash": idle_cash, "sleeves": top, "table": table}


# ── Register filtering + placeholder resolution ────────────────────────────────

def _accounts_to_pseudonyms(accounts_df: pd.DataFrame, display_names) -> set[str]:
    if not display_names:
        return set()
    return set(accounts_df[accounts_df["display_name"].isin(display_names)]["pseudonym"])


def filter_register_for_group(register: pd.DataFrame, group: dict) -> pd.DataFrame:
    """Register rows belonging to a group: symbols ∩ case_filter ∩ accounts.

    Rows keep their ORIGINAL register index — no reset_index — so a caller can
    union the returned indices across groups and diff against register.index to
    find rows no group claims (assert_full_coverage depends on exactly this).
    Every current consumer reads column values, lengths, sums, or iterrows, none
    of which the index affects, so preserving it is inert for them; the previous
    reset_index(drop=True) was a footgun that collided a 0-based result index with
    the register's own 0-based index in any index-set arithmetic."""
    if register.empty or not group.get("symbols"):
        return register.iloc[0:0]
    out = register[register["symbol"].isin(group["symbols"])]
    if group.get("case_filter"):
        out = out[out["case"].isin(group["case_filter"])]
    if group.get("accounts"):
        out = out[out["account"].isin(group["accounts"])]
    return out


def _group_title(key: str) -> str:
    """The authored title of a group, by key — so prose can reference another
    group's title (e.g. group 3 → group 2) without hardcoding it, surviving a
    retitle. Raises if the key is unknown (a config typo, caught at render)."""
    return next(g["title"] for g in ACTION_GROUPS if g["key"] == key)


def assert_full_coverage(register: pd.DataFrame) -> None:
    """Every register row must be claimed by at least one action group. RAISE (at
    render) if any row is orphaned — the authored groups and the computed register
    must never silently drift apart. Depends on filter_register_for_group keeping
    each row's original register index (see that function)."""
    if register.empty:
        return
    covered: set = set()
    for g in ACTION_GROUPS:
        covered |= set(filter_register_for_group(register, g).index)
    orphans = register.index.difference(covered)
    if len(orphans):
        det = "; ".join(
            f"{r.symbol}/{r.account}/{r.case}"
            for r in register.loc[orphans, ["symbol", "account", "case"]].itertuples()
        )
        raise ValueError(
            f"{len(orphans)} register row(s) belong to no action group: {det}. "
            "Every register row must be claimed by a group — extend a group's "
            "symbols/case_filter/accounts, or add a group."
        )


def _fmt_dollars(x: float) -> str:
    x = float(x)
    return f"-${abs(x):,.0f}" if x < 0 else f"${x:,.0f}"


def deploy_targets_split(deploy_answer: dict) -> dict[str, str | None]:
    """The {deploy_targets}/{deploy_split} placeholders for the deploy_roth_cash
    action line: the tickers the idle Roth cash buys ("AVUV and IEMG") and the
    per-sleeve amount (the 50/50 split). Sourced from build_roth_deploy_answer's
    computed table — never hardcoded — so the one-liner tracks the deploy table
    shown directly below it. Returns None values when there is nothing to deploy;
    the page falls back to a generic line rather than rendering a blank."""
    tbl = deploy_answer.get("table")
    if tbl is None or tbl.empty:
        return {"deploy_targets": None, "deploy_split": None}
    return {
        "deploy_targets": " and ".join(tbl["ticker"].tolist()),
        "deploy_split": _fmt_dollars(float(tbl["dollar"].iloc[0])),
    }


def capital_gains_headroom(register: pd.DataFrame) -> dict:
    """The 0% LTCG bracket as a finite budget: total, consumed by the recommended
    gain-side realizations, and remaining. Single source of truth for both the
    Assumptions block and group 4's prose, so the two can never disagree."""
    from src.location_config import LTCG_HEADROOM_2026
    gain = next(g for g in ACTION_GROUPS if g["key"] == "relocate_gain_side")
    rows = filter_register_for_group(register, gain)
    consumed = max(0.0, float(rows["embedded_gain"].sum())) if not rows.empty else 0.0
    remaining = max(0.0, float(LTCG_HEADROOM_2026) - consumed)
    return {"total": float(LTCG_HEADROOM_2026), "consumed": consumed, "remaining": remaining}


def _pop_holdings(
    group: dict, positions_df: pd.DataFrame, accounts_df: pd.DataFrame, register: pd.DataFrame,
) -> pd.DataFrame:
    """Holdings a group's {value}/{count} measure over, per its `population`:
      "matched_symbols" — every held position matching symbols ∩ accounts;
      "register_rows"   — only those that are actual register rows (the default).
    For groups whose symbols are all mislocations the two coincide, so making
    "register_rows" the default leaves their rendered output byte-identical."""
    syms = set(group.get("symbols") or [])
    if not syms:
        return positions_df.iloc[0:0]
    pseudos = _accounts_to_pseudonyms(accounts_df, group.get("accounts")) if group.get("accounts") else None
    matched = positions_df[positions_df["symbol"].isin(syms)]
    if pseudos is not None:
        matched = matched[matched["pseudonym"].isin(pseudos)]

    if group.get("population", "register_rows") == "matched_symbols":
        return matched

    reg = filter_register_for_group(register, group)
    if reg.empty:
        return matched.iloc[0:0]
    disp_to_pseudo = dict(zip(accounts_df["display_name"], accounts_df["pseudonym"]))
    reg_pairs = {(r["symbol"], disp_to_pseudo.get(r["account"])) for _, r in reg.iterrows()}
    keep = matched.apply(lambda r: (r["symbol"], r["pseudonym"]) in reg_pairs, axis=1)
    return matched[keep] if len(matched) else matched


def _household_placeholders(
    positions_df: pd.DataFrame, accounts_df: pd.DataFrame, securities_df: pd.DataFrame,
) -> dict[str, str | None]:
    """Account-level placeholders derived straight from positions (not register
    rows), available to every group. A value is None (→ render_prose raises) only
    when the underlying account is absent — never a $0 fallback.

      trad_ira_equity        Σ current_value of EQUITY_SLEEVES holdings in the
                             Traditional IRA (equity sleeves enumerated, not inferred)
      pretax_capacity        Traditional IRA total current_value
      workplace_plan_value   total of the specific rollable 401(k) account
                             (ROLLOVER_SOURCE_PSEUDONYM) — a definition, not an
                             argmax; the Moody's PPP is deliberately excluded
      pretax_capacity_after  pretax_capacity + workplace_plan_value (derived)
      pretax_capacity_threshold
                             the fixed-income/real-asset policy book that the
                             pre-tax capacity can shelter, given pre-tax's ~25%
                             target share of that book: pretax_capacity / 0.25
    """
    from src.location_config import EQUITY_SLEEVES, ROLLOVER_SOURCE_PSEUDONYM
    tt = accounts_df.set_index("pseudonym")["tax_treatment"].to_dict()
    pos = positions_df.copy()
    pos["_tt"] = pos["pseudonym"].map(tt)

    trad = pos[pos["_tt"] == "traditional_ira"]
    pretax_capacity = float(trad["current_value"].sum()) if not trad.empty else None

    trad_ira_equity = None
    if not trad.empty:
        sec = securities_df[["ticker", "sleeve_category"]]
        joined = trad.merge(sec, left_on="symbol", right_on="ticker", how="left")
        eq = joined[joined["sleeve_category"].isin(EQUITY_SLEEVES)]
        trad_ira_equity = float(eq["current_value"].sum())

    # The rollable 401(k) is identified by pseudonym — never by comparing balances.
    wk = positions_df[positions_df["pseudonym"] == ROLLOVER_SOURCE_PSEUDONYM]
    workplace_plan_value = float(wk["current_value"].sum()) if not wk.empty else None

    after = None
    if pretax_capacity is not None and workplace_plan_value is not None:
        after = pretax_capacity + workplace_plan_value

    # The pre-tax capacity shelters a policy book ~4x its size — pre-tax is the
    # ~25% ordinary-income/real-asset share of that book, so capacity / 0.25.
    _PRETAX_BOOK_SHARE = 0.25
    threshold = None if pretax_capacity is None else pretax_capacity / _PRETAX_BOOK_SHARE

    def _fmt(x): return None if x is None else _fmt_dollars(x)
    return {
        "trad_ira_equity":           _fmt(trad_ira_equity),
        "pretax_capacity":           _fmt(pretax_capacity),
        "workplace_plan_value":      _fmt(workplace_plan_value),
        "pretax_capacity_after":     _fmt(after),
        "pretax_capacity_threshold": _fmt(threshold),
    }


def resolve_placeholders(
    group: dict,
    positions_df: pd.DataFrame,
    accounts_df: pd.DataFrame,
    securities_df: pd.DataFrame,
    register: pd.DataFrame,
    roth_idle_cash: float | None = None,
) -> dict[str, str | None]:
    """Resolve every placeholder for a group. A key maps to a formatted string, or
    None if it cannot resolve (empty subset) — render_prose raises if the prose
    references a None key.

    value/count/embedded_gain measure over the group's `population` holdings;
    annual_benefit is register-based; headroom_* and the account-level values
    (trad_ira_equity, workplace_plan_value, …) are household-wide.
    """
    hr = capital_gains_headroom(register)
    base = {
        "headroom_total": _fmt_dollars(hr["total"]),
        "headroom_consumed": _fmt_dollars(hr["consumed"]),
        "headroom_remaining": _fmt_dollars(hr["remaining"]),
        # A group may reference another group's authored title (group 3 → group 2)
        # without hardcoding it, so a retitle never leaves stale cross-references.
        "group_2_title": _group_title("clear_roth_non_equity"),
        **_household_placeholders(positions_df, accounts_df, securities_df),
    }
    if group["key"] == "deploy_roth_cash":
        v = None if roth_idle_cash is None else _fmt_dollars(roth_idle_cash)
        return {**base, "value": v, "count": None, "embedded_gain": None, "annual_benefit": None}

    pop = _pop_holdings(group, positions_df, accounts_df, register)
    value = _fmt_dollars(pop["current_value"].sum()) if not pop.empty else None
    count = str(len(pop)) if not pop.empty else None

    if not pop.empty:
        eg, _ = compute_embedded_gain(positions_df)
        eg = eg.merge(pop[["pseudonym", "symbol"]].drop_duplicates(), on=["pseudonym", "symbol"], how="inner")
        embedded_gain = _fmt_dollars(eg["embedded_gain"].sum()) if not eg.empty else None
    else:
        embedded_gain = None

    reg = filter_register_for_group(register, group)
    annual_benefit = _fmt_dollars(reg["annual_benefit"].sum()) if not reg.empty else None

    return {**base, "value": value, "count": count,
            "embedded_gain": embedded_gain, "annual_benefit": annual_benefit}


_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


def render_prose(template: str, resolved: dict[str, str | None]) -> str:
    """Substitute {placeholders}. RAISE if a referenced placeholder is unresolvable
    — never render a blank or a misleading $0. Returns raw text with real "$"
    (use render_prose_md for anything passed to st.markdown)."""
    def _repl(m: re.Match) -> str:
        key = m.group(1)
        val = resolved.get(key)
        if val is None:
            raise ValueError(
                f"Unresolvable placeholder {{{key}}} — no live data for it in this "
                "group; refusing to render a blank or $0."
            )
        return val
    return _PLACEHOLDER_RE.sub(_repl, template)


def escape_md(text: str) -> str:
    r"""Escape "$" as "\$" so Streamlit Markdown does not read it as a LaTeX math
    delimiter. An unescaped "$" opens math mode and swallows following text (and
    the "$" itself) into monospace — the visible bug where "$51" rendered as
    "- 51" and a workplace-plan balance rendered without its leading "$"."""
    return text.replace("$", "\\$")


def render_prose_md(template: str, resolved: dict[str, str | None]) -> str:
    """render_prose, then escape "$" for safe rendering inside st.markdown."""
    return escape_md(render_prose(template, resolved))


def resolve_caption(
    group: dict, positions_df: pd.DataFrame, accounts_df: pd.DataFrame, register: pd.DataFrame,
) -> str | None:
    """Render a group's population caption (escaped for markdown), or None if the
    group has none. Uses {population_count}/{population_value} (population holdings)
    and {row_count} (register rows shown in the expander)."""
    tmpl = group.get("caption")
    if not tmpl:
        return None
    pop = _pop_holdings(group, positions_df, accounts_df, register)
    reg = filter_register_for_group(register, group)
    row_count = len(reg)
    resolved = {
        "population_count": str(len(pop)) if not pop.empty else None,
        "population_value": _fmt_dollars(pop["current_value"].sum()) if not pop.empty else None,
        "row_count": str(row_count),
    }
    out = escape_md(render_prose(tmpl, resolved))
    # Subject-verb agreement for the "{row_count} appear below" clause, fixed HERE so
    # no caption template carries a per-group singular/plural form: one row "appears",
    # more than one "appear".
    if row_count == 1:
        out = out.replace(" appear below", " appears below")
    return out


def validate_action_groups() -> None:
    """Config-load-time invariants — raise HERE (at import), never at render time:
      - a group whose population can differ from its register-row count
        (population='matched_symbols') MUST supply a caption."""
    for g in ACTION_GROUPS:
        if g.get("population") == "matched_symbols" and not g.get("caption"):
            raise ValueError(
                f"group {g['key']!r} uses population='matched_symbols' (its count may "
                "differ from its register-row count) but supplies no caption."
            )


validate_action_groups()   # enforce config invariants at import time
