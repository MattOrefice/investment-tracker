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
    "{trad_ira_equity} of its own equity, which this consumes almost entirely."
)

_GAIN_SIDE_PROS = (
    "{annual_benefit} of recurring annual drag on {value} of assets, and the same "
    "credit-risk argument as the loss side. The embedded gain across the whole "
    "block is only {embedded_gain}, which is small relative to what it buys."
)
_GAIN_SIDE_CONS = (
    "This is where the honest uncertainty sits. Your 0% capital-gains headroom for "
    "2026 is roughly {headroom_total}, of which {headroom_remaining} is left after "
    "the gain-side realization above — narrow, and it shrinks with every dollar of "
    "unemployment income. Part of this fits inside it; the rest is taxed at 15% "
    "federal plus 3.07% Pennsylvania. Worse, the headroom depends on income you "
    "cannot know until December, and it evaporates the day you start a job. A "
    "genuine wait-and-see."
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
    "{population_count} holdings, {population_value}. Only {row_count} appear "
    "below — the register lists mislocations, and most of this book is correctly "
    "located in taxable. The case for cleanup is fees and sprawl, not location."
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


# ── Authored group config (scores/statuses are fixed, never computed) ──────────
# status order for the page: act_now, evaluate, blocked, accepted.
STATUS_ORDER = ["act_now", "evaluate", "blocked", "accepted"]

ACTION_GROUPS: list[dict] = [
    {
        "key": "deploy_roth_cash", "title": "Deploy idle Roth cash",
        "score": 10, "status": "act_now",
        "symbols": None, "case_filter": None, "accounts": None,   # informational
        "pros": _DEPLOY_ROTH_CASH_PROS, "cons": _DEPLOY_ROTH_CASH_CONS,
    },
    {
        "key": "clear_roth_non_equity", "title": "Clear non-equity from the Roth",
        "score": 9, "status": "act_now",
        "symbols": ["JEPI", "JEPQ", "HELO", "JHEQX", "USRT", "IAU"],
        "case_filter": ["C"], "accounts": ["Roth IRA"],
        "pros": _CLEAR_ROTH_PROS, "cons": _CLEAR_ROTH_CONS,
    },
    {
        "key": "relocate_loss_side", "title": "Relocate the loss side (free)",
        "score": 9, "status": "act_now",
        "symbols": ["BFRIX", "HLIPX", "JEPI"],
        "case_filter": ["A", "B"], "accounts": ["Individual Taxable (TOD)"],
        "pros": _LOSS_SIDE_PROS, "cons": _LOSS_SIDE_CONS,
    },
    {
        "key": "relocate_gain_side", "title": "Relocate the gain side",
        "score": 5, "status": "evaluate",
        "symbols": ["GBOSX", "FIWDX", "JEPQ", "USRT"],
        "case_filter": ["A", "B"], "accounts": ["Individual Taxable (TOD)"],
        "pros": _GAIN_SIDE_PROS, "cons": _GAIN_SIDE_CONS,
    },
    {
        "key": "thematic_sprawl", "title": "Thematic sprawl",
        "score": 2, "status": "accepted",
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
        "symbols": None, "case_filter": None, "accounts": None,   # informational
        "pros": _ROLLOVER_PROS, "cons": _ROLLOVER_CONS,
    },
]

# Groups rendered without an "Underlying positions" expander (no register rows).
INFORMATIONAL_KEYS = frozenset({"deploy_roth_cash", "rollover_401k"})


# ── Roth deploy answer ─────────────────────────────────────────────────────────
# Equity, non-international sleeves only belong in the Roth's tax-free space.
# Everything below is INELIGIBLE and must never appear in the deploy answer.
ROTH_DEPLOY_EXCLUDED_SLEEVES = frozenset({
    "cash", "hedged_equity",
    "core_fi_treasury", "core_fi_credit", "tips",
    "high_yield_fi", "high_yield_muni", "floating_rate", "multi_sector_fi",
    "real_assets_reit", "real_assets_commodities", "real_assets_gold",
    "intl_developed", "intl_all_exus",
})
# The priority map labels the small-cap slot us_small_core; the SAA implements it
# with the small-VALUE ETF (AVUV), so map to it for the is_in_saa lookup.
_DEPLOY_SLEEVE_ALIAS = {"us_small_core": "us_small_value"}


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
    sleeve_priority: dict,
    n_sleeves: int = 2,
) -> dict:
    """Answer 'where does the idle Roth cash go', not a picker.

    Returns {idle_cash, sleeves, table} where table has columns
    [ticker, sleeve, dollar] — the top-N eligible deploy sleeves split evenly.
    RAISES if an ineligible sleeve (cash / hedged_equity / FI / real asset /
    international) would appear — that means PR A's exclusion logic is broken.
    """
    sba = compute_sleeve_by_account(positions_df, accounts_df, securities_df)
    _, idle_cash = _roth_idle_cash(sba, accounts_df)

    eligible = [
        s for s, _ in sorted(sleeve_priority.items(), key=lambda kv: kv[1])
        if s not in ROTH_DEPLOY_EXCLUDED_SLEEVES
    ]
    top = eligible[:n_sleeves]
    leaked = set(top) & ROTH_DEPLOY_EXCLUDED_SLEEVES
    if leaked:
        raise ValueError(
            f"Ineligible sleeve(s) reached the Roth deploy answer: {sorted(leaked)}. "
            "PR A's exclusion logic is broken."
        )

    ticker_by_sleeve = _saa_ticker_by_sleeve(securities_df)
    per = idle_cash / len(top) if top else 0.0
    rows = []
    for sleeve in top:
        ticker = ticker_by_sleeve.get(_DEPLOY_SLEEVE_ALIAS.get(sleeve, sleeve))
        if ticker is None:
            raise ValueError(f"No is_in_saa ticker for deploy sleeve {sleeve!r}.")
        rows.append({"ticker": ticker, "sleeve": sleeve, "dollar": round(per, 2)})
    table = pd.DataFrame(rows, columns=["ticker", "sleeve", "dollar"])
    return {"idle_cash": idle_cash, "sleeves": top, "table": table}


