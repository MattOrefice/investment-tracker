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
  {register_count} number of those register rows — the holdings actually driving
                   {annual_benefit}; differs from {count} for matched_symbols
                   groups (frozen_tod_income counts GHYIX, which is never a row)
value/count/embedded_gain are position-based (the whole holding); annual_benefit
is register-based (only the mislocated rows). This asymmetry is intentional and
matches the authored prose.
"""
from __future__ import annotations

import re

import pandas as pd

from src.household import compute_embedded_gain, compute_sleeve_by_account

# Distinguishes "argument omitted — resolve it" from an explicit None, which means
# "known to be unknown". Both are meaningful for ordinary income, so they cannot
# share a default.
_UNSET = object()

# ── Authored prose (verbatim; do not edit for style) ───────────────────────────

_DEPLOY_ROTH_CASH_PROS = (
    "Zero tax, zero friction, one session. {value} is this year's contribution "
    "sitting in a money market inside your most valuable account. Every day it "
    "sits is compounding you don't get back, in the one wrapper where growth is "
    "never taxed. Every target sleeve is household-underweight against your own "
    "revealed targets, and the buys are sized to each sleeve's dollar gap, so the "
    "trade closes an allocation gap and a location gap at once — the largest share "
    "goes to the deepest underweight, not split by a round number."
)
_DEPLOY_ROTH_CASH_CONS = (
    "Tilts the Roth toward the factor sleeves you are most underweight — small-cap "
    "value and emerging markets among them. Small-cap value underperformed broad "
    "equity for most of the 2010s; emerging markets have had worse decades. You "
    "will open this account in some future year and see it trailing the S&P 500, "
    "and that will be the design working, not failing. The emerging-markets slice "
    "(IEMG) also forfeits the foreign tax credit inside the Roth: foreign "
    "governments withhold tax on non-US dividends, which a taxable account "
    "reclaims as a credit against your US taxes. A Roth owes no US tax to credit "
    "it against, so it is simply lost — a minor drag, accepted because this cash "
    "is Roth-trapped and never-taxed growth outweighs it. If you can't hold that, "
    "hold less of it."
)

_CLEAR_ROTH_PROS = (
    "Free — trades inside a shelter aren't taxable events. This clears {value} of "
    "misplaced income and non-equity holdings across {count} lots out of your most "
    "valuable account, then puts only the equity portion back to work in the Roth: "
    "the {roth_equity_rebuy} of hedged, covered-call funds is rebought "
    "one-for-one as VOO — the uncapped US large-cap they were selling away, a "
    "location move sized to the equity sold, not an allocation change — those are "
    "the Deploy card's job, with new cash. VTI is broader and the default in "
    "isolation, same cost, but VOO is US Large Core's SAA ticker: the rebuy stays "
    "countable where VTI would sit off-SAA, as these hedged funds do today. The "
    "mid/small tail VTI adds is already held in its own sleeves, so bundling it in "
    "would double it where the SAA cannot track. The measured overweight widens "
    "against the 17.35% target — visibility, not new exposure; only the option "
    "overlay goes. The equivalents are bought "
    "inside the Traditional IRA to keep household exposure flat: that is where "
    "hedged, covered-call funds belong, not the Roth — their heavy ordinary income "
    "is sheltered for free there (withdrawals are ordinary regardless), while their "
    "capped upside doesn't merit the Roth's never-taxed growth. Gold yields nothing "
    "and compounds at roughly inflation; thirty-two years of never-taxed growth is "
    "wasted on it. REIT income is ordinary and belongs in pre-tax."
)
_CLEAR_ROTH_CONS = (
    "JHEQX is a mutual fund — check Fidelity's transaction fee and redemption "
    "charge before selling. These (JEPI/JEPQ/HELO/JHEQX) are equity funds with an "
    "option-income overlay, not stable income — most of the long-run return still "
    "comes from equity exposure, with a high distribution yield (largely option "
    "premium) standing in for some appreciation. They cap upside for income; they "
    "are not bond-like, so rebuilding the hedge is optional: you give up that "
    "downside cushion, a real change in risk profile. The horizon argument that "
    "pulls the hedge from the Roth applies to the Traditional IRA too, so keeping "
    "it is a volatility decision, not a tax one — decide it deliberately, not as a "
    "side effect of location.\n\n"
    "**What happens in the Traditional IRA.** This rebuild, the loss-side bond "
    "rebuy, and the gain-side relocation compete for the same {trad_ira_equity} — "
    "the equity now in the Traditional IRA, the only dollars sellable to make room "
    "for relocated assets without new contributions — one shared capacity, not "
    "three; none fit until the 401(k) rollover frees space. The practical default "
    "is not to rebuild at all: over a multi-decade horizon a return-capped fund "
    "fits neither shelter, so the honest baseline lets household hedged equity fall "
    "to about zero. If space stays scarce after the rollover, the rebuild is the "
    "claim to drop: the loss-side bond rebuy and gain-side relocation each buy real "
    "drag relief ({loss_side_benefit}/yr and {gain_side_benefit}/yr) the rebuild lacks."
)

_LOSS_SIDE_PROS = (
    "These {count} holdings sit at a net embedded gain of {embedded_gain} — "
    "effectively flat. Selling owes almost nothing (BFRIX's harvested loss offsets "
    "JEPI's small gain). It removes "
    "{annual_benefit} of recurring annual drag on {value} of assets, and removes "
    "credit risk from holdings treated as a safety buffer. Floating-rate bank "
    "loans and multisector income are equity-correlated in exactly the drawdowns "
    "where you would want them stable."
)
_LOSS_SIDE_CONS = (
    "The wash-sale rule. Rebuying substantially identical funds within 30 days "
    "either side permanently destroys the loss — it cannot attach to a basis "
    "inside an IRA. Rebuy the exposure with a broad total-bond-market fund — BND, "
    "AGG, or FXNAX — in the Traditional IRA, not the same ticker: different enough "
    "to clear the wash-sale rule, and it restores investment-grade bond ballast. "
    "Note the change in kind, not just location: this trades BFRIX's credit "
    "and floating-rate risk for a bond index's duration (rate) risk. This is what "
    "the SAA's Treasury-ballast intent wants — an improvement, but a change in "
    "character, not like-for-like. Relocating into the Traditional IRA also means "
    "these dollars are no longer penalty-free accessible before 59½ — a real "
    "liquidity cost if the house/car goal lands sooner than expected. This "
    "capacity is shared — see {group_2_title}'s Traditional IRA summary."
)

_GAIN_SIDE_PROS = (
    "{annual_benefit} of recurring annual drag on {value} of assets, and the same "
    "credit-risk argument as the loss side. Relocating realizes {embedded_gain} of "
    "gains — now taxable at 18.07% (15% federal + 3.07% PA), since 2026 income put the "
    "0% bracket out of reach — but that {cost_to_realize} is a {payback} payback against "
    "the {annual_benefit}/yr of relief. On the merits, worth doing."
)
_GAIN_SIDE_CONS = (
    "It is worth doing, but there is nowhere to put it yet — the same shared "
    "Traditional IRA capacity {group_2_title} details is already spoken for. So "
    "this is blocked on capacity, not merit: the 401(k) rollover is what frees "
    "the pre-tax space to shelter these gains, the same constraint that makes "
    "that rollover the household's largest lever."
)

_THEMATIC_PROS = (
    "{count} unmanaged positions worth {value}, carrying roughly 0.45% in average "
    "fees against 0.07% for broad index. Most are slivers you could not state a "
    "forward thesis for. The intellectual case for cleaning it up is real."
)
_THEMATIC_CONS = (
    "The excess fee is about $76 a year. Unwinding realizes {embedded_gain} of "
    "gain, costing several hundred dollars in 18.07% capital-gains tax (15% federal + "
    "3.07% PA) now that the 0% bracket is gone — for no location benefit. You have decided to keep the sector bets. "
    "Household-wide the thematic book is 11.8% of your equity — a real allocation, "
    "though each position is a rounding error. The correct action is to log "
    "this as accepted, capped at its current weight — not to fix it. A logged "
    "decision is not drift."
)

_THEMATIC_CAPTION = (
    "This group covers {population_count} holdings worth {population_value}. "
    "{row_count} appear below (worth {shown_value}) — the register lists "
    "mislocations, and most of this book is correctly located in taxable. The "
    "case for cleanup is fees and sprawl, not location."
)

_ROLLOVER_PROS = (
    "Available now — a former employer's plan, not gated on your next job. "
    "It converts {workplace_plan_value} of self-allocating target-date money "
    "into pre-tax space that can hold your bonds — letting fixed income leave "
    "taxable entirely. Pre-tax capacity (room to relocate assets into the "
    "Traditional IRA/401(k) before they're full) is {pretax_capacity} today, "
    "already exhausted; this raises it to {pretax_capacity_after}. Rollovers "
    "typically sell to cash and reinvest in the new plan's menu — these exact "
    "holdings don't survive."
)
_ROLLOVER_CONS = (
    "Rolling straight into a Traditional IRA works today, but creates a "
    "pre-tax balance that triggers pro-rata and forecloses backdoor Roth once "
    "income clears the direct limits. A future employer plan avoids that — so "
    "available now doesn't mean immediately: if a decent plan is coming, wait "
    "for it, not the account being locked. Worth a CPA check either way."
)


# Groups 7–9 are register-backed COVERAGE groups (they exist so every register
# row is claimed by some group). Prose is AUTHORED by the repository owner —
# verbatim; do not edit for style. Dollar figures are templated ({value}/{count}/
# {annual_benefit}/{embedded_gain}/{trad_ira_equity}/{pretax_capacity}/
# {pretax_capacity_threshold}); render_prose raises on any unresolvable one.
_TOD_INCOME_PROS = (
    "{count} ordinary-income holdings worth {value} sit in the TOD account. "
    "{register_count} of them — a hedged-equity mutual fund, a liquid-alternatives "
    "fund, a global allocation fund, and a core-plus bond ETF — throw ordinary "
    "income into your highest-taxed wrapper, at a "
    "cost of {annual_benefit} a year. None of which you selected, none of which you "
    "would buy today. Relocating them to pre-tax space would eliminate that drag "
    "entirely."
)
_TOD_INCOME_CONS = (
    "There is no pre-tax space — the Traditional IRA capacity {group_2_title} "
    "details is already spoken for. And GHYIX is a municipal fund — its interest "
    "is federally exempt, so taxable is exactly where it belongs; sheltering it "
    "would waste the shelter. The rest carry embedded gains of {embedded_gain}, "
    "now taxable at 18.07% (15% federal + 3.07% PA) with the 0% bracket gone — "
    "another reason to leave them undisturbed. This book is externally managed "
    "and frozen by decision, not by oversight. Logged as accepted."
)

# {population_count}/{population_value} span all matched holdings (matched_symbols);
# {row_count} is the register-row count shown in the expander. The gap is GHYIX: a
# federally-exempt muni that generates no A/B action, so it is counted but not listed.
_TOD_INCOME_CAPTION = (
    "This group covers {population_count} holdings worth {population_value}. "
    "{row_count} appear below (worth {shown_value}) — GHYIX is a federally exempt "
    "municipal fund, so there is no federal tax to save and moving it into a "
    "pre-tax account would convert exempt interest into ordinary income. It is "
    "correctly located and generates no action."
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
    "{pretax_capacity_threshold} — already smaller than this book, and "
    "{group_2_title} claims what's left. So the better home exists and is full. "
    "The drag is {annual_benefit} a year — the honest price of running a coherent "
    "SAA inside the only wrapper you control. Accepted until the 401(k) rollover "
    "creates space, which is the argument that makes that rollover the "
    "household's largest lever."
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


# ── International tilt funding: taxable is the tax-home, capacity lives pre-tax ──
# Target share of the household: the 15/9/8-of-49 US-structure proportions applied to
# the 20% ex-cash international region (matches seed_saa's Intl Quality/LV/SV targets).
_INTL_TILT_TARGET_FRACTION = 0.20 * (15 + 9 + 8) / 49 / 0.98
# Estimated annual drag of sheltering the tilts instead of holding them in taxable —
# the forgone foreign tax credit, ~3% intl dividend yield x ~15% effective foreign
# withholding. Both terms vary by country mix and year, so this is one editable
# assumption and {intl_tilt_ftc_cost} scales off it live — never a hardcoded dollar.
_INTL_FTC_DRAG = 0.0045
# The self-directed taxable book (post-Phase-45 single account identity).
_SELF_DIRECTED_PSEUDONYM = "acct_01"

_FUND_INTL_TILTS_PROS = (
    "The split completes the factor structure abroad: Quality (IDHQ), Large Value "
    "(AVIV) and Small Value (AVDV) apply the US structure's proportions to the 20% "
    "international region — the 15-, 9- and 8-of-49 weights the domestic tilts carry — "
    "so the sizing follows from choices already made, not a separate decision. Together "
    "they target {intl_tilt_target_value} against the household. The capacity to fund "
    "them exists in exactly one place: the {workplace_plan_value} MissionSquare "
    "rollover. The self-directed taxable book — international's tax-home, the only "
    "wrapper you control that can credit the foreign tax withheld — holds "
    "{self_directed_value}, so "
    "it cannot fund a {intl_tilt_target_value} sleeve. The counterfactual to funding "
    "these from pre-tax is therefore not 'hold them in taxable' — there is no taxable "
    "capacity — it is 'do not hold them at all,' which forfeits the whole factor "
    "structure to save a credit worth a fraction of it."
)
_FUND_INTL_TILTS_CONS = (
    "Pre-tax forfeits the foreign tax credit. In a taxable account the ~15% foreign tax "
    "withheld on international dividends can generally be credited against US tax on "
    "that income; a shelter cannot claim it at all — and the credit is itself limited, "
    "capped by US liability on the foreign income and not combinable with the "
    "deduction, so a low-income year can leave part of it unused. On current "
    "assumptions the loss is roughly 45 bps a year on the sleeve — about a 3% dividend "
    "yield times that ~15% effective withholding, both of which move with country mix "
    "and year — an estimated {intl_tilt_ftc_cost} a year on the {intl_tilt_target_value} "
    "target. And the two shelters are not equal homes for it: the Traditional IRA is "
    "the worse one — it loses the credit AND converts these funds' qualified dividends "
    "into ordinary income at withdrawal, taxed at your marginal rate — while a Roth "
    "loses the same credit but never taxes the growth again. If the tilts must sit in a "
    "shelter, the Roth is the less-bad one, a live input to where the rollover lands. "
    "That estimated {intl_tilt_ftc_cost} is priced, not hidden: the standing cost of "
    "running the abroad factor structure without the taxable capacity to house it "
    "correctly."
)


# ── Authored group config (scores/statuses are fixed, never computed) ──────────
# status order for the page: act_now, evaluate, blocked, accepted.
STATUS_ORDER = ["act_now", "evaluate", "blocked", "accepted"]

ACTION_GROUPS: list[dict] = [
    {
        "key": "deploy_roth_cash", "title": "Deploy idle Roth cash",
        "score": 10, "status": "act_now",
        "action": "Buy {deploy_targets} with the {value} of idle Roth cash, each "
                  "sized to its household underweight gap — most to {deploy_largest}.",
        "symbols": None, "case_filter": None, "accounts": None,   # informational
        "pros": _DEPLOY_ROTH_CASH_PROS, "cons": _DEPLOY_ROTH_CASH_CONS,
    },
    {
        "key": "clear_roth_non_equity", "title": "Clear misplaced holdings from the Roth",
        "score": 9, "status": "act_now",
        "action": "Sell the {count} misplaced income and non-equity holdings in the "
                  "Roth and rebuy the {roth_equity_rebuy} of hedged equity as VOO "
                  "there — free, no tax.",
        "symbols": ["JEPI", "JEPQ", "HELO", "JHEQX", "USRT", "IAU", "IDGT"],
        "case_filter": ["C"], "accounts": ["Roth IRA"],
        # The Roth equity rebuy (VOO — US Large Core's is_in_saa ticker, so the buy
        # stays inside the SAA; VTI is in no sleeve at all) is sized 1:1 to the
        # hedged, covered-call EQUITY
        # sold — a location move, not the whole card value; {roth_equity_rebuy} sums
        # these symbols in the Roth from live positions (resolve_placeholders).
        "equity_rebuy_symbols": ["JEPQ", "JEPI", "JHEQX", "HELO"],
        "pros": _CLEAR_ROTH_PROS, "cons": _CLEAR_ROTH_CONS,
    },
    {
        # No free/costly verdict in the title: that is derived per render from the
        # register (_summary_line), and this card sells in a TAXABLE account, which
        # the page glossary calls costly regardless of what the block nets. The old
        # "(free)" was authored when the block carried a net loss and stayed put
        # when the Aug-2026 swap turned it into a net gain — a literal cannot track
        # the register, so it does not get to make this claim.
        "key": "relocate_loss_side", "title": "Relocate the loss side",
        # Re-scored Aug-2026 (9/act_now -> 4/evaluate): the 9 rested on "harvests a
        # deduction at zero cost", and that premise left with HLIPX — the advisor
        # took the loss, and the remaining BFRIX+JEPI block nets to a small GAIN.
        # What survives is the credit-risk argument (BFRIX's floating-rate/credit
        # exposure vs the Treasury duration the SAA wants): real but structural —
        # nothing expires, no window closes. That is evaluate, not act_now.
        "score": 4, "status": "evaluate",
        # JEPI in this group is a small GAIN lot (a distinct account-lot that stays
        # here); naming it individually "at a loss" was false, and only BFRIX is named
        # that way now. The block itself nets to a small GAIN since HLIPX left (see
        # the re-scoring note above) — the action prose says "nets to roughly zero"
        # and reports {embedded_gain}, which is templated (never a literal).
        # HLIPX left this block in Aug 2026: the advisor sold it and rebought the same
        # Core Plus strategy as JCPB (ETF wrapper) in taxable — so the harvest this
        # card proposed for HLIPX was executed externally, and JCPB is claimed by the
        # frozen_tod_income card, not here.
        "action": "Sell BFRIX at a loss, and JEPI in taxable; the block "
                  "nets to roughly zero ({embedded_gain} embedded gain), so selling "
                  "costs almost nothing. Rebuy the exposure in the Traditional IRA "
                  "with a broad total-bond fund (BND/AGG/FXNAX).",
        "symbols": ["BFRIX", "JEPI"],
        "case_filter": ["A", "B"], "accounts": ["Individual Taxable (TOD)"],
        "pros": _LOSS_SIDE_PROS, "cons": _LOSS_SIDE_CONS,
    },
    {
        "key": "relocate_gain_side", "title": "Relocate the gain side",
        # A genuinely good move (short payback) that is BLOCKED on pre-tax capacity,
        # not weak on merit — so it sits in the blocked/waiting bucket, scored near
        # the rollover that unblocks it, not down at "not worth doing".
        "score": 3, "status": "blocked",
        "action": "Defer — with the 0% bracket gone this costs {cost_to_realize} to "
                  "relieve {annual_benefit}/yr (a {payback} payback, worth doing), but "
                  "the Traditional IRA is full: the loss side alone nearly consumes it. "
                  "Revisit after the 401(k) rollover frees space.",
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
        "score": 3, "status": "evaluate",
        "action": "Roll the {workplace_plan_value} MissionSquare 401(k) now — a future "
                  "employer plan keeps the Traditional IRA empty; an IRA works too, but "
                  "starts the pro-rata clock.",
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
        # JCPB (Aug-2026): the advisor's core-plus bond ETF replacing HLIPX — an
        # ordinary-income holding placed by the frozen book's manager, so it is
        # claimed here (accepted), not by a sell-side group.
        "symbols": ["BILPX", "GAOSX", "GHYIX", "JHEQX", "JCPB"],
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
    {
        "key": "fund_intl_tilts", "title": "Fund the international tilts",
        "score": 4, "status": "evaluate",
        "action": "Fund IDHQ / AVIV / AVDV — {intl_tilt_target_value} against the "
                  "household — from the {workplace_plan_value} rollover once it lands in "
                  "a shelter. Taxable is their tax-home but holds {self_directed_value}, "
                  "so pre-tax is the only capacity; price the foreign-tax-credit cost in "
                  "when choosing the shelter.",
        # No holdings yet, so informational (no register rows). The forgone foreign
        # tax credit is stated and quantified ({intl_tilt_ftc_cost}) rather than
        # silently allowed by adding the sleeves to _PRETAX_PRIORITY. The mirror of
        # saa_sleeves_taxable: there the SAA sleeves belong in a shelter that is full;
        # here they belong in taxable, which has no capacity.
        "symbols": None, "case_filter": None, "accounts": None,
        "pros": _FUND_INTL_TILTS_PROS, "cons": _FUND_INTL_TILTS_CONS,
    },
]

# Groups rendered without an "Underlying positions" expander (no register rows).
INFORMATIONAL_KEYS = frozenset({"deploy_roth_cash", "rollover_401k", "fund_intl_tilts"})


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


def household_deploy_gaps(
    positions_df: pd.DataFrame,
    accounts_df: pd.DataFrame,
    securities_df: pd.DataFrame,
    compositions_df: pd.DataFrame,
    saa_targets_df: pd.DataFrame,
) -> pd.DataFrame:
    """Roth-deploy-eligible sleeves that are HOUSEHOLD-underweight, with each one's
    dollar gap (target$ − current$), sourced from the single household aggregation
    the Household View uses — compute_household_allocation(mode='look_through',
    scope='total'). Weights are NOT recomputed here; this only reads that frame.

    Eligibility is the same structural rule as the deploy: a sleeve in the roth_ira
    priority map that also has an is_in_saa ticker to buy (so cash / fixed income /
    real assets / hedged equity / developed international never qualify — developed
    intl is absent from the map because a Roth forfeits the foreign tax credit).

    A sleeve at or over its household target (gap ≤ 0) is dropped — new cash never
    overshoots a target. Returns [sleeve, ticker, roth_rank, current, target, gap],
    underweight only, ordered by gap descending (largest underweight first). Empty
    if nothing eligible is underweight.
    """
    from src.location_config import SLEEVE_PRIORITY_BY_ACCOUNT_TYPE
    roth_priority = SLEEVE_PRIORITY_BY_ACCOUNT_TYPE["roth_ira"]
    ticker_by_sleeve = _saa_ticker_by_sleeve(securities_df)
    total_hh = float(positions_df["current_value"].sum())

    from src.household import compute_household_allocation
    alloc = compute_household_allocation(
        positions_df, accounts_df, securities_df, compositions_df, saa_targets_df,
        mode="look_through", scope="total",
    )
    # sleeve_category → SAA output name: the SAME join compute_household_allocation
    # builds internally (is_in_saa securities → asset_classes.name), so the deploy's
    # sleeve_category keys line up with the allocation's SAA-named rows.
    saa_secs = (
        securities_df[securities_df["is_in_saa"] == 1][["sleeve_category", "asset_class_id"]]
        .drop_duplicates("sleeve_category")
    )
    saa_join = saa_secs.merge(
        saa_targets_df[["asset_class_id", "name", "target_weight"]], on="asset_class_id", how="left"
    )
    sleeve_to_saa_name = dict(zip(saa_join["sleeve_category"], saa_join["name"]))
    alloc_by_name = alloc.set_index("sleeve")

    cols = ["sleeve", "ticker", "roth_rank", "current", "target", "gap"]
    rows: list[dict] = []
    for sleeve, rank in sorted(roth_priority.items(), key=lambda kv: kv[1]):
        if sleeve not in ticker_by_sleeve:
            continue                                  # no is_in_saa ticker → not a deploy target
        name = sleeve_to_saa_name.get(sleeve)
        if name is None or name not in alloc_by_name.index:
            continue                                  # sleeve not represented in the SAA allocation
        r = alloc_by_name.loc[name]
        current = float(r["dollar_value"])
        target = float(r["target_weight"]) * total_hh
        gap = target - current
        if gap <= 0:
            continue                                  # at/over household target → don't add
        rows.append({
            "sleeve": sleeve, "ticker": ticker_by_sleeve[sleeve], "roth_rank": rank,
            "current": round(current, 2), "target": round(target, 2), "gap": round(gap, 2),
        })
    if not rows:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame(rows, columns=cols).sort_values("gap", ascending=False).reset_index(drop=True)


def _gap_proportional_split(cash: float, gaps: list[float]) -> tuple[list[float], float]:
    """Allocate `cash` across sleeves in proportion to their dollar `gaps`, capping
    each sleeve's buy at its own gap so none overshoots target. Returns (allocs,
    residual).

    Because the proportional share of sleeve i is cash·gapᵢ/Σgap, a share exceeds
    its gap iff cash > Σgap — the SAME condition for every sleeve at once (the ratio
    share/gap = cash/Σgap is uniform). So there is no partial-cap case: either cash
    ≤ Σgap and a single proportional pass never overshoots, or cash > Σgap and every
    gap fills to the brim with cash − Σgap left over. No iterative water-filling
    needed. Non-positive gaps contribute nothing and receive nothing.
    """
    cash = float(cash)
    pos = [g if g > 0 else 0.0 for g in gaps]
    total = sum(pos)
    if cash <= 0 or total <= 0:
        return [0.0] * len(gaps), round(max(0.0, cash), 2)
    if cash <= total:
        allocs = [round(cash * g / total, 2) for g in pos]
    else:
        allocs = [round(g, 2) for g in pos]          # fill every gap; remainder is residual
    residual = round(cash - sum(allocs), 2)
    return allocs, max(0.0, residual)


def build_roth_deploy_answer(
    positions_df: pd.DataFrame,
    accounts_df: pd.DataFrame,
    securities_df: pd.DataFrame,
    compositions_df: pd.DataFrame | None = None,
    saa_targets_df: pd.DataFrame | None = None,
) -> dict:
    """Answer 'where does the idle Roth cash go' with GAP-PROPORTIONAL sizing: the
    idle cash is spread across every Roth-eligible equity sleeve that is
    household-underweight, in proportion to each sleeve's dollar gap, capped at its
    gap so no sleeve overshoots target. Ticker AND label resolve from ONE sleeve key
    (no alias): us_small_value → its is_in_saa ticker AVUV → the label "US Small
    Value". Only roth-map sleeves with an SAA ticker qualify, so cash / fixed income /
    real assets / hedged equity / developed international never surface.

    The number of tickers now FOLLOWS from how many sleeves are underweight (not a
    fixed count): the pool expands or contracts on real gaps. If the cash exceeds the
    sum of gaps, every gap is filled and the remainder is reported as `residual`
    rather than forced into a sleeve.

    Gaps come from household_deploy_gaps (one household aggregation, not recomputed).
    compositions_df / saa_targets_df are required to size the buys; when omitted (a
    caller that only needs idle_cash) the table is empty and residual == idle_cash.

    Returns {idle_cash, sleeves, table, residual} with table columns
    [ticker, sleeve, dollar], ordered largest-gap first.
    """
    sba = compute_sleeve_by_account(positions_df, accounts_df, securities_df)
    _, idle_cash = _roth_idle_cash(sba, accounts_df)

    empty = pd.DataFrame(columns=["ticker", "sleeve", "dollar"])
    if compositions_df is None or saa_targets_df is None:
        return {"idle_cash": idle_cash, "sleeves": [], "table": empty, "residual": round(idle_cash, 2)}

    gaps_df = household_deploy_gaps(
        positions_df, accounts_df, securities_df, compositions_df, saa_targets_df)
    if gaps_df.empty or idle_cash <= 0:
        return {"idle_cash": idle_cash, "sleeves": [], "table": empty, "residual": round(idle_cash, 2)}

    allocs, residual = _gap_proportional_split(idle_cash, gaps_df["gap"].tolist())
    table = pd.DataFrame({
        "ticker": gaps_df["ticker"].tolist(),
        "sleeve": gaps_df["sleeve"].tolist(),
        "dollar": allocs,
    }, columns=["ticker", "sleeve", "dollar"])
    return {
        "idle_cash": idle_cash,
        "sleeves": gaps_df["sleeve"].tolist(),
        "table": table,
        "residual": residual,
    }


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


def _group_annual_benefit(register: pd.DataFrame, key: str) -> str | None:
    """Another group's total annual_benefit (drag relief), formatted — or None if it
    claims no register rows. Lets one card cite a sibling card's LIVE tax-relief figure
    (the Roth-cleanup card weighs which Traditional-IRA claim to drop against the loss-
    and gain-side relief) without hardcoding a dollar amount."""
    grp = next(g for g in ACTION_GROUPS if g["key"] == key)
    reg = filter_register_for_group(register, grp)
    return _fmt_dollars(float(reg["annual_benefit"].sum())) if not reg.empty else None


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


def _join_tickers(tickers: list[str]) -> str:
    """Grammatical join: "AVUV", "AVUV and IEMG", "AVUV, SPHQ, and IEMG"."""
    if len(tickers) == 1:
        return tickers[0]
    if len(tickers) == 2:
        return " and ".join(tickers)
    return ", ".join(tickers[:-1]) + ", and " + tickers[-1]


def deploy_targets_split(deploy_answer: dict) -> dict[str, str | None]:
    """The {deploy_targets}/{deploy_largest} placeholders for the deploy_roth_cash
    action line: the tickers the idle Roth cash buys, grammatically joined, and the
    ticker taking the largest buy (the deepest household underweight). Sourced from
    build_roth_deploy_answer's gap-proportional table — never hardcoded — so the
    one-liner tracks the deploy table shown directly below it. The per-sleeve amounts
    are no longer a single figure (they vary with each sleeve's gap), so the table
    below carries them; the line names the pool and its largest buy. Returns None
    values when there is nothing to deploy; the page falls back to a generic line."""
    tbl = deploy_answer.get("table")
    if tbl is None or tbl.empty:
        return {"deploy_targets": None, "deploy_largest": None}
    tickers = tbl["ticker"].tolist()
    largest = str(tbl.loc[tbl["dollar"].idxmax(), "ticker"])
    return {"deploy_targets": _join_tickers(tickers), "deploy_largest": largest}


def capital_gains_headroom(
    register: pd.DataFrame,
    ordinary_income: float | None = _UNSET,
    is_demo: bool | None = None,
) -> dict:
    """The 0% LTCG bracket as a finite budget: total, consumed by the recommended
    gain-side realizations, and remaining. Single source of truth for both the
    Assumptions block and group 4's prose, so the two can never disagree.

    Income is resolved from the runtime profile unless passed explicitly. Omitting
    the argument and passing None are DIFFERENT: omitted means "resolve it", None
    means "known to be unknown" — hence the sentinel, so a caller can state the
    unknown case without the resolver silently overriding it.

    ``income_known`` is False when no income is configured; the budget then
    collapses to zero — the conservative reading — and the page says so rather
    than presenting a computed-looking $0.
    """
    from src.location_config import ltcg_headroom

    if ordinary_income is _UNSET:
        from src.personal_profile import load_income_profile
        if is_demo is None:
            from src.config import IS_DEMO
            is_demo = IS_DEMO
        ordinary_income = load_income_profile(is_demo)["ordinary_income"]

    headroom = ltcg_headroom(ordinary_income)
    income_known = headroom is not None
    total = float(headroom) if income_known else 0.0

    gain = next(g for g in ACTION_GROUPS if g["key"] == "relocate_gain_side")
    rows = filter_register_for_group(register, gain)
    consumed = max(0.0, float(rows["embedded_gain"].sum())) if not rows.empty else 0.0
    remaining = max(0.0, total - consumed)
    return {
        "total": total,
        "consumed": consumed,
        "remaining": remaining,
        "income_known": income_known,
        "ordinary_income": ordinary_income,
    }


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
                             argmax; the $0-vested Moody's PPP is deliberately excluded
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

    # International tilt sizing + the forgone-FTC cost of sheltering it (fund_intl_tilts).
    # The tilt target is a household share; the FTC drag scales off it live, so no
    # dollar figure is hardcoded.
    household_total = float(positions_df["current_value"].sum()) if not positions_df.empty else None
    sd = positions_df[positions_df["pseudonym"] == _SELF_DIRECTED_PSEUDONYM]
    self_directed_value = float(sd["current_value"].sum()) if not sd.empty else None
    intl_tilt_target = None if household_total is None else household_total * _INTL_TILT_TARGET_FRACTION
    intl_tilt_ftc = None if intl_tilt_target is None else intl_tilt_target * _INTL_FTC_DRAG

    def _fmt(x): return None if x is None else _fmt_dollars(x)
    return {
        "trad_ira_equity":           _fmt(trad_ira_equity),
        "pretax_capacity":           _fmt(pretax_capacity),
        "workplace_plan_value":      _fmt(workplace_plan_value),
        "pretax_capacity_after":     _fmt(after),
        "pretax_capacity_threshold": _fmt(threshold),
        "self_directed_value":       _fmt(self_directed_value),
        "intl_tilt_target_value":    _fmt(intl_tilt_target),
        "intl_tilt_ftc_cost":        _fmt(intl_tilt_ftc),
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
        # Sibling cards' live drag-relief figures, so the Roth-cleanup card can weigh
        # which shared-capacity claim to drop against the loss-/gain-side tax value
        # without hardcoding either dollar amount.
        "loss_side_benefit": _group_annual_benefit(register, "relocate_loss_side"),
        "gain_side_benefit": _group_annual_benefit(register, "relocate_gain_side"),
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
    # The count of register rows (the holdings driving {annual_benefit}) — derived,
    # never a literal in prose, so a symbols-list change can't strand a spelled-out
    # number (the "Three of them" staleness JCPB's arrival exposed).
    register_count = str(len(reg)) if not reg.empty else None
    # Cost to realize the group's gains (<=0 for a net-loss / free move) and the
    # payback in years (cost / annual relief) — used by the gain-side defer prose.
    _ctr = float(reg["cost_to_realize"].sum()) if not reg.empty else 0.0
    _ab = float(reg["annual_benefit"].sum()) if not reg.empty else 0.0
    cost_to_realize = _fmt_dollars(_ctr) if not reg.empty else None
    payback = f"{_ctr / _ab:.1f}-year" if (_ctr > 0 and _ab > 0) else None

    # Roth equity-rebuy sizing (clear_roth_non_equity): the VOO buy is 1:1 with the
    # hedged, covered-call EQUITY sold — a location move, not the whole card value —
    # so it sums current_value over equity_rebuy_symbols ∩ the group's accounts, from
    # live positions. None (→ render raises) if none of those symbols are held.
    roth_equity_rebuy = None
    _rebuy_syms = group.get("equity_rebuy_symbols")
    if _rebuy_syms:
        _rebuy = positions_df[positions_df["symbol"].isin(_rebuy_syms)]
        _pseudos = _accounts_to_pseudonyms(accounts_df, group.get("accounts"))
        if _pseudos:
            _rebuy = _rebuy[_rebuy["pseudonym"].isin(_pseudos)]
        if not _rebuy.empty:
            roth_equity_rebuy = _fmt_dollars(float(_rebuy["current_value"].sum()))

    return {**base, "value": value, "count": count,
            "embedded_gain": embedded_gain, "annual_benefit": annual_benefit,
            "register_count": register_count,
            "cost_to_realize": cost_to_realize, "payback": payback,
            "roth_equity_rebuy": roth_equity_rebuy}


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
    group has none. Uses {population_count}/{population_value} (population holdings),
    {row_count} (register rows shown in the expander), and {shown_value} (the value
    of those shown rows — equals the expander's Value total row, so the caption's
    second figure matches the table rather than the population figure)."""
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
        "shown_value": _fmt_dollars(reg["current_value"].sum()) if not reg.empty else None,
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