# ── Register filtering + placeholder resolution ────────────────────────────────

def _accounts_to_pseudonyms(accounts_df: pd.DataFrame, display_names) -> set[str]:
    if not display_names:
        return set()
    return set(accounts_df[accounts_df["display_name"].isin(display_names)]["pseudonym"])


def filter_register_for_group(register: pd.DataFrame, group: dict) -> pd.DataFrame:
    """Register rows belonging to a group: symbols ∩ case_filter ∩ accounts."""
    if register.empty or not group.get("symbols"):
        return register.iloc[0:0]
    out = register[register["symbol"].isin(group["symbols"])]
    if group.get("case_filter"):
        out = out[out["case"].isin(group["case_filter"])]
    if group.get("accounts"):
        out = out[out["account"].isin(group["accounts"])]
    return out.reset_index(drop=True)


def _fmt_dollars(x: float) -> str:
    x = float(x)
    return f"-${abs(x):,.0f}" if x < 0 else f"${x:,.0f}"


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

    def _fmt(x): return None if x is None else _fmt_dollars(x)
    return {
        "trad_ira_equity":       _fmt(trad_ira_equity),
        "pretax_capacity":       _fmt(pretax_capacity),
        "workplace_plan_value":  _fmt(workplace_plan_value),
        "pretax_capacity_after": _fmt(after),
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
        "headroom_remaining": _fmt_dollars(hr["remaining"]),
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
    "- 51" and "$77,690" as "77,690"."""
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
    resolved = {
        "population_count": str(len(pop)) if not pop.empty else None,
        "population_value": _fmt_dollars(pop["current_value"].sum()) if not pop.empty else None,
        "row_count": str(len(reg)),
    }
    return escape_md(render_prose(tmpl, resolved))


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
