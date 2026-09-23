"""Tests for the authored Asset Location action groups (src/location_actions.py).

Live-data tests build the real register from the newest CSV + tracker.db and
skip when those personal-mode inputs are absent. Pure tests (placeholder-raise,
deploy exclusion, score pinning) run everywhere.
"""
import pathlib
import sqlite3

import pandas as pd
import pytest

import re

from src.location_actions import (
    ACTION_GROUPS,
    INFORMATIONAL_KEYS,
    build_roth_deploy_answer,
    deploy_state,
    deploy_card_text,
    DEPLOY_BUY_FORMAT,
    household_deploy_gaps,
    _gap_proportional_split,
    deploy_targets_split,
    resolve_placeholders,
    resolve_caption,
    render_prose,
    render_prose_md,
    escape_md,
    _fmt_dollars,
    _pop_holdings,
    filter_register_for_group,
    capital_gains_headroom,
    validate_action_groups,
    assert_full_coverage,
    _group_title,
)
from src.location_config import (
    TAX_PROFILE,
    SLEEVE_PRIORITY_BY_ACCOUNT_TYPE,
    ACCOUNT_SHELTER_PRIORITY,
    DIRECTABLE_PSEUDONYMS,
    is_directable,
)
from src.household import build_location_register

ROOT       = pathlib.Path(__file__).resolve().parent.parent
TRACKER_DB = ROOT / "data" / "tracker.db"

_REGISTER_COLS = ["holding", "symbol", "account", "sleeve", "case", "current_value",
                  "annual_benefit", "assumed_yield", "yield_basis",
                  "embedded_gain", "cost_to_realize", "is_free", "payback_months"]


def _live():
    from src.household_data import find_latest_positions_csv
    csv = find_latest_positions_csv()
    if csv is None or not TRACKER_DB.exists() or TRACKER_DB.stat().st_size == 0:
        pytest.skip("personal-mode inputs absent")
    from src.ingestion.fidelity import parse_fidelity_csv
    pos = parse_fidelity_csv(csv)
    conn = sqlite3.connect(str(TRACKER_DB))
    acct = pd.read_sql_query("SELECT * FROM accounts", conn)
    sec = pd.read_sql_query("SELECT * FROM securities", conn)
    # Compositions are loaded HERE because the page loads them: a blend is sized by
    # looking through its composition, and a helper that omits the frame the page
    # passes stops mirroring the page. Omitting it made every blend refuse, which
    # surfaced as frozen_tod_income.pros raising on {annual_benefit} — the designed
    # behaviour for a caller with no compositions, in a helper that should have had
    # them.
    comps = pd.read_sql_query("SELECT * FROM fund_compositions", conn)
    conn.close()
    reg = build_location_register(pos, acct, sec, TAX_PROFILE,
                                  SLEEVE_PRIORITY_BY_ACCOUNT_TYPE, ACCOUNT_SHELTER_PRIORITY,
                                  compositions_df=comps)
    return pos, acct, sec, reg


def _live_saa():
    """The two SAA tables the gap-proportional deploy needs (compositions +
    per-sleeve targets), loaded exactly as the page does. Call alongside _live()
    only in tests that inspect the deploy TABLE (not those needing idle_cash only)."""
    conn = sqlite3.connect(str(TRACKER_DB))
    comps = pd.read_sql_query("SELECT * FROM fund_compositions", conn)
    targets = pd.read_sql_query(
        "SELECT asset_class_id, name, target_weight, tolerance_band FROM asset_classes "
        "WHERE parent_id IS NOT NULL AND target_weight > 0", conn)
    conn.close()
    return comps, targets


def _live_tier(pos, acct, sec):
    """The deploy answer and its tier, built exactly as page 14 builds them."""
    comps, targets = _live_saa()
    dep = build_roth_deploy_answer(pos, acct, sec, comps, targets)
    g = next(x for x in ACTION_GROUPS if x["key"] == "deploy_roth_cash")
    return dep, deploy_state(g, dep, targets)


# ── Scores are authored config, never computed ─────────────────────────────────

def test_scores_are_read_from_config_verbatim():
    got = {g["key"]: g["score"] for g in ACTION_GROUPS}
    assert got == {
        "deploy_roth_cash": 10,
        "clear_roth_non_equity": 9,
        "relocate_loss_side": 4,
        "relocate_gain_side": 3,
        "thematic_sprawl": 2,
        "rollover_401k": 3,
        "frozen_tod_income": 5,
        "saa_sleeves_taxable": 1,
        "predeploy_stranded_equity": 4,
        "fund_intl_tilts": 4,
    }


def test_statuses_are_read_from_config_verbatim():
    got = {g["key"]: g["status"] for g in ACTION_GROUPS}
    assert got == {
        "deploy_roth_cash": "act_now",
        "clear_roth_non_equity": "act_now",
        "relocate_loss_side": "evaluate",
        "relocate_gain_side": "blocked",
        "thematic_sprawl": "accepted",
        "rollover_401k": "evaluate",
        "frozen_tod_income": "accepted",
        "saa_sleeves_taxable": "accepted",
        "predeploy_stranded_equity": "evaluate",
        "fund_intl_tilts": "evaluate",
    }


def test_no_card_title_asserts_free_or_costly():
    """A card title must not claim a trade is free or costly.

    That determination is DERIVED per render, in pages/14_Asset_Location.py's
    _summary_line: free when every register row for the group is is_free, costly
    otherwise. The page's own glossary fixes the meaning — a trade inside a
    tax-advantaged account is non-taxable and therefore free; a sale in a taxable
    account realizes the embedded gain and is costly.

    A title is an authored literal, so it cannot track the register. "Relocate the
    loss side (free)" survived the Aug-2026 advisor swap that removed HLIPX and
    turned the block from a net loss into a small net GAIN, still labelling the
    card free while the derived badge said costly. It was wrong independently of
    the net, too: the card sells in Individual Taxable (TOD), which the glossary
    calls costly whether the block nets +$7 or -$40. Pinning the derivation rather
    than the sign is what makes this durable against the next external swap.
    """
    offenders = {
        g["key"]: g["title"] for g in ACTION_GROUPS
        if re.search(r"\b(free|costly)\b", g["title"], re.IGNORECASE)
    }
    assert not offenders, (
        "card titles assert a free/costly verdict that only the register can "
        f"determine (see _summary_line): {offenders}"
    )


# ── No two groups render identical prose (guard against templated filler) ───────

def test_no_two_groups_render_identical_prose():
    pos, acct, sec, reg = _live()
    comps, _targets = _live_saa()
    deploy, tier = _live_tier(pos, acct, sec)
    rendered = []
    for g in ACTION_GROUPS:
        resolved = resolve_placeholders(g, pos, acct, sec, reg, roth_idle_cash=deploy["idle_cash"],
                                        compositions_df=comps, tier_state=tier)
        rendered.append(render_prose(g["pros"], resolved))
        rendered.append(render_prose(g["cons"], resolved))
    assert len(set(rendered)) == len(rendered), (
        "two groups rendered identical prose — templated filler, not authored copy"
    )


# ── Every register-backed group resolves to >=1 register row ───────────────────

def test_every_register_backed_group_has_rows():
    _pos, _acct, _sec, reg = _live()
    for g in ACTION_GROUPS:
        if g["key"] in INFORMATIONAL_KEYS:
            continue
        rows = filter_register_for_group(reg, g)
        assert not rows.empty, (
            f"group {g['key']!r} matched zero register rows against the live CSV — config error"
        )


def test_informational_groups_have_no_symbols():
    for g in ACTION_GROUPS:
        if g["key"] in INFORMATIONAL_KEYS:
            assert not g["symbols"], f"{g['key']} is informational and must carry no symbols"


# ── Unresolvable placeholder RAISES rather than rendering $0 ────────────────────

def test_unresolvable_placeholder_raises_not_zero():
    bad_group = {
        "key": "synthetic", "symbols": ["ZZZ_NOT_A_TICKER"],
        "case_filter": ["A"], "accounts": None,
        "pros": "value is {value}", "cons": "",
    }
    pos = pd.DataFrame([{
        "pseudonym": "a", "symbol": "VOO", "current_value": 100.0,
        "total_gain_loss": 10.0, "cost_basis_total": 90.0,
    }])
    acct = pd.DataFrame([{"pseudonym": "a", "display_name": "A", "tax_treatment": "taxable"}])
    sec = pd.DataFrame(columns=["ticker", "sleeve_category"])
    reg = pd.DataFrame(columns=_REGISTER_COLS)

    resolved = resolve_placeholders(bad_group, pos, acct, sec, reg)
    assert resolved["value"] is None, "empty subset must resolve to None, not 0"
    with pytest.raises(ValueError):
        render_prose(bad_group["pros"], resolved)


def test_resolved_zero_is_not_confused_with_unresolvable():
    # A genuinely-present holding with a computed value renders; only *missing*
    # data raises. (Sanity: a real symbol resolves.) population=matched_symbols so
    # {value} measures the held position directly, independent of the register.
    bad_group = {
        "key": "synthetic", "symbols": ["VOO"], "case_filter": None,
        "accounts": None, "population": "matched_symbols",
        "pros": "value is {value}", "cons": "",
    }
    pos = pd.DataFrame([{
        "pseudonym": "a", "symbol": "VOO", "current_value": 100.0,
        "total_gain_loss": 10.0, "cost_basis_total": 90.0,
    }])
    acct = pd.DataFrame([{"pseudonym": "a", "display_name": "A", "tax_treatment": "taxable"}])
    sec = pd.DataFrame(columns=["ticker", "sleeve_category"])
    reg = pd.DataFrame(columns=_REGISTER_COLS)
    resolved = resolve_placeholders(bad_group, pos, acct, sec, reg)
    assert render_prose(bad_group["pros"], resolved) == "value is $100"


def test_roth_cash_prose_does_not_claim_the_balance_is_a_contribution():
    """{value} on the deploy card is a CASH BALANCE, and the prose must not assert
    what put it there.

    It resolves from _roth_idle_cash, which takes the largest cash-sleeve balance
    in any Roth account — a position, not a provenance. Calling it "this year's
    contribution" asserts an identity nothing can check: the repository holds no
    contribution ledger. tracker.db has no contribution column in any table,
    trades.action carries only 'Buy', and there is no contributions seed or CSV.
    The one related field, roth_contribution_basis, is a runtime-profile INPUT
    (pages/15_Liquidity.py), not a record of deposits.

    An idle Roth balance can be a contribution, a dividend that swept to cash, a
    sale awaiting redeployment, or any mixture, and the card cannot tell which. The
    argument for deploying it does not need to know — every day it sits is
    uninvested either way — so the honest sentence says what the number is.
    """
    from src.location_actions import _DEPLOY_ROTH_CASH_PROS
    assert "this year's contribution" not in _DEPLOY_ROTH_CASH_PROS, (
        "the deploy card asserts the idle Roth balance IS this year's contribution; "
        "no contribution record exists anywhere in the repo to support it"
    )
    assert "{value}" in _DEPLOY_ROTH_CASH_PROS, "the balance itself is still stated"


def test_phantom_income_is_attributed_to_tips_not_the_whole_group():
    """Phantom income belongs to the TIPS sleeve, not to all four taxable-SAA holdings.

    The saa_sleeves_taxable card covers PDBC, SCHP, VGIT and VNQ. Only SCHP throws
    phantom income — TIPS are taxed each year on inflation accruals that pay no
    cash until maturity. The other three generate ordinary income and nothing
    phantom: VGIT interest, VNQ non-qualified dividends, and PDBC distributions
    reported on a 1099, which is precisely the structure PDBC exists to provide
    (it is the no-K-1 commodity wrapper).

    The seed already records this correctly and is the source of truth checked
    below, so a card conflating the two contradicts the repo's own data.

    #213 CHANGED THE POLARITY, NOT THE CLAIM. This test used to assert the clause
    "plus the phantom income the TIPS accrue" was PRESENT. That was correct while
    the tips yield was 0.025 — a real yield only — because the accrual then sat
    outside {annual_benefit} and "plus" said so. #213 constructed the yield as
    real + breakeven, so the figure now includes the accrual and "plus" would
    double-count it in prose. The attribution invariant is untouched: the accrual
    still belongs to TIPS alone, and the card must still say which sleeve throws
    it. Only the preposition moved, from "plus" to "including".
    """
    import csv as _csv
    from src.location_actions import _SAA_TAXABLE_PROS

    assert "ordinary income and phantom income" not in _SAA_TAXABLE_PROS, (
        "conflates the two income types across all four holdings; only the TIPS "
        "sleeve throws phantom income"
    )
    assert "including" in _SAA_TAXABLE_PROS and "accruals" in _SAA_TAXABLE_PROS, (
        "the accrual point is still worth making — the figure includes it, and a "
        "reader cannot tell that from a bare ordinary-income sentence"
    )
    clause = _SAA_TAXABLE_PROS[_SAA_TAXABLE_PROS.index("accruals") - 60:]
    assert "TIPS" in clause, (
        f"the accrual clause must name TIPS as its source: {clause[:160]!r}"
    )
    assert "plus the phantom income" not in _SAA_TAXABLE_PROS, (
        "'plus' puts the accrual outside {annual_benefit}, which was true at 0.025 "
        "and is false now that the yield is constructed as real + breakeven"
    )

    # Source of truth: the seed's own per-ticker notes.
    rows = {r["symbol"]: (r.get("notes") or "").lower()
            for r in _csv.DictReader(
                (ROOT / "data" / "seed" / "securities_household.csv").open(encoding="utf-8-sig"))}
    assert "phantom" in rows["SCHP"], "seed no longer attributes phantom income to SCHP"
    assert "phantom" not in rows["PDBC"], "seed now attributes phantom income to PDBC"


def test_foreign_tax_credit_claims_do_not_contradict():
    """No card may claim taxable is "the only wrapper" that can credit foreign tax
    withheld, without qualifying WHICH taxable wrapper.

    The household holds two taxable books — Individual Taxable (Self-Directed) and
    Individual Taxable (TOD) — and both preserve the foreign tax credit, because
    the credit follows the tax treatment, not who directs the account. So an
    unqualified "the only wrapper that can credit the foreign tax withheld" on the
    international-tilts card contradicted the small-cap/EM card, which says holding
    EM in taxable preserves the credit "which an IRA forfeits", and the deploy-Roth
    card, which scopes the forfeit to a Roth.

    What is actually unique about the self-directed book is that it is the only one
    the owner can DIRECT — the TOD book is externally managed and frozen by
    decision (see the frozen-TOD card). That is a control claim, not a tax claim,
    and dropping the qualifier turned a true statement into a false one.
    """
    from src.location_actions import _FUND_INTL_TILTS_PROS
    claim = "the only wrapper that can credit the foreign tax withheld"
    assert claim not in _FUND_INTL_TILTS_PROS, (
        "unqualified foreign-tax-credit exclusivity claim: both taxable books "
        "credit foreign withholding, so this contradicts the small-cap/EM card. "
        "The distinguishing property is control, not tax treatment."
    )
    assert "the only wrapper you control" in _FUND_INTL_TILTS_PROS, (
        "the exclusivity claim must be qualified by control, naming what is "
        "genuinely unique about the self-directed book"
    )


# ── fund_intl_tilts: no dollar literal, all placeholders resolve ────────────────

def test_fund_intl_tilts_prose_no_literal_and_placeholders_resolve():
    """The FTC cost is a live placeholder, never a hardcoded dollar; the group's
    pros/cons/action carry no $-literal (rates only), and every placeholder it
    references resolves against a household with a self-directed book + the rollable
    401(k)."""
    from src.location_actions import (
        ACTION_GROUPS, _INTL_TILT_TARGET_FRACTION, _INTL_FTC_DRAG)
    from src.location_config import ROLLOVER_SOURCE_PSEUDONYM

    g = next(x for x in ACTION_GROUPS if x["key"] == "fund_intl_tilts")
    assert not _DOLLAR_LITERAL.search(g["pros"] + g["cons"] + g["action"]), \
        "fund_intl_tilts prose must carry no hardcoded dollar literal (FTC cost is a placeholder)"

    pos = pd.DataFrame([
        {"pseudonym": "acct_01", "symbol": "VEA", "current_value": 2000.0,
         "total_gain_loss": 0.0, "cost_basis_total": 2000.0},
        {"pseudonym": ROLLOVER_SOURCE_PSEUDONYM, "symbol": "RFUTX", "current_value": 78000.0,
         "total_gain_loss": 0.0, "cost_basis_total": 78000.0},
        {"pseudonym": "acct_taxable_02", "symbol": "IXUS", "current_value": 140000.0,
         "total_gain_loss": 0.0, "cost_basis_total": 140000.0},
    ])
    acct = pd.DataFrame([
        {"pseudonym": "acct_01", "display_name": "Individual Taxable (Self-Directed)", "tax_treatment": "taxable"},
        {"pseudonym": ROLLOVER_SOURCE_PSEUDONYM, "display_name": "MissionSquare 401(k)", "tax_treatment": "workplace_plan"},
        {"pseudonym": "acct_taxable_02", "display_name": "Individual Taxable (TOD)", "tax_treatment": "taxable"},
    ])
    sec = pd.DataFrame(columns=["ticker", "sleeve_category"])
    reg = pd.DataFrame(columns=_REGISTER_COLS)

    resolved = resolve_placeholders(g, pos, acct, sec, reg)
    for ph in ("intl_tilt_target_value", "workplace_plan_value", "self_directed_value", "intl_tilt_ftc_cost"):
        assert resolved[ph] is not None, f"{ph} did not resolve"
    # renders fully (render_prose raises on any None-valued referenced placeholder)
    for text in (g["pros"], g["cons"], g["action"]):
        out = render_prose(text, resolved)
        assert "{" not in out and "}" not in out, "an placeholder was left unsubstituted"

    # the dollar figures are computed live off the household + the two assumptions
    household = float(pos["current_value"].sum())
    assert resolved["intl_tilt_target_value"] == _fmt_dollars(household * _INTL_TILT_TARGET_FRACTION)
    assert resolved["intl_tilt_ftc_cost"] == _fmt_dollars(
        household * _INTL_TILT_TARGET_FRACTION * _INTL_FTC_DRAG)
    assert resolved["self_directed_value"] == _fmt_dollars(2000.0)


# ── Roth deploy answer excludes ineligible sleeves (synthetic + live) ──────────

def _deploy_fixture():
    """A self-contained household for gap-proportional deploy tests.

    $1,000 idle Roth cash; household $7,500. Targets make four eligible equity
    sleeves underweight (US Small Cap, US Large Quality, US Large Value, EM) and one
    OVERWEIGHT (US Large Core, held at $6,000 vs a $1,500 target) so the deploy must
    EXCLUDE the overweight sleeve. Cash ($1,000) < Σgap ($4,750) so no sleeve caps
    and there is no residual. Gap-desc order: AVUV 2250, SPHQ 1500, VTV 750, IEMG 250.
    """
    securities = pd.DataFrame([
        {"ticker": "AVUV",  "sleeve_category": "us_small_value",   "is_in_saa": 1, "asset_class_id": 10},
        {"ticker": "IEMG",  "sleeve_category": "emerging_markets", "is_in_saa": 1, "asset_class_id": 20},
        {"ticker": "SPHQ",  "sleeve_category": "us_large_quality", "is_in_saa": 1, "asset_class_id": 30},
        {"ticker": "VTV",   "sleeve_category": "us_large_value",   "is_in_saa": 1, "asset_class_id": 40},
        {"ticker": "VOO",   "sleeve_category": "us_large_core",    "is_in_saa": 1, "asset_class_id": 50},
        {"ticker": "SPAXX", "sleeve_category": "cash",             "is_in_saa": 1, "asset_class_id": 99},
    ])
    saa_targets = pd.DataFrame([
        {"asset_class_id": 10, "name": "US Small Cap",     "target_weight": 0.30},
        {"asset_class_id": 20, "name": "Emerging Markets", "target_weight": 0.10},
        {"asset_class_id": 30, "name": "US Large Quality", "target_weight": 0.20},
        {"asset_class_id": 40, "name": "US Large Value",   "target_weight": 0.10},
        {"asset_class_id": 50, "name": "US Large Core",    "target_weight": 0.20},
    ])
    compositions = pd.DataFrame(columns=["fund_symbol", "underlying_sleeve", "weight"])
    accounts = pd.DataFrame([
        {"pseudonym": "r", "tax_treatment": "roth_ira", "display_name": "Roth",    "managed_by": "self"},
        {"pseudonym": "t", "tax_treatment": "taxable",  "display_name": "Taxable", "managed_by": "self"},
    ])
    def _p(pseudo, sym, val):
        return {"pseudonym": pseudo, "symbol": sym, "current_value": val,
                "total_gain_loss": float("nan"), "cost_basis_total": float("nan")}
    positions = pd.DataFrame([
        _p("r", "SPAXX", 1000.0),   # idle Roth cash
        _p("t", "VOO",   6000.0),   # us_large_core — overweight ($6k vs $1.5k target)
        _p("t", "IEMG",   500.0),   # emerging_markets — partial ($0.5k vs $0.75k target)
    ])
    return positions, accounts, securities, compositions, saa_targets


def test_deploy_answer_sizes_buys_to_household_gaps():
    # Eligibility is structural (roth-map sleeve with an is_in_saa ticker) and sizing
    # is gap-proportional: each buy = idle_cash × sleeve_gap / Σgap, capped at its gap.
    pos, acct, sec, comp, tgt = _deploy_fixture()
    ans = build_roth_deploy_answer(pos, acct, sec, comp, tgt)
    roth_map = SLEEVE_PRIORITY_BY_ACCOUNT_TYPE["roth_ira"]
    assert set(ans["table"]["sleeve"]).issubset(set(roth_map)), "a non-roth-map sleeve appeared"
    assert ans["idle_cash"] == 1000.0
    # Four underweight sleeves, gap-desc; the OVERWEIGHT us_large_core (VOO) is excluded.
    assert list(ans["table"]["ticker"]) == ["AVUV", "SPHQ", "VTV", "IEMG"]
    assert "VOO" not in set(ans["table"]["ticker"]), "an overweight sleeve must not be a buy target"
    # Each buy equals its gap-proportional share (cash < Σgap → no cap, no residual).
    gaps = household_deploy_gaps(pos, acct, sec, comp, tgt)
    total_gap = float(gaps["gap"].sum())
    for _, g in gaps.iterrows():
        got = float(ans["table"].set_index("ticker").loc[g["ticker"], "dollar"])
        assert got == pytest.approx(1000.0 * g["gap"] / total_gap, abs=0.02), g["ticker"]
    assert ans["table"]["dollar"].sum() == pytest.approx(1000.0, abs=0.05), "buys must sum to idle cash"
    assert ans["residual"] < 1.0, "cash < Σgap → at most sub-dollar rounding, nothing reported"


def test_gap_proportional_split_caps_and_reports_residual():
    """The pure sizer: proportional when cash ≤ Σgap; when cash > Σgap every gap fills
    to its cap and the remainder is residual (never forced into a sleeve); a sleeve
    with no positive gap gets nothing."""
    # cash ≤ Σgap: proportional, no residual.
    allocs, residual = _gap_proportional_split(20.0, [30.0, 10.0])
    assert allocs == [15.0, 5.0] and residual == 0.0
    # cash > Σgap: fill both gaps, report the leftover.
    allocs, residual = _gap_proportional_split(100.0, [30.0, 10.0])
    assert allocs == [30.0, 10.0] and residual == 60.0
    # a non-positive gap receives nothing.
    allocs, residual = _gap_proportional_split(50.0, [40.0, 0.0, -5.0])
    assert allocs == [40.0, 0.0, 0.0] and residual == 10.0
    # nothing underweight → all cash is residual.
    assert _gap_proportional_split(50.0, [0.0, -1.0]) == ([0.0, 0.0], 50.0)


def test_deploy_answer_reports_residual_when_gaps_smaller_than_cash():
    """When the idle cash exceeds the sum of household gaps, every gap fills exactly
    and the leftover is reported as residual — not padded into a sleeve past target."""
    pos, acct, sec, comp, tgt = _deploy_fixture()
    tgt = tgt.copy()
    tgt["target_weight"] = tgt["target_weight"] / 100.0    # gaps now ~$47.50 total ≪ $1,000 cash
    ans = build_roth_deploy_answer(pos, acct, sec, comp, tgt)
    gaps = household_deploy_gaps(pos, acct, sec, comp, tgt)
    # every buy equals its (small) gap exactly …
    for _, g in gaps.iterrows():
        got = float(ans["table"].set_index("ticker").loc[g["ticker"], "dollar"])
        assert got == pytest.approx(g["gap"], abs=0.01), g["ticker"]
    # … and the rest of the idle cash is residual.
    assert ans["residual"] == pytest.approx(1000.0 - float(gaps["gap"].sum()), abs=0.05)
    assert ans["table"]["dollar"].sum() + ans["residual"] == pytest.approx(1000.0, abs=0.05)


def test_deploy_answer_without_targets_returns_idle_cash_only():
    """A caller that omits the SAA tables (needs only idle_cash) gets an empty table
    and the whole idle cash as residual — no flat-split fallback."""
    pos, acct, sec, _comp, _tgt = _deploy_fixture()
    ans = build_roth_deploy_answer(pos, acct, sec)
    assert ans["idle_cash"] == 1000.0
    assert ans["table"].empty
    assert ans["residual"] == 1000.0


def test_ineligible_sleeves_absent_from_roth_map():
    # cash / fixed income / real assets / hedged equity / international are simply
    # not in the roth map, so they can never be a Roth deploy target.
    roth = SLEEVE_PRIORITY_BY_ACCOUNT_TYPE["roth_ira"]
    for s in ("cash", "hedged_equity", "core_fi_treasury", "core_fi_credit", "tips",
              "high_yield_fi", "high_yield_muni", "floating_rate", "multi_sector_fi",
              "real_assets_reit", "real_assets_commodities", "real_assets_gold",
              "intl_developed", "intl_all_exus", "single_stock"):
        assert s not in roth


def test_deploy_answer_live_excludes_all_banned_sleeves():
    from src.household import sleeve_display_name
    pos, acct, sec, _reg = _live()
    comp, tgt = _live_saa()
    ans = build_roth_deploy_answer(pos, acct, sec, comp, tgt)
    sleeves = set(ans["table"]["sleeve"])
    for banned in ("cash", "hedged_equity", "intl_developed", "intl_all_exus",
                   "core_fi_treasury", "tips", "real_assets_reit"):
        assert banned not in sleeves
    # The deploy table is exactly the household-underweight eligible sleeves, gap-desc.
    gaps = household_deploy_gaps(pos, acct, sec, comp, tgt)
    assert list(ans["table"]["ticker"]) == list(gaps["ticker"]), "table must be the underweight gap set"
    assert set(ans["table"]["sleeve"]).issubset(set(SLEEVE_PRIORITY_BY_ACCOUNT_TYPE["roth_ira"]))
    # Ticker AND label from ONE key: e.g. AVUV -> us_small_value -> "US Small Value".
    labels = [sleeve_display_name(s) for s in ans["table"]["sleeve"]]
    assert "US Small Core" not in labels


def test_priority_maps_are_account_conditional():
    from src.location_config import sleeve_priority
    # intl_developed appears for taxable, never for roth. Deliberately the DEVELOPED
    # sleeve, not "international" as a class: emerging_markets is in the roth map at
    # rank 2, accepting the forgone credit for the growth premium (#221).
    assert sleeve_priority("taxable", "intl_developed") == 1
    assert sleeve_priority("roth_ira", "intl_developed") is None
    # hedged_equity appears for traditional_ira, never for roth.
    assert sleeve_priority("traditional_ira", "hedged_equity") == 4
    assert sleeve_priority("roth_ira", "hedged_equity") is None
    # Cash is never a deploy target anywhere.
    for at in ("roth_ira", "hsa", "traditional_ira", "workplace_plan", "taxable"):
        assert sleeve_priority(at, "cash") is None
    # Roth rank 1 = small VALUE (AVUV), not broad small-core.
    assert sleeve_priority("roth_ira", "us_small_value") == 1
    assert sleeve_priority("roth_ira", "us_small_core") == 6


# ── Informational groups render (deploy uses {value}; rollover is literal) ─────

def test_deploy_and_rollover_render():
    pos, acct, sec, reg = _live()
    deploy, tier = _live_tier(pos, acct, sec)
    by_key = {g["key"]: g for g in ACTION_GROUPS}

    d = resolve_placeholders(by_key["deploy_roth_cash"], pos, acct, sec, reg,
                             roth_idle_cash=deploy["idle_cash"], tier_state=tier)
    deploy_pros = render_prose(by_key["deploy_roth_cash"]["pros"], d)
    assert f"${deploy['idle_cash']:,.0f}" in deploy_pros

    r = resolve_placeholders(by_key["rollover_401k"], pos, acct, sec, reg)
    # rollover pros now templates three account-level figures (informational group,
    # so they must resolve from positions_df, not from register rows).
    rollover_pros = render_prose(by_key["rollover_401k"]["pros"], r)
    for key in ("workplace_plan_value", "pretax_capacity", "pretax_capacity_after"):
        assert r[key] is not None, f"{key} must resolve for the informational rollover group"
        assert r[key] in rollover_pros
    # cons carries no placeholders -> renders unchanged.
    assert render_prose(by_key["rollover_401k"]["cons"], r) == by_key["rollover_401k"]["cons"]


# ── Dollar formatting + Markdown escaping (bugs 1 & 2) ─────────────────────────

def test_fmt_dollars_keeps_symbol_and_sign():
    assert _fmt_dollars(51) == "$51"
    assert _fmt_dollars(12345) == "$12,345"   # synthetic value — exercises comma insertion
    assert _fmt_dollars(-51) == "-$51"        # negative is "-$51", never "- 51"
    assert _fmt_dollars(-51.4) == "-$51"


def test_render_prose_md_escapes_every_dollar():
    # Synthetic fixture values — arbitrary strings that exercise the $-escaping path.
    resolved = {"value": "$8,159", "count": "3", "embedded_gain": "-$51", "annual_benefit": "$62"}
    template = "These {count} sit at {embedded_gain}; removing {annual_benefit} on {value}."
    out = render_prose_md(template, resolved)
    # Every "$" is escaped as "\$" — no bare "$" that could open LaTeX math mode.
    assert "\\$" in out, "expected escaped \\$ in rendered markdown"
    assert "$" not in out.replace("\\$", ""), f"bare $ would open math mode: {out!r}"
    # Numbers and sign survive the escaping.
    assert "8,159" in out and "-\\$51" in out and "\\$62" in out


def test_escape_md_leaves_non_dollar_markdown_intact():
    assert escape_md("**For.** costs $780 today") == "**For.** costs \\$780 today"
    assert escape_md("no dollars here") == "no dollars here"


# ── Account directability (bug 5) ──────────────────────────────────────────────

def test_directable_accounts_enumerated():
    assert DIRECTABLE_PSEUDONYMS == frozenset({"acct_01", "acct_roth_01", "acct_trad_ira_01"})
    # Directable: self-directed taxable (acct_01) + both IRAs.
    assert is_directable("acct_01") is True
    assert is_directable("acct_roth_01") is True
    assert is_directable("acct_trad_ira_01") is True
    # Needs coordination: TOD taxable, workplace plans, HSA.
    assert is_directable("acct_taxable_02") is False
    assert is_directable("acct_wkpl_01") is False
    assert is_directable("acct_wkpl_02") is False
    assert is_directable("acct_hsa_01") is False


def test_directability_is_not_managed_by_or_tax_treatment():
    # The Roth/Traditional are externally managed yet directable; the TOD taxable
    # shares tax_treatment with the directable self-directed taxable yet is not.
    assert is_directable("acct_roth_01") and is_directable("acct_trad_ira_01")   # external, but directable
    assert not is_directable("acct_taxable_02")                                  # taxable, but not directable


# ── Bug 1: prose-corruption guards ─────────────────────────────────────────────

def _rendered_all():
    pos, acct, sec, reg = _live()
    comps, _targets = _live_saa()
    dep, tier = _live_tier(pos, acct, sec)
    out = {}
    for g in ACTION_GROUPS:
        r = resolve_placeholders(g, pos, acct, sec, reg, roth_idle_cash=dep["idle_cash"],
                                 compositions_df=comps, tier_state=tier)
        out[g["key"]] = (render_prose_md(g["pros"], r), render_prose_md(g["cons"], r), r, g)
    return out


def test_no_rendered_prose_contains_comma_emdash():
    """A dropped clause renders as valid Markdown ('accepted, — not to fix it') and
    is invisible in review. This is the canary."""
    for key, (pros, cons, _r, _g) in _rendered_all().items():
        assert ", —" not in pros, f"{key} pros contains ', —' (dropped clause): {pros!r}"
        assert ", —" not in cons, f"{key} cons contains ', —' (dropped clause): {cons!r}"


# Exact lengths of the AUTHORED prose — a brittle-on-purpose canary for silent prose
# corruption (a dropped word renders as valid Markdown and is invisible in review).
#
# MEASURED AT FIXED PLACEHOLDER VALUES, not against the live book (#305). Every
# placeholder renders as the sentinel "<name>", so a length moves only when the
# authored prose changes. The pin used to render against the newest positions CSV,
# so it also moved whenever the BOOK moved: it failed on deploy_roth_cash at 496
# once the Sep-23 CSV set the Roth cash to $7.93, and relocate_loss_side's 410 vs
# 408 hid behind that failure, because a loop reports only its first mismatch. The
# deploy card moved to a fixed balance in #312; this applies the same to every card.
# Whether each placeholder RESOLVES in the live render is still checked live by
# test_every_placeholder_resolves_into_rendered_output. Why each card's prose has
# the length it does is in git history, not here.
AUTHORED_PROSE_LEN = {
    "deploy_roth_cash":          (621, 766),
    "clear_roth_non_equity":     (1390, 1479),
    "relocate_loss_side":        (438, 885),
    "relocate_gain_side":        (383, 338),
    "thematic_sprawl":           (208, 772),
    "rollover_401k":             (524, 353),
    "frozen_tod_income":         (408, 508),
    "saa_sleeves_taxable":       (349, 669),
    "predeploy_stranded_equity": (334, 540),
    "fund_intl_tilts":           (936, 1209),
}


def _authored(template: str) -> str:
    return render_prose_md(template, {k: f"<{k}>" for k in re.findall(r"\{(\w+)\}", template)})


def test_authored_prose_lengths_pinned():
    """Every card, not just the first mismatch: collect all drifts, then assert, so
    one card's failure cannot hide another's (the relocate_loss_side lesson)."""
    got = {g["key"]: (len(_authored(g["pros"])), len(_authored(g["cons"])))
           for g in ACTION_GROUPS}
    assert set(got) == set(AUTHORED_PROSE_LEN), "a card was added or removed: pin it"
    drifted = {k: (got[k], AUTHORED_PROSE_LEN[k]) for k in got if got[k] != AUTHORED_PROSE_LEN[k]}
    assert not drifted, (
        "authored prose length changed (got, pinned) — possible silent prose corruption; "
        f"update the pin only for a deliberate edit: {drifted}")


def test_the_authored_pin_does_not_read_the_book():
    """The fixed render must not depend on data: a sentinel for every placeholder,
    none left unresolved, and no dollar figure anywhere."""
    for g in ACTION_GROUPS:
        for field in ("pros", "cons"):
            text = _authored(g[field])
            assert "{" not in text, f"{g['key']}.{field}: an unresolved placeholder"
            assert not re.search(r"\\?\$\d", text), f"{g['key']}.{field}: a live figure leaked in"


def test_every_placeholder_resolves_into_rendered_output():
    ph_re = re.compile(r"\{(\w+)\}")
    for key, (pros, cons, resolved, g) in _rendered_all().items():
        for field, rendered in (("pros", pros), ("cons", cons)):
            for ph in ph_re.findall(g[field]):
                val = resolved.get(ph)
                assert val is not None, f"{key}.{field}: placeholder {{{ph}}} did not resolve"
                assert escape_md(val) in rendered, (
                    f"{key}.{field}: resolved {{{ph}}}={val!r} is absent from the rendered output"
                )


# ── Bug 2: population field ────────────────────────────────────────────────────

def test_matched_symbols_population_groups_are_declared_and_captioned():
    """Exactly the two coverage groups that count more than they list use
    population='matched_symbols', and each MUST carry a caption stating the gap
    (thematic_sprawl: fees/sprawl; frozen_tod_income: the federally-exempt GHYIX,
    counted in the population but never a register row)."""
    matched = {g["key"] for g in ACTION_GROUPS if g.get("population") == "matched_symbols"}
    assert matched == {"thematic_sprawl", "frozen_tod_income"}
    for g in ACTION_GROUPS:
        if g["key"] in matched:
            assert g.get("caption"), f"{g['key']} uses matched_symbols but has no caption"
        else:
            assert g.get("population", "register_rows") == "register_rows"


def test_thematic_population_exceeds_register_rows_and_caption_states_both():
    pos, acct, sec, reg = _live()
    g = next(x for x in ACTION_GROUPS if x["key"] == "thematic_sprawl")
    pop = _pop_holdings(g, pos, acct, reg)
    rows = filter_register_for_group(reg, g)
    assert len(pop) > len(rows), "thematic population must exceed its register-row count"
    cap = resolve_caption(g, pos, acct, reg)
    assert cap, "thematic must render a caption"
    assert str(len(pop)) in cap and str(len(rows)) in cap, "caption must state both counts"


def test_non_thematic_groups_population_byte_identical():
    """Switching the default population to register_rows must not change any group
    whose symbols are all mislocations (their two populations coincide). Excludes the
    two matched_symbols groups, whose populations DELIBERATELY exceed their register
    rows (thematic_sprawl: correctly-located sprawl; frozen_tod_income: exempt GHYIX)."""
    pos, acct, sec, reg = _live()
    for g in ACTION_GROUPS:
        if g["key"] in ("deploy_roth_cash", "rollover_401k", "thematic_sprawl", "frozen_tod_income"):
            continue
        a = _pop_holdings({**g, "population": "register_rows"}, pos, acct, reg)
        b = _pop_holdings({**g, "population": "matched_symbols"}, pos, acct, reg)
        assert len(a) == len(b)
        assert abs(float(a["current_value"].sum()) - float(b["current_value"].sum())) < 1e-9


def test_matched_symbols_without_caption_raises_at_config_load():
    import src.location_actions as la
    bad = {"key": "bad", "population": "matched_symbols", "caption": None, "symbols": ["X"]}
    orig = la.ACTION_GROUPS
    la.ACTION_GROUPS = orig + [bad]
    try:
        with pytest.raises(ValueError):
            validate_action_groups()
    finally:
        la.ACTION_GROUPS = orig


# ── SAA sleeves: sub-threshold backing rows now surface (count == row_count) ─────

def test_saa_sleeves_count_equals_row_count_live():
    """The 'SAA sleeves in taxable' group's four holdings (VGIT/SCHP/PDBC/VNQ) are
    low-efficiency case-A rows whose annual drag is below MIN_ANNUAL_BENEFIT. They
    are register rows and its population counts them, so count MUST equal row_count —
    the expander now shows every one. Pins the 'header says 4, table empty' bug."""
    pos, acct, sec, reg = _live()
    g = next(x for x in ACTION_GROUPS if x["key"] == "saa_sleeves_taxable")
    rows = filter_register_for_group(reg, g)
    pop = _pop_holdings(g, pos, acct, reg)
    assert set(rows["symbol"]) == {"VGIT", "SCHP", "PDBC", "VNQ"}, (
        f"the four SAA-sleeve symbols must all be register rows, got {sorted(set(rows['symbol']))}"
    )
    assert set(rows["case"]) == {"A"}, f"SAA sleeves are case A, got {sorted(set(rows['case']))}"
    assert len(pop) == len(rows) == 4, f"count {len(pop)} must equal row_count {len(rows)} == 4"
    # count == row_count holds regardless of where each row sits relative to
    # MIN_ANNUAL_BENEFIT: the expander shows every backing row whether or not it clears
    # the threshold (at higher ordinary rates some of these sub-$1 sleeves now cross it —
    # the invariant is unaffected). There is no longer a `surfaced` flag to be robust to;
    # the register carries no presentation column at all.


def test_only_matched_symbols_groups_have_a_population_gap_live():
    """After showing all backing rows, the ONLY groups whose count differs from their
    register-row count are the two matched_symbols coverage groups (they count
    correctly-located / federally-exempt holdings that are never register rows). Every
    other group has count == row_count, and each gap-bearing group carries a caption."""
    pos, acct, sec, reg = _live()
    gap = set()
    for g in ACTION_GROUPS:
        if g["key"] in INFORMATIONAL_KEYS:
            continue
        if len(_pop_holdings(g, pos, acct, reg)) != len(filter_register_for_group(reg, g)):
            gap.add(g["key"])
    assert gap == {"thematic_sprawl", "frozen_tod_income"}, f"unexpected population gaps: {gap}"
    for k in gap:
        gg = next(x for x in ACTION_GROUPS if x["key"] == k)
        assert gg.get("caption"), f"{k} has a population gap but no caption"
    # And no group is orphaned (every register row is claimed).
    assert_full_coverage(reg)


def test_page14_renders_value_column_and_saa_rows_live(monkeypatch):
    """End-to-end render on real personal data: the Asset Location page loads without
    exception, every Underlying-positions table carries a 'Value ($)' column, and the
    four SAA-sleeve symbols — previously dropped as sub-threshold — appear in a table."""
    from src.household_data import find_latest_positions_csv
    csv = find_latest_positions_csv()
    if csv is None or not TRACKER_DB.exists() or TRACKER_DB.stat().st_size == 0:
        pytest.skip("personal-mode inputs absent")
    import src.config
    import src.db
    monkeypatch.setattr(src.config, "IS_DEMO", False)     # force the personal-mode branch
    monkeypatch.setattr(src.db, "DB_PATH", TRACKER_DB)     # read the real tracker.db, not demo.db

    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(ROOT / "pages" / "14_Asset_Location.py"), default_timeout=90).run()
    assert not at.exception, f"page raised: {at.exception}"

    value_tables = [df.value for df in at.dataframe
                    if "Value ($)" in list(getattr(df.value, "columns", []))]
    assert value_tables, "no Underlying-positions table carried a 'Value ($)' column"
    shown = set()
    for t in value_tables:
        if "Symbol" in t.columns:
            shown |= set(t["Symbol"])
    assert {"VGIT", "SCHP", "PDBC", "VNQ"} <= shown, (
        f"SAA-sleeve rows missing from rendered tables; shown symbols: {sorted(shown)}"
    )


# ── Bug 3: no hardcoded headroom / dollar literals ─────────────────────────────

_DOLLAR_LITERAL = re.compile(r"\$[\d,]")


def test_gain_side_prose_is_capacity_framed_no_literal():
    """The gain-side card is capacity-framed (deferred on pre-tax room), not
    headroom-framed. The cons no longer restates the IRA capacity directly —
    it cross-references the consolidated "What happens in the Traditional IRA"
    summary in clear_roth_non_equity's cons via {group_2_title} — but no $
    figure in pros/cons/action is a literal, and the stale 0%-headroom
    language is gone."""
    g4 = next(g for g in ACTION_GROUPS if g["key"] == "relocate_gain_side")
    blob = g4["pros"] + g4["cons"] + g4["action"]
    assert not _DOLLAR_LITERAL.search(blob), f"group 4 must have no $ literal: {blob!r}"
    assert "{group_2_title}" in g4["cons"], "gain-side cons must cross-reference the Traditional IRA summary"
    assert "headroom" not in blob.lower(), "stale 0%-headroom language must be gone"
    for ph in ("{headroom_total}", "{headroom_consumed}", "{headroom_remaining}"):
        assert ph not in blob, f"{ph} must be gone from the gain-side prose"


def test_gain_side_action_shows_computed_cost_relief_payback_live():
    """The gain-side action states the live cost and relief: cost == Σ cost_to_realize
    of its register rows, relief == Σ annual_benefit, both rendered, plus a payback and
    the defer-to-rollover framing."""
    pos, acct, sec, reg = _live()
    g4 = next(g for g in ACTION_GROUPS if g["key"] == "relocate_gain_side")
    rows = filter_register_for_group(reg, g4)
    action = render_prose_md(g4["action"], resolve_placeholders(g4, pos, acct, sec, reg))
    assert escape_md(_fmt_dollars(float(rows["cost_to_realize"].sum()))) in action, "action must show the real cost"
    assert escape_md(_fmt_dollars(float(rows["annual_benefit"].sum()))) in action, "action must show the real relief"
    assert "payback" in action.lower() and "rollover" in action.lower()


def test_no_dollar_literals_except_allow_literals_groups():
    for g in ACTION_GROUPS:
        has_lit = bool(_DOLLAR_LITERAL.search(g["pros"])) or bool(_DOLLAR_LITERAL.search(g["cons"]))
        if g.get("allow_literals"):
            continue
        assert not has_lit, f"group {g['key']!r} has an un-allowed dollar literal in its prose"


def test_no_group_carries_allow_literals():
    """No group is exempt from the dollar-literal guard any more.

    thematic_sprawl was the last holdout, exempted for its "$76 a year" excess-fee
    estimate. That figure had no source — securities.expense_ratio is NULL for all
    16 thematic tickers, so neither it nor the 0.45%/0.07% rates it derived from
    could be computed — and it was wrong by its own arithmetic besides (38 bps on
    the live book is ~$58). Deleting the false precision retired the exemption
    with it, so every group's prose is now covered by
    test_no_dollar_literals_except_allow_literals_groups.

    Re-adding the flag is a deliberate act: it silently removes a card from that
    guard, which is how "$76" survived long enough to go stale unnoticed.
    """
    import src.location_actions as la
    allowed = [g["key"] for g in ACTION_GROUPS if g.get("allow_literals")]
    assert not allowed, (
        f"groups exempt from the dollar-literal guard: {allowed}. Prefer a "
        "placeholder; if a literal is genuinely unavoidable, say why it cannot be "
        "computed rather than exempting the card."
    )
    src = pathlib.Path(la.__file__).read_text(encoding="utf-8")
    lit_lines = [ln for ln in src.splitlines() if re.search(r'"allow_literals":\s*True', ln)]
    assert not lit_lines, f"allow_literals reintroduced in config: {lit_lines}"


# ── Percent literals: rates may be authored, portfolio shares may not ──────────
#
# The dollar guard above can be blanket (no card needs to author a dollar figure).
# Percents cannot: eight cards carry legitimate RATE literals — tax rates from
# TAX_PROFILE, an SAA target weight, the international structural assumptions —
# and a blanket regex would fire on every one of them, which is how you end up
# deleting the guard instead of the defect.
#
# The distinguishing property is not the value, it is what the number measures.
# A rate, target, or assumption is authored config and may be a literal. A
# MEASUREMENT OF THE LIVE BOOK may not: it goes stale the moment prices move,
# which is exactly what "11.8% of your equity" did (it read 11.5% by the time
# anyone checked, and nothing in the repo could recompute it).
#
# So the guard is an inventory pin, brittle-on-purpose in the same way
# AUTHORED_PROSE_LEN is: every percent literal in every card's pros/cons/action,
# enumerated with what sources it. A new percent literal fails this test until
# someone writes down which of the two kinds it is — and a portfolio share has
# no honest entry to write, because it has to be a placeholder instead.
_PERCENT_LITERAL = re.compile(r"\d+(?:\.\d+)?\s?%")

PERCENT_LITERALS = {
    # TAX_PROFILE: federal_ltcg 0.15 + state_ltcg 0.0307 = 18.07%; "0%" is the
    # LTCG_0_BRACKET_CEILING_SINGLE_2026 bracket the card says is out of reach.
    "relocate_gain_side":         {"18.07%", "15%", "3.07%", "0%"},
    "frozen_tod_income":          {"18.07%", "15%", "3.07%", "0%"},
    # Same tax rates. The 11.8% book share that used to sit here is now
    # {thematic_equity_share} — computed household-wide over look-through equity.
    "thematic_sprawl":            {"18.07%", "15%", "3.07%", "0%"},
    # US Large Core's SAA target weight (asset_classes.target_weight), which the
    # rebuy widens the measured overweight against.
    "clear_roth_non_equity":      {"17.35%"},
    # 20%: the ex-cash international region behind _INTL_TILT_TARGET_FRACTION.
    # ~3% intl dividend yield x ~15% effective foreign withholding: the two
    # structural assumptions _INTL_FTC_DRAG is built from.
    "fund_intl_tilts":            {"20%", "15%", "3%"},
    "deploy_roth_cash":           set(),
    "relocate_loss_side":         set(),
    "rollover_401k":              set(),
    "saa_sleeves_taxable":        set(),
    "predeploy_stranded_equity":  set(),
}


def test_percent_literals_pinned_to_rates_never_portfolio_shares():
    for g in ACTION_GROUPS:
        found = set()
        for field in ("pros", "cons", "action"):
            if g.get(field):
                found |= set(_PERCENT_LITERAL.findall(g[field]))
        assert found == PERCENT_LITERALS[g["key"]], (
            f"{g['key']}: percent literals drifted from the pin — got {sorted(found)}, "
            f"pinned {sorted(PERCENT_LITERALS[g['key']])}. A rate/target/assumption "
            "belongs in the pin with its source named; a figure measuring the live "
            "book belongs in a placeholder, not in prose."
        )


def test_thematic_book_share_is_a_placeholder_in_prose_and_action():
    """The card's own weight is measured, not authored — in BOTH the cons paragraph
    and the one-line action, which is the copy most likely to be skimmed and the
    place the stale 11.8% survived longest."""
    g = next(x for x in ACTION_GROUPS if x["key"] == "thematic_sprawl")
    assert "{thematic_equity_share}" in g["cons"], "cons must template the book share"
    assert "{thematic_equity_share}" in g["action"], "the action line must template it too"
    for field in ("pros", "cons", "action"):
        assert "11.8" not in g[field], f"the authored book share is back in {field}"
    # Module-wide, excluding comments: the note explaining WHY 11.8% was deleted has
    # to be able to quote it, but no prose string may carry it again.
    import src.location_actions as la
    src = pathlib.Path(la.__file__).read_text(encoding="utf-8")
    code = [ln for ln in src.splitlines() if not ln.lstrip().startswith("#")]
    assert not any("11.8%" in ln for ln in code), (
        "the authored 11.8% book share is back in a prose string"
    )


# ── Templated account-level literals (this PR) ─────────────────────────────────

def test_household_placeholders_resolve_from_positions():
    """Every account-level placeholder resolves to a formatted dollar string, and
    pretax_capacity_after is the sum of the Traditional IRA capacity and the
    workplace-plan value. Invariants against live data — no hardcoded real figure.
    (Skips without personal inputs, so it runs on the desktop, not in CI.)"""
    import re
    from src.location_actions import _household_placeholders
    pos, acct, sec, _reg = _live()   # skips without the personal CSV + tracker.db
    hp = _household_placeholders(pos, acct, sec)
    for k in ("trad_ira_equity", "pretax_capacity", "workplace_plan_value", "pretax_capacity_after"):
        assert re.fullmatch(r"\$[\d,]+", hp[k]), f"{k} not a formatted $ value: {hp[k]!r}"
    _d = lambda s: int(s.replace("$", "").replace(",", ""))
    assert _d(hp["pretax_capacity_after"]) == _d(hp["pretax_capacity"]) + _d(hp["workplace_plan_value"]), (
        f"pretax_capacity_after {hp['pretax_capacity_after']!r} != capacity + workplace_plan"
    )


def test_trad_ira_equity_excludes_non_equity_sleeves():
    """Equity capacity is equity sleeves only — bond/real-asset holdings in the
    Traditional IRA must not inflate it (enumerated, not substring-inferred)."""
    from src.location_actions import _household_placeholders, _fmt_dollars
    from src.location_config import EQUITY_SLEEVES
    pos, acct, sec, _reg = _live()
    tt = acct.set_index("pseudonym")["tax_treatment"].to_dict()
    trad = pos[pos["pseudonym"].map(tt) == "traditional_ira"].merge(
        sec[["ticker", "sleeve_category"]], left_on="symbol", right_on="ticker", how="left")
    total = float(trad["current_value"].sum())
    equity = float(trad[trad["sleeve_category"].isin(EQUITY_SLEEVES)]["current_value"].sum())
    assert equity < total, "the Traditional IRA holds non-equity that must be excluded"
    hp = _household_placeholders(pos, acct, sec)
    assert hp["trad_ira_equity"] == _fmt_dollars(equity)


def test_group3_and_group6_templated_not_literal():
    g3 = next(g for g in ACTION_GROUPS if g["key"] == "relocate_loss_side")
    g6 = next(g for g in ACTION_GROUPS if g["key"] == "rollover_401k")
    assert not _DOLLAR_LITERAL.search(g3["pros"] + g3["cons"]), "group 3 must have no $ literal"
    assert not _DOLLAR_LITERAL.search(g6["pros"] + g6["cons"]), "group 6 must have no $ literal"
    # group 3's capacity restatement was consolidated into group 2's cons (the
    # "What happens in the Traditional IRA" summary); group 3 now cross-references
    # it by title instead of re-templating {trad_ira_equity} itself.
    assert "{group_2_title}" in g3["cons"]
    for ph in ("{workplace_plan_value}", "{pretax_capacity}", "{pretax_capacity_after}"):
        assert ph in g6["pros"]
    assert not g3.get("allow_literals") and not g6.get("allow_literals")


# ── {workplace_plan_value} is a definition, not an argmax (this PR) ─────────────

def test_workplace_plan_value_selects_by_pseudonym_not_largest():
    """A SECOND workplace_plan account larger than the roll source must NOT change
    {workplace_plan_value}. This is what distinguishes a definition from an argmax
    that happens to be correct."""
    from src.location_actions import _household_placeholders
    from src.location_config import ROLLOVER_SOURCE_PSEUDONYM
    acct = pd.DataFrame([
        {"pseudonym": ROLLOVER_SOURCE_PSEUDONYM, "display_name": "Workplace Plan",  "tax_treatment": "workplace_plan"},
        {"pseudonym": "acct_wkpl_bigger",        "display_name": "Bigger Plan",     "tax_treatment": "workplace_plan"},
        {"pseudonym": "acct_trad_ira_01",        "display_name": "Traditional IRA", "tax_treatment": "traditional_ira"},
    ])
    pos = pd.DataFrame([
        {"pseudonym": ROLLOVER_SOURCE_PSEUDONYM, "symbol": "RFUTX", "current_value": 77_000.0,
         "total_gain_loss": float("nan"), "cost_basis_total": float("nan")},
        {"pseudonym": "acct_wkpl_bigger", "symbol": "BIGX", "current_value": 200000.0,   # bigger than the 401k
         "total_gain_loss": 0.0, "cost_basis_total": 0.0},
        {"pseudonym": "acct_trad_ira_01", "symbol": "VOO", "current_value": 10000.0,
         "total_gain_loss": 0.0, "cost_basis_total": 10000.0},
    ])
    sec = pd.DataFrame([
        {"ticker": "RFUTX", "sleeve_category": "target_date"},
        {"ticker": "BIGX",  "sleeve_category": "target_date"},
        {"ticker": "VOO",   "sleeve_category": "us_large_core"},
    ])
    hp = _household_placeholders(pos, acct, sec)
    assert hp["workplace_plan_value"] == "$77,000", (
        f"argmax leak — a larger second workplace account changed the figure: {hp['workplace_plan_value']}"
    )
    # after stays derived: pretax_capacity ($10,000) + workplace_plan_value ($77,000)
    assert hp["pretax_capacity"] == "$10,000"
    assert hp["pretax_capacity_after"] == "$87,000"


def test_unresolvable_rollover_source_raises_no_fallback():
    """If ROLLOVER_SOURCE_PSEUDONYM has no positions, the figure is unresolvable and
    group 6's prose must RAISE — never fall back to another workplace account."""
    from src.location_actions import _household_placeholders
    acct = pd.DataFrame([
        {"pseudonym": "acct_wkpl_other", "display_name": "Other Plan",      "tax_treatment": "workplace_plan"},
        {"pseudonym": "acct_trad_ira_01","display_name": "Traditional IRA", "tax_treatment": "traditional_ira"},
    ])
    pos = pd.DataFrame([
        {"pseudonym": "acct_wkpl_other",  "symbol": "BIGX", "current_value": 50000.0, "total_gain_loss": 0.0, "cost_basis_total": 0.0},
        {"pseudonym": "acct_trad_ira_01", "symbol": "VOO",  "current_value": 10000.0, "total_gain_loss": 0.0, "cost_basis_total": 10000.0},
    ])
    sec = pd.DataFrame([{"ticker": "BIGX", "sleeve_category": "target_date"}, {"ticker": "VOO", "sleeve_category": "us_large_core"}])
    hp = _household_placeholders(pos, acct, sec)
    assert hp["workplace_plan_value"] is None, "must not fall back to another workplace account"
    assert hp["pretax_capacity_after"] is None, "derived-from-None stays None"

    g6 = next(g for g in ACTION_GROUPS if g["key"] == "rollover_401k")
    reg = pd.DataFrame(columns=_REGISTER_COLS)
    resolved = resolve_placeholders(g6, pos, acct, sec, reg)
    with pytest.raises(ValueError):
        render_prose(g6["pros"], resolved)


# ── Coverage guard: every register row belongs to >=1 group (this PR) ───────────

def test_coverage_guard_passes_over_live_register():
    """assert_full_coverage must not raise: after groups 7-9, every one of the
    live register's rows is claimed by some action group (zero orphans)."""
    _pos, _acct, _sec, reg = _live()
    assert_full_coverage(reg)   # raises on any orphan


def test_coverage_guard_raises_and_names_the_orphan():
    """Dropping a coverage group must orphan its rows and make the guard RAISE,
    naming the uncovered symbol — this is the whole point of the guard."""
    import src.location_actions as la
    _pos, _acct, _sec, reg = _live()
    orig = la.ACTION_GROUPS
    la.ACTION_GROUPS = [g for g in orig if g["key"] != "saa_sleeves_taxable"]
    try:
        with pytest.raises(ValueError) as ei:
            la.assert_full_coverage(reg)
        assert "VGIT" in str(ei.value), "guard must name the now-orphaned SAA sleeves"
    finally:
        la.ACTION_GROUPS = orig


# ── Item 5: thematic is not a Roth deploy target ───────────────────────────────

def test_thematic_absent_from_roth_and_hsa_priority_maps():
    for acct_type in ("roth_ira", "hsa"):
        assert "thematic" not in SLEEVE_PRIORITY_BY_ACCOUNT_TYPE[acct_type], (
            f"thematic must not be a deploy target in {acct_type}"
        )


def test_thematic_never_in_a_roth_deploy_answer():
    _pos, _acct, _sec, _reg = _live()
    _comp, _tgt = _live_saa()
    ans = build_roth_deploy_answer(_pos, _acct, _sec, _comp, _tgt)
    assert "thematic" not in set(ans["sleeves"]), "thematic leaked into the Roth deploy sleeves"
    assert "thematic" not in set(ans["table"]["sleeve"]), "thematic leaked into the Roth deploy table"


# ── Item 2: group 4 cons is headroom-correct (no 15% claim, no self-reference) ──

def test_gain_side_cons_frames_capacity_and_the_rollover():
    """The gain-side cons defers on capacity (not headroom): it names the pre-tax
    capacity constraint and ties it to the 401(k) rollover that unblocks it."""
    g4 = next(g for g in ACTION_GROUPS if g["key"] == "relocate_gain_side")
    cons = g4["cons"].lower()
    assert "capacity" in cons and "rollover" in cons, "cons must frame capacity + the rollover"
    assert "headroom" not in cons, "no stale 0%-headroom language"


# ── Item 3: group 3 cross-references group 2 by its LIVE title, not a hardcode ──

def test_loss_side_cons_references_current_group2_title():
    pos, acct, sec, reg = _live()
    dep = build_roth_deploy_answer(pos, acct, sec)
    g3 = next(g for g in ACTION_GROUPS if g["key"] == "relocate_loss_side")
    assert "{group_2_title}" in g3["cons"], "group 3 must template group 2's title, not hardcode it"
    cons = render_prose_md(g3["cons"], resolve_placeholders(g3, pos, acct, sec, reg, roth_idle_cash=dep["idle_cash"]))
    assert _group_title("clear_roth_non_equity") in cons, "rendered group 3 cons must show group 2's live title"


# ── Roth-cleanup card names VOO as the Roth equity rebuy (1:1, not gap-sized) ───

def test_clear_roth_names_voo_and_templates_the_rebuy_figure():
    """Config/template guard (runs in CI, no live data): the Roth-cleanup card carries
    the hedged-equity rebuy subset and both its action and For prose name VOO and
    template the {roth_equity_rebuy} figure — never a hardcoded dollar or a ticker-less
    'broad equity'. The subset is a strict subset of the 7 sold symbols (equity only).

    VOO, not VTI: VOO is US Large Core's is_in_saa ticker, so the rebuy lands in an
    SAA sleeve the framework can count. VTI is in no sleeve at all, so the same buy
    would land off-SAA. The card's For prose must weigh that tradeoff rather than
    carry the old pro-VTI 'total-market, not the S&P 500' rationale, which argued
    the opposite call."""
    g = next(x for x in ACTION_GROUPS if x["key"] == "clear_roth_non_equity")
    assert g["equity_rebuy_symbols"] == ["JEPQ", "JEPI", "JHEQX", "HELO"]
    assert set(g["equity_rebuy_symbols"]) < set(g["symbols"]), "rebuy subset must be equity-only, a subset of the 7"
    assert "{roth_equity_rebuy}" in g["action"] and "{roth_equity_rebuy}" in g["pros"]
    assert "VOO" in g["action"] and "VOO" in g["pros"]
    assert "total-market, not the S&P 500" not in g["pros"], "the superseded pro-VTI rationale must be gone"
    assert "broad equity there" not in g["action"], "the ticker-less 'broad equity' clause must be replaced"
    assert not _DOLLAR_LITERAL.search(g["action"]) and not _DOLLAR_LITERAL.search(g["pros"]), "no $ literal"


def test_clear_roth_rebuy_sized_1to1_to_hedged_equity_live():
    """The VOO rebuy is sized 1:1 to the hedged, covered-call equity sold
    (JEPQ/JEPI/JHEQX/HELO) in the Roth — a location move, the equity portion only,
    NOT the whole card value and NOT gap-sized. The figure templates from live rows."""
    pos, acct, sec, reg = _live()
    dep = build_roth_deploy_answer(pos, acct, sec)
    g = next(x for x in ACTION_GROUPS if x["key"] == "clear_roth_non_equity")
    r = resolve_placeholders(g, pos, acct, sec, reg, roth_idle_cash=dep["idle_cash"])
    roth_pseudos = set(acct[acct["display_name"] == "Roth IRA"]["pseudonym"])
    hedged = pos[(pos["symbol"].isin(g["equity_rebuy_symbols"])) & (pos["pseudonym"].isin(roth_pseudos))]
    assert not hedged.empty, "expected the hedged-equity lots in the Roth"
    assert r["roth_equity_rebuy"] == _fmt_dollars(float(hedged["current_value"].sum())), "rebuy must equal Σ hedged equity"
    assert r["roth_equity_rebuy"] != r["value"], "rebuy is the equity portion, strictly less than the whole card"
    action = render_prose_md(g["action"], r)
    pros = render_prose_md(g["pros"], r)
    assert "VOO" in action and "VOO" in pros
    assert r["roth_equity_rebuy"] in action, "action must show the sized figure"
    assert "location move" in pros and "overweight" in pros, "why/location-move framing must render"


# ── Clarity + structure pass: action lines, rename, prominent captions ─────────

def test_every_group_declares_a_nonempty_action_line():
    for g in ACTION_GROUPS:
        assert isinstance(g.get("action"), str) and g["action"].strip(), (
            f"{g['key']} must declare a one-line action decision"
        )


def test_action_lines_have_no_dollar_literal_except_allow_literals():
    """The bold action line is templated like the prose: dollar figures resolve from
    placeholders, never a hardcoded literal (allow_literals groups exempt, same rule
    as pros/cons)."""
    for g in ACTION_GROUPS:
        if g.get("allow_literals"):
            continue
        assert not _DOLLAR_LITERAL.search(g["action"]), (
            f"{g['key']} action has a $ literal — template it: {g['action']!r}"
        )


def test_stranded_equity_renamed_to_plain_language():
    """'Pre-deploy stranded equity' was jargon. The KEY is unchanged (scores/coverage
    tests key on it); only the human-facing title is plain language now."""
    g = next(x for x in ACTION_GROUPS if x["key"] == "predeploy_stranded_equity")
    assert g["title"] == "Taxable small-cap/EM — already handled"
    low = g["title"].lower()
    assert "stranded" not in low and "pre-deploy" not in low


def test_gap_captions_lead_with_prominent_coverage_phrasing():
    """The only groups with a real count>rows gap are the two matched_symbols groups,
    and each states BOTH the population value AND the shown-rows value: 'This group
    covers {population_count} ... {row_count} appear below (worth {shown_value}) —
    {reason}'. The {shown_value} figure lets the caption match the table's Value total
    instead of contradicting it. SAA sleeves (count==row_count) carries no caption."""
    gapped = {g["key"] for g in ACTION_GROUPS if g.get("population") == "matched_symbols"}
    assert gapped == {"thematic_sprawl", "frozen_tod_income"}
    for g in ACTION_GROUPS:
        if g["key"] in gapped:
            cap = g["caption"]
            assert cap.startswith("This group covers "), f"{g['key']} caption not prominent: {cap!r}"
            for ph in ("{population_count}", "{population_value}", "{row_count}", "{shown_value}"):
                assert ph in cap, f"{g['key']} caption missing {ph}: {cap!r}"
    saa = next(x for x in ACTION_GROUPS if x["key"] == "saa_sleeves_taxable")
    assert not saa.get("caption"), "SAA sleeves (count==row_count) must carry no caption"


def test_gap_caption_shown_value_equals_register_rows_value_live():
    """Fix 1: each population-gap caption's {shown_value} equals the value of the rows
    it shows — i.e. the expander's Value total row — so the caption's second figure
    matches the table (no more $17,526-in-caption vs $13,005-in-table contradiction)."""
    pos, acct, sec, reg = _live()
    for g in ACTION_GROUPS:
        if g.get("population") != "matched_symbols":
            continue
        rr = filter_register_for_group(reg, g)
        shown = _fmt_dollars(float(rr["current_value"].sum()))   # == the table's Value total
        cap = resolve_caption(g, pos, acct, reg)
        assert cap is not None, f"{g['key']}: expected a caption"
        assert f"(worth {escape_md(shown)})" in cap, (
            f"{g['key']}: caption must state shown value {shown} (= table Value total): {cap!r}"
        )


def test_deploy_targets_split_sources_tickers_and_amount_from_answer():
    """deploy_targets_split reads the computed deploy table (never hardcodes tickers):
    targets are the table's tickers grammatically joined, and deploy_largest is the
    ticker taking the biggest buy (deepest underweight); an empty answer degrades to
    None so the page can fall back rather than raise."""
    pos, acct, sec, _reg = _live()
    comp, tgt = _live_saa()
    dep = build_roth_deploy_answer(pos, acct, sec, comp, tgt)
    dts = deploy_targets_split(dep)
    tickers = dep["table"]["ticker"].tolist()
    for t in tickers:
        assert t in dts["deploy_targets"], f"{t} missing from joined targets"
    # deploy_largest is the ticker with the largest dollar buy.
    assert dts["deploy_largest"] == dep["table"].loc[dep["table"]["dollar"].idxmax(), "ticker"]
    empty = {"table": pd.DataFrame(columns=["ticker", "sleeve", "dollar"])}
    assert deploy_targets_split(empty) == {"deploy_targets": None, "deploy_largest": None}


def test_every_group_action_line_renders_live():
    """Each action line resolves fully against live data — no unresolved {placeholder}
    reaches the page. The deploy line's injected {deploy_targets}/{deploy_split} are
    added exactly as the page does it."""
    pos, acct, sec, reg = _live()
    comp, tgt = _live_saa()
    dep = build_roth_deploy_answer(pos, acct, sec, comp, tgt)
    for g in ACTION_GROUPS:
        r = resolve_placeholders(g, pos, acct, sec, reg, roth_idle_cash=dep["idle_cash"],
                                 compositions_df=comp)
        if g["key"] == "deploy_roth_cash":
            r = {**r, **deploy_targets_split(dep)}
        rendered = render_prose_md(g["action"], r)   # raises on any unresolved placeholder
        assert rendered and "\n" not in rendered
        assert "{" not in rendered and "}" not in rendered, (
            f"{g['key']} action left an unresolved placeholder: {rendered!r}"
        )


def test_page14_action_lines_and_prominent_captions_live(monkeypatch):
    """Rendered page: bold action lines appear in markdown, and the gap captions
    render as prominent markdown — NOT a muted st.caption."""
    from src.household_data import find_latest_positions_csv
    csv = find_latest_positions_csv()
    if csv is None or not TRACKER_DB.exists() or TRACKER_DB.stat().st_size == 0:
        pytest.skip("personal-mode inputs absent")
    import src.config
    import src.db
    monkeypatch.setattr(src.config, "IS_DEMO", False)
    monkeypatch.setattr(src.db, "DB_PATH", TRACKER_DB)
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(ROOT / "pages" / "14_Asset_Location.py"), default_timeout=90).run()
    assert not at.exception, f"page raised: {at.exception}"
    md = " ||| ".join(m.value for m in at.markdown)
    caps = " ||| ".join(c.value for c in at.caption)
    # The deploy action line follows the live balance's tier (#309): below the floor
    # it is the below-floor line, otherwise the gap-proportional deploy line.
    pos, acct, sec, _reg = _live()
    _dep, tier = _live_tier(pos, acct, sec)
    deploy_line = ("No buy proposed" if tier["tier"] == "below_floor"
                   else "sized to its household underweight gap")
    for snippet in (deploy_line, "keeps the Traditional IRA empty",
                    "already builds these positions", "belong in a shelter"):
        assert snippet in md, f"action line missing from render: {snippet!r}"
    assert "This group covers" in md, "gap caption must render as prominent markdown"
    assert "This group covers" not in caps, "gap caption must NOT be a muted st.caption"


def test_page14_renders_status_bucket_headers_live(monkeypatch):
    """Status grouping is made visible: each status bucket renders a section header
    (st.header), in act_now -> evaluate -> blocked -> accepted order, above its cards."""
    from src.household_data import find_latest_positions_csv
    csv = find_latest_positions_csv()
    if csv is None or not TRACKER_DB.exists() or TRACKER_DB.stat().st_size == 0:
        pytest.skip("personal-mode inputs absent")
    import src.config
    import src.db
    monkeypatch.setattr(src.config, "IS_DEMO", False)
    monkeypatch.setattr(src.db, "DB_PATH", TRACKER_DB)
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(ROOT / "pages" / "14_Asset_Location.py"), default_timeout=90).run()
    assert not at.exception, f"page raised: {at.exception}"
    headers = [h.value for h in at.header]
    assert headers == ["Act now", "Evaluate", "Blocked", "Accepted"], (
        f"status bucket headers missing or out of order: {headers}"
    )


def test_page14_evaluate_bucket_blurb_describes_its_cards_live(monkeypatch):
    """The evaluate bucket's blurb must not claim its cards turn on this year's income.

    The blurb is a static per-bucket string, so it asserts one reason for every card
    that lands in the bucket. None of the four now there depends on this year's
    income: relocate_loss_side survives on a structural credit-risk argument
    ("nothing expires, no window closes"), rollover_401k is a destination choice
    available now, predeploy_stranded_equity is a foreign-tax-credit argument for
    leaving things alone, and fund_intl_tilts turns on pre-tax capacity. The income
    claim is a leftover from when the bucket held gain-harvesting cards that did
    turn on the 0% bracket.

    A static blurb can only carry what is true of the BUCKET, not of whichever card
    happens to sit in it — which for "evaluate" is that a decision is wanted and no
    deadline forces it.
    """
    from src.household_data import find_latest_positions_csv
    csv = find_latest_positions_csv()
    if csv is None or not TRACKER_DB.exists() or TRACKER_DB.stat().st_size == 0:
        pytest.skip("personal-mode inputs absent")
    import src.config
    import src.db
    monkeypatch.setattr(src.config, "IS_DEMO", False)
    monkeypatch.setattr(src.db, "DB_PATH", TRACKER_DB)
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(ROOT / "pages" / "14_Asset_Location.py"), default_timeout=90).run()
    assert not at.exception, f"page raised: {at.exception}"
    caps = " ||| ".join(c.value for c in at.caption)
    assert "depends on this year's income" not in caps, (
        "the evaluate bucket blurb claims its cards turn on this year's income; "
        "none of the four cards in that bucket does"
    )
    assert "nothing expires" in caps, (
        "the evaluate bucket blurb must state what is true of the bucket itself — "
        "a decision is wanted and no deadline forces it"
    )


# ── Final polish: totals, labelled figures, case-C footnote, act-now summary ────

def test_gain_side_action_templates_cost_relief_payback():
    """Group 4's action is now a capacity-defer: it templates the realization COST and
    the annual RELIEF (each labelled, no bare figures) plus the payback, and no $
    literal. Guards against reverting to hardcoded or unlabelled figures."""
    g4 = next(g for g in ACTION_GROUPS if g["key"] == "relocate_gain_side")
    a = g4["action"]
    assert "{cost_to_realize}" in a and "{annual_benefit}" in a and "{payback}" in a, (
        f"gain-side action must template cost, relief, and payback: {a!r}"
    )
    assert not _DOLLAR_LITERAL.search(a), f"no $ literal in gain-side action: {a!r}"
    assert "defer" in a.lower() and "rollover" in a.lower()


def test_page14_value_tables_total_and_summaries_live(monkeypatch):
    """Final-polish render checks: every Value ($) expander table ends in a 'Total'
    row equal to the sum of its rows; the Act-now actionable-dollars summary and the
    case-C footnote render; and group 4's action shows both value and gain."""
    from src.household_data import find_latest_positions_csv
    csv = find_latest_positions_csv()
    if csv is None or not TRACKER_DB.exists() or TRACKER_DB.stat().st_size == 0:
        pytest.skip("personal-mode inputs absent")
    import src.config
    import src.db
    monkeypatch.setattr(src.config, "IS_DEMO", False)
    monkeypatch.setattr(src.db, "DB_PATH", TRACKER_DB)
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(ROOT / "pages" / "14_Asset_Location.py"), default_timeout=90).run()
    assert not at.exception, f"page raised: {at.exception}"

    # 1. every Value ($) table totals its Value column in a trailing 'Total' row
    value_tables = [d.value for d in at.dataframe if "Value ($)" in list(d.value.columns)]
    assert value_tables, "no Value ($) tables rendered"
    for t in value_tables:
        assert str(t.iloc[-1]["Holding"]) == "Total", f"no Total row: {list(t['Holding'])}"
        body = t.iloc[:-1]
        assert abs(float(body["Value ($)"].sum()) - float(t.iloc[-1]["Value ($)"])) < 0.5, (
            "Total row Value must equal the sum of the rows above it"
        )

    md = " ||| ".join(m.value for m in at.markdown)
    caps = " ||| ".join(c.value for c in at.caption)
    # 2. Act-now actionable-dollars summary (three computed figures)
    assert "Deploy" in md and "in-shelter (free)" in md, (
        "Act-now actionable-dollars summary line is missing"
    )
    assert "harvest the loss side" not in md, (
        "the loss-side harvest clause must not render: relocate_loss_side is evaluate (Aug-2026)"
    )
    # 3. case-C footnote (subtle st.caption, present)
    assert "repositioning value, not tax drag" in caps, "case-C footnote missing"
    # 4. group 4's action is the capacity-defer line (cost/relief/payback + rollover)
    assert "Defer" in md and "payback" in md and "rollover frees space" in md, (
        "group 4 action must render the capacity-defer line"
    )


def test_kpi_scope_note_partitions_drag_deferred_vs_actionable_live(monkeypatch):
    """The drag KPI counts ALL A/B/D drag; the scope note splits it into DEFERRED
    (accepted OR blocked groups — logged-no-action plus the capacity-blocked gain
    side) vs actionable-today. deferred + actionable == KPI (a partition — every
    register row belongs to exactly one group). Nothing is 'excluded'; it's included."""
    pos, acct, sec, reg = _live()
    ABD = ["A", "B", "D"]
    kpi = float(reg[reg["case"].isin(ABD)]["annual_benefit"].sum())
    def_idx = set()
    for g in ACTION_GROUPS:
        if g["status"] in ("accepted", "blocked"):
            def_idx |= set(filter_register_for_group(reg, g).index)
    _def = reg.loc[sorted(def_idx)] if def_idx else reg.iloc[0:0]
    deferred = float(_def[_def["case"].isin(ABD)]["annual_benefit"].sum())
    actionable = kpi - deferred
    assert deferred > 0, "deferred (accepted + blocked) groups must carry some A/B/D drag"
    assert abs((deferred + actionable) - kpi) < 0.005, "deferred + actionable must equal the KPI"
    # summing A/B/D over ALL group tables must equal the KPI (no double-counting)
    all_groups_abd = 0.0
    for g in ACTION_GROUPS:
        if g["key"] in INFORMATIONAL_KEYS:
            continue
        rr = filter_register_for_group(reg, g)
        all_groups_abd += float(rr[rr["case"].isin(ABD)]["annual_benefit"].sum())
    assert abs(all_groups_abd - kpi) < 0.005, f"all-groups A/B/D {all_groups_abd} must equal KPI {kpi}"

    import src.config
    import src.db
    monkeypatch.setattr(src.config, "IS_DEMO", False)
    monkeypatch.setattr(src.db, "DB_PATH", TRACKER_DB)
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(ROOT / "pages" / "14_Asset_Location.py"), default_timeout=120).run()
    assert not at.exception, f"page raised: {at.exception}"
    caps = " ||| ".join(c.value for c in at.caption)
    assert "accepted or capacity-blocked groups" in caps, "KPI scope note missing/mis-worded"
    assert f"about {escape_md(_fmt_dollars(deferred))} " in caps, (
        f"note must show deferred drag {_fmt_dollars(deferred)}: {caps!r}"
    )
    assert f"about {escape_md(_fmt_dollars(actionable))}." in caps, (
        f"note must show actionable drag {_fmt_dollars(actionable)}"
    )


# ── Check-7 fix: no per-holding prose claim contradicts a per-holding value ─────

def test_loss_side_action_templates_net_loss_no_literal():
    """The loss-side action states the NET figure via {embedded_gain} (never a $
    literal), so 'the block nets to roughly zero (...)' stays live-accurate as the
    data moves (the block flipped from net loss to small net gain in Aug-2026)."""
    g = next(x for x in ACTION_GROUPS if x["key"] == "relocate_loss_side")
    assert "{embedded_gain}" in g["action"], "loss-side action must template the net loss figure"
    assert not _DOLLAR_LITERAL.search(g["action"]), f"no $ literal in loss-side action: {g['action']!r}"


def test_loss_side_action_names_only_loss_lots_at_a_loss_live():
    """The loss-side action must not name a GAIN lot as sold 'at a loss'. JEPI is a
    +$ gain lot in this group; every ticker in the 'at a loss' clause must be at an
    embedded loss, and JEPI must not appear there. BFRIX individually must still
    carry the embedded loss.

    This asserts a PER-HOLDING label, not the group's net. It used to also require
    `embedded_gain.sum() < 0`, which contradicted this docstring's own statement
    that the block nets to roughly zero: the Aug-2026 advisor swap removed HLIPX,
    the lot carrying the loss, leaving BFRIX -$40.22 + JEPI +$47.70 = +$7.48. The
    action prose already says "nets to roughly zero" and reports the figure from a
    template, so the sign of the net is data, not a promise this test can make —
    and the next external swap can move it again in either direction. What must
    stay true regardless is that no gain lot is described as sold at a loss.
    """
    pos, acct, sec, reg = _live()
    g = next(x for x in ACTION_GROUPS if x["key"] == "relocate_loss_side")
    rr = filter_register_for_group(reg, g)
    action = render_prose(g["action"], resolve_placeholders(g, pos, acct, sec, reg))
    assert "at a loss" in action, action
    before = action.split("at a loss")[0]
    # Non-vacuity: without this the two checks below pass trivially if the prose
    # stops naming any holding at all, which is the same mislabelling defect in
    # its silent form.
    named = [s for s in rr["symbol"] if s in before]
    assert named, f"no holding is named in the 'at a loss' clause: {action!r}"
    for sym, eg in zip(rr["symbol"], rr["embedded_gain"]):
        if sym in before:
            assert eg is not None and float(eg) < 0, (
                f"{sym} is named 'at a loss' but embedded_gain={eg} (a gain, not a loss)"
            )
    gains = [s for s, e in zip(rr["symbol"], rr["embedded_gain"]) if e is not None and float(e) > 0]
    for s in gains:
        assert s not in before, f"gain lot {s} must not be named 'at a loss'"


# ── Capital-gains headroom sourced from the runtime income profile ─────────────

def test_headroom_unknown_income_collapses_budget_to_zero():
    """No configured income → no assumed room. The conservative default: claiming
    headroom that isn't there would mean realizing gains expected to be untaxed."""
    from src.location_actions import capital_gains_headroom
    from src.household import build_location_register
    import pandas as pd

    reg = pd.DataFrame(columns=["embedded_gain"])
    hr = capital_gains_headroom(reg, ordinary_income=None, is_demo=False)
    assert hr["income_known"] is False
    assert hr["total"] == 0.0
    assert hr["remaining"] == 0.0


def test_headroom_unknown_income_never_reports_the_full_ceiling():
    from src.location_actions import capital_gains_headroom
    from src.location_config import LTCG_0_BRACKET_CEILING_SINGLE_2026
    import pandas as pd

    hr = capital_gains_headroom(
        pd.DataFrame(columns=["embedded_gain"]), ordinary_income=None, is_demo=False
    )
    assert hr["total"] != LTCG_0_BRACKET_CEILING_SINGLE_2026


def test_headroom_income_below_ceiling_reports_real_room():
    from src.location_actions import capital_gains_headroom
    import pandas as pd

    hr = capital_gains_headroom(
        pd.DataFrame(columns=["embedded_gain"]), ordinary_income=40_000.0
    )
    assert hr["income_known"] is True
    assert hr["total"] == pytest.approx(8_350.0)


def test_headroom_income_above_ceiling_is_exhausted():
    from src.location_actions import capital_gains_headroom
    import pandas as pd

    hr = capital_gains_headroom(
        pd.DataFrame(columns=["embedded_gain"]), ordinary_income=100_000.0
    )
    assert hr["income_known"] is True
    assert hr["total"] == 0.0


def test_headroom_resolves_demo_income_when_not_passed():
    """With no explicit income, demo mode resolves the synthetic constant."""
    from src.location_actions import capital_gains_headroom
    from src.personal_profile import DEMO_ORDINARY_INCOME
    import pandas as pd

    hr = capital_gains_headroom(pd.DataFrame(columns=["embedded_gain"]), is_demo=True)
    assert hr["income_known"] is True
    assert hr["ordinary_income"] == DEMO_ORDINARY_INCOME


# ── The thematic book's household weight (computed, not authored) ───────────────
#
# This is the only figure in the prose-defect batch that is COMPUTED rather than
# deleted or reworded, so it is the only one where a test can satisfy itself by
# calling the implementation's own expression and asserting it equals itself. The
# right-hand side below is therefore derived by a different algorithm — a merge
# against fund_compositions — never by calling look_through_position or the
# resolver. If the resolver's filter, scope, or sleeve set is wrong, these fail.


def _independent_lookthrough_equity(pos, sec, comps, symbols=None):
    """Household look-through equity dollars, derived by merge.

    A holding with rows in fund_compositions contributes one row per underlying
    sleeve at that sleeve's weight; anything else contributes its own
    securities.sleeve_category at weight 1. Then keep EQUITY_SLEEVES only, and
    optionally restrict to a symbol list (the card's numerator).
    """
    from src.location_config import EQUITY_SLEEVES
    sleeve_of = sec.set_index("ticker")["sleeve_category"].to_dict()
    w = comps[["fund_symbol", "underlying_sleeve", "weight"]].rename(
        columns={"fund_symbol": "symbol"})
    m = pos[["symbol", "current_value"]].merge(w, on="symbol", how="left")
    m["sleeve"] = m["underlying_sleeve"].where(
        m["underlying_sleeve"].notna(), m["symbol"].map(sleeve_of))
    m["dollars"] = m["current_value"] * m["weight"].fillna(1.0)
    assert m["sleeve"].notna().all(), (
        f"unclassified symbols: {sorted(set(m[m['sleeve'].isna()]['symbol']))}"
    )
    # The decomposition must conserve dollars, or the denominator is meaningless.
    assert abs(float(m["dollars"].sum()) - float(pos["current_value"].sum())) < 1.0
    eq = m[m["sleeve"].isin(EQUITY_SLEEVES)]
    if symbols is not None:
        eq = eq[eq["symbol"].isin(symbols)]
    return float(eq["dollars"].sum())


def _thematic_live_frames():
    """The frames pages/14 actually resolves against — including
    exclude_non_household_positions, which the page applies at line 115 before any
    figure is computed. It drops the unvested/forfeitable employer contribution;
    leaving it in inflates the look-through equity denominator by its equity share
    and moves the card's weight by ~0.05pp, so the frame is part of the figure's
    definition, not an incidental detail."""
    from src.household import exclude_non_household_positions
    pos, acct, sec, reg = _live()
    comps, _targets = _live_saa()
    return exclude_non_household_positions(pos, acct), acct, sec, reg, comps


def test_thematic_equity_share_matches_independent_derivation_live():
    pos, acct, sec, reg, comps = _thematic_live_frames()
    g = next(x for x in ACTION_GROUPS if x["key"] == "thematic_sprawl")
    r = resolve_placeholders(g, pos, acct, sec, reg, compositions_df=comps)

    denom = _independent_lookthrough_equity(pos, sec, comps)
    numer = _independent_lookthrough_equity(pos, sec, comps, symbols=set(g["symbols"]))
    assert r["lookthrough_equity_value"] == _fmt_dollars(denom), "denominator drifted"
    assert r["thematic_equity_value"] == _fmt_dollars(numer), "numerator drifted"
    assert r["thematic_equity_share"] == f"{numer / denom * 100:.1f}%", "share drifted"


def test_thematic_share_denominator_is_look_through_not_as_held():
    """household.py's methodology decision 1 makes look-through the household's
    stated basis, and the Household View defaults to it. As-held would read ~19.8%
    against look-through's ~11.5% — an 8pp gap, and a card contradicting the
    methodology note on the same data."""
    from src.location_config import EQUITY_SLEEVES
    pos, acct, sec, reg, comps = _thematic_live_frames()
    g = next(x for x in ACTION_GROUPS if x["key"] == "thematic_sprawl")
    r = resolve_placeholders(g, pos, acct, sec, reg, compositions_df=comps)

    as_held = pos.merge(sec[["ticker", "sleeve_category"]], left_on="symbol",
                        right_on="ticker", how="left")
    as_held_equity = float(
        as_held[as_held["sleeve_category"].isin(EQUITY_SLEEVES)]["current_value"].sum())
    lt_equity = _independent_lookthrough_equity(pos, sec, comps)
    assert lt_equity > as_held_equity, (
        "fixture assumption: the household holds funds-of-funds whose equity is "
        "invisible as-held"
    )
    assert r["lookthrough_equity_value"] == _fmt_dollars(lt_equity)
    assert r["lookthrough_equity_value"] != _fmt_dollars(as_held_equity)


def test_thematic_share_numerator_is_a_strict_subset_of_its_denominator():
    """A share of equity has to be a share OF something: every dollar on top must
    also be counted underneath. That is what excludes IBIT — crypto is not an equity
    sleeve, so it stays in the card's {count}/{value} and out of the percentage.
    Without this the figure would put $238 of crypto over an equity base and stop
    being a proportion at all."""
    from src.location_config import EQUITY_SLEEVES
    pos, acct, sec, reg, comps = _thematic_live_frames()
    g = next(x for x in ACTION_GROUPS if x["key"] == "thematic_sprawl")
    syms = set(g["symbols"])

    numer = _independent_lookthrough_equity(pos, sec, comps, symbols=syms)
    denom = _independent_lookthrough_equity(pos, sec, comps)
    assert 0 < numer < denom

    card_all = float(pos[pos["symbol"].isin(syms)]["current_value"].sum())
    sleeve_of = sec.set_index("ticker")["sleeve_category"].to_dict()
    non_equity = float(
        pos[pos["symbol"].isin(syms)
            & ~pos["symbol"].map(sleeve_of).isin(EQUITY_SLEEVES)]["current_value"].sum())
    assert non_equity > 0, (
        "fixture assumption: the card holds at least one non-equity symbol — if this "
        "is now false, the cons clause naming the exclusion is false too"
    )
    # The numerator is the card's book MINUS its non-equity holdings, exactly.
    assert numer == pytest.approx(card_all - non_equity, abs=0.01)


def test_thematic_non_equity_holdings_are_named_in_the_cons_prose():
    """The exclusion must be stated, not silent. A reader adding up the card's own
    table cannot reach the share unless the prose says which holdings the equity
    percentage leaves out — and if the advisor buys another non-equity name into
    this list, this fails until the prose accounts for it."""
    from src.location_config import EQUITY_SLEEVES
    pos, _acct, sec, _reg, _comps = _thematic_live_frames()
    g = next(x for x in ACTION_GROUPS if x["key"] == "thematic_sprawl")
    sleeve_of = sec.set_index("ticker")["sleeve_category"].to_dict()
    held = pos[pos["symbol"].isin(set(g["symbols"]))]
    non_equity = sorted({
        s for s in set(held["symbol"]) if sleeve_of.get(s) not in EQUITY_SLEEVES
    })
    assert non_equity, (
        "no card symbol is outside the equity sleeves any more — the cons clause "
        "naming an excluded holding is now false and must be deleted"
    )
    for sym in non_equity:
        assert sym in g["cons"], (
            f"{sym} is in the card's symbol list but outside EQUITY_SLEEVES, so it is "
            f"excluded from {{thematic_equity_share}} — the cons prose must name it"
        )


def test_thematic_cons_states_both_scopes_so_the_arithmetic_closes():
    """The card shows a TOD-scoped {value} and a household-wide share. A reader
    dividing the displayed dollars by anything cannot reach the percentage, so the
    cons must render all three figures: the TOD subtotal, the household-wide
    numerator, and the denominator it is a share of."""
    pos, acct, sec, reg, comps = _thematic_live_frames()
    g = next(x for x in ACTION_GROUPS if x["key"] == "thematic_sprawl")
    r = resolve_placeholders(g, pos, acct, sec, reg, compositions_df=comps)
    cons = render_prose_md(g["cons"], r)

    for key in ("value", "thematic_equity_value", "lookthrough_equity_value",
                "thematic_equity_share"):
        assert escape_md(r[key]) in cons, f"cons must render {{{key}}}: {cons!r}"
    # The household numerator and the TOD subtotal are different dollar figures —
    # if they ever render identically the sentence has lost its point.
    assert r["thematic_equity_value"] != r["value"]
    # And the share is genuinely the two rendered figures divided.
    _d = lambda s: float(s.replace("$", "").replace(",", ""))
    share = _d(r["thematic_equity_value"]) / _d(r["lookthrough_equity_value"]) * 100
    assert f"{share:.1f}%" == r["thematic_equity_share"], (
        "the rendered dollars do not divide to the rendered percent — the reader "
        "cannot check the figure"
    )


def test_thematic_share_is_none_without_compositions_so_render_raises():
    """No compositions frame means no household look-through, and a share computed
    on any other basis would silently contradict the page. Resolve to None and let
    render_prose raise — the same contract roth_idle_cash has."""
    pos, acct, sec, reg, _comps = _thematic_live_frames()
    g = next(x for x in ACTION_GROUPS if x["key"] == "thematic_sprawl")
    r = resolve_placeholders(g, pos, acct, sec, reg)
    assert r["thematic_equity_share"] is None
    assert r["thematic_equity_value"] is None
    assert r["lookthrough_equity_value"] is None
    with pytest.raises(ValueError, match="Unresolvable placeholder"):
        render_prose(g["cons"], r)


# ── The fabricated zero behind {value} on the deploy card (PR 1b) ──────────────
#
# render_prose raises on an unresolvable None, and pages/14's Assumptions expander
# advertises exactly that: "an unresolvable figure raises rather than rendering $0".
# The guard has a hole shaped like this bug — a fabricated 0.0 produced UPSTREAM
# sails straight through it, and the card renders "$0 is sitting uninvested in a
# money market inside your most valuable account", an argument to act on nothing.
#
# Two cases share that 0.0 today and only one of them is honest:
#   genuinely zero  — a Roth resolved and its cash sleeve is empty. A real
#                     measurement; the card must say so instead of arguing.
#   unresolvable    — no Roth account resolves at all. A fabrication; must be None
#                     so the existing guard can do its job.

def _deploy_group():
    return next(g for g in ACTION_GROUPS if g["key"] == "deploy_roth_cash")


def _sba(rows):
    return pd.DataFrame(rows, columns=["pseudonym", "sleeve_category", "current_value"])


def _accounts(treatments):
    return pd.DataFrame(
        [{"pseudonym": p, "tax_treatment": t} for p, t in treatments],
        columns=["pseudonym", "tax_treatment"])


def test_idle_cash_is_none_when_no_roth_account_resolves():
    """The fabrication. With no Roth in the accounts frame there is no balance to
    report, and the old code answered ("", 0.0) — a real-looking zero for an
    account that does not exist."""
    from src.location_actions import _roth_idle_cash
    _pseudo, idle = _roth_idle_cash(
        _sba([("acct_taxable_01", "cash", 900.0)]),
        _accounts([("acct_taxable_01", "taxable")]),
    )
    assert idle is None, "an unresolvable Roth balance must be None, never 0.0"


def test_idle_cash_is_zero_when_the_roth_resolves_and_holds_no_cash():
    """Non-vacuity for the test above: making everything None would be just as
    wrong. A resolved Roth with an empty cash sleeve genuinely holds zero, and
    that zero is a measurement."""
    from src.location_actions import _roth_idle_cash
    pseudo, idle = _roth_idle_cash(
        _sba([("acct_roth_01", "us_large_core", 5000.0)]),
        _accounts([("acct_roth_01", "roth_ira")]),
    )
    assert idle == 0.0 and pseudo == "acct_roth_01"


def test_deploy_answer_propagates_the_unresolvable_balance():
    """build_roth_deploy_answer must not do arithmetic on the unknown and must not
    substitute its own zero on the way through."""
    from src.location_actions import build_roth_deploy_answer
    ans = build_roth_deploy_answer(
        pd.DataFrame([{"pseudonym": "acct_taxable_01", "symbol": "VOO",
                       "current_value": 900.0}]),
        _accounts([("acct_taxable_01", "taxable")]),
        pd.DataFrame(columns=["ticker", "sleeve_category", "is_in_saa"]),
    )
    assert ans["idle_cash"] is None
    assert ans["residual"] is None


def test_unresolvable_balance_makes_the_card_raise_not_render_zero():
    """The hole, closed: with the balance unknown the existing guard fires."""
    from src.location_actions import render_prose, resolve_placeholders
    g = _deploy_group()
    resolved = resolve_placeholders(
        g, pd.DataFrame(columns=["pseudonym", "symbol", "current_value"]),
        pd.DataFrame(columns=["pseudonym", "tax_treatment"]),
        pd.DataFrame(columns=["ticker", "sleeve_category"]),
        pd.DataFrame(columns=_REGISTER_COLS), roth_idle_cash=None)
    assert resolved["value"] is None
    with pytest.raises(ValueError, match="Unresolvable placeholder"):
        render_prose(g["pros"], resolved)


def test_a_genuine_zero_renders_the_zero_state_not_the_deploy_argument():
    """The honest zero must not render the deploy argument. "$0 is sitting
    uninvested ... every day it sits is compounding you don't get back" is an
    argument to act, and there is nothing to act on."""
    from src.location_actions import deploy_prose_for
    g = _deploy_group()
    body = deploy_prose_for(g, 0.0, tier="zero")
    assert body is g["zero_state"]
    assert "sitting uninvested" not in body
    assert "{value}" not in body, "the zero state must not template a balance"


def test_a_real_balance_still_renders_the_deploy_argument():
    """Non-vacuity: the zero state must not swallow the ordinary case."""
    from src.location_actions import deploy_prose_for
    g = _deploy_group()
    assert deploy_prose_for(g, 1234.0, tier="full_band") is g["pros"]


def test_deploy_prose_refuses_an_unresolvable_balance():
    from src.location_actions import deploy_prose_for
    with pytest.raises(ValueError, match="unresolvable|unknown"):
        deploy_prose_for(_deploy_group(), None, tier="full_band")


def test_page14_discloses_the_yield_assumption_and_marks_its_basis_live(monkeypatch):
    """#210 page-level proof: every yield basis reaches the rendered page.

    The unit tests in tests/test_yield_basis.py pin what the strings SAY; this pins
    that they arrive on the page a reader opens. A helper returning the right text
    proves nothing if the page never calls it — the two mutants that survive every
    unit test are "page never calls the note" and "page drops the marker column".
    """
    from src.household_data import find_latest_positions_csv
    csv = find_latest_positions_csv()
    if csv is None or not TRACKER_DB.exists() or TRACKER_DB.stat().st_size == 0:
        pytest.skip("personal-mode inputs absent")
    import src.config
    import src.db
    monkeypatch.setattr(src.config, "IS_DEMO", False)
    monkeypatch.setattr(src.db, "DB_PATH", TRACKER_DB)

    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(ROOT / "pages" / "14_Asset_Location.py"), default_timeout=90).run()
    assert not at.exception, f"page raised: {at.exception}"

    md = " ||| ".join(m.value for m in at.markdown)
    caps = " ||| ".join(c.value for c in at.caption)

    # 1. the mechanism, unchanged from #191
    assert "position value × assumed sleeve yield × tax rate" in md
    # MIGRATED at #289: "no declared basis" was true while authored MEANT undeclared.
    # An authored entry now declares a basis that is not a measurement, and the live
    # page must say which kind of claim it is — that is the reader-facing property.
    assert "authored assumption" in md
    assert "judgement, not a measurement" in md, (
        "the live page does not say the authored rows are judgements")
    assert "no declared basis** at all" not in md
    # 2. the claim that made the omission a false statement stays gone
    assert "Dollar figures in every card are templated" not in md

    # 3. every basis present in the register is disclosed, and the counts are
    #    derived from the frame rather than authored.
    pos, acct, sec, reg = _live()
    counts = reg["yield_basis"].value_counts().to_dict()
    n = len(reg)
    if counts.get("look_through", 0) + counts.get("look_through_partial", 0):
        n_lt = counts.get("look_through", 0) + counts.get("look_through_partial", 0)
        assert f"{n_lt} of {n} rows" in md, "look-through rows are not disclosed"
        assert "looking through" in md
    if counts.get("not_modelled", 0):
        assert f"{counts['not_modelled']} of {n} rows" in md
        assert "not modelled, not zero" in md
    if counts.get("default", 0):
        assert f"{counts['default']} of {n} rows" in md
    # #289's kinds, disclosed on the live page the same way — a basis that exists in
    # the register and not in the rendered note is not disclosed.
    if counts.get("authored", 0):
        assert f"{counts['authored']} of {n} rows** use an **authored** yield" in md
    if counts.get("structural", 0):
        assert f"{counts['structural']} of {n} rows" in md
        assert "no cash flow to yield" in md

    # 4. the KPI's exclusion notice renders WITH the total it qualifies, not only in
    #    the expander — the .sum() crux: a total omitting rows must say so where the
    #    total is read.
    from src.location_actions import drag_coverage, format_drag_exclusion
    cov = drag_coverage(reg)
    if cov.n_not_modelled:
        assert "not modelled, not zero" in caps, (
            "the drag KPI rendered without its exclusion notice; expected "
            f"{format_drag_exclusion(cov)!r} in a caption"
        )
        for sym in set(cov.symbols):
            assert sym in caps

    # 5. the per-row marker. Derived from the register, never a symbol literal.
    yield_tables = [df.value for df in at.dataframe
                    if "Assumed Yield" in list(getattr(df.value, "columns", []))]
    assert yield_tables, "no Underlying-positions table carried an 'Assumed Yield' column"
    rendered = set()
    for t in yield_tables:
        if "Symbol" not in t.columns:
            continue
        for sym, cell in zip(t["Symbol"], t["Assumed Yield"]):
            if isinstance(cell, str) and cell:
                rendered.add((sym, cell))
    for basis, marker in (("look_through_partial", "look-through, partial"),
                          ("look_through", "look-through"),
                          ("default", "default"),
                          ("not_modelled", "not modelled")):
        syms = set(reg.loc[reg["yield_basis"] == basis, "symbol"])
        if not syms:
            continue
        hit = {s for s, cell in rendered if s in syms and marker in cell}
        assert hit, (
            f"no row of basis {basis!r} rendered its {marker!r} marker; register says "
            f"{sorted(syms)} carry it, rendered cells were {sorted(rendered)}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# #309 — the deploy floor, its tiers, the effect in one number, and cents
# ══════════════════════════════════════════════════════════════════════════════
# Synthetic book, CI-runnable (page 14 itself is personal-only): household $100,000,
# tightest band 2pp -> one full band $2,000, floor 10% of it = $200. Two gaps,
# $3,000 and $1,000, so Σgap = $4,000.

_HH = 100_000.0
_TARGETS = pd.DataFrame({"name": ["A", "B", "C"], "target_weight": [0.2, 0.1, 0.1],
                         "tolerance_band": [0.03, 0.02, 0.0]})
_GAPS = pd.DataFrame({"sleeve": ["us_large_quality", "emerging_markets"],
                      "ticker": ["SPHQ", "IEMG"], "current": [5_000.0, 9_000.0],
                      "target": [8_000.0, 10_000.0], "gap": [3_000.0, 1_000.0]})


def _synthetic(cash):
    return {"idle_cash": cash, "gaps": _GAPS, "household_value": _HH,
            "table": pd.DataFrame(columns=["ticker", "sleeve", "dollar"])}


def _tier(cash):
    return deploy_state(_deploy_group(), _synthetic(cash), _TARGETS)


def test_floor_tiers_are_read_from_config_verbatim():
    """The tiers' scores and statuses are AUTHORED, like every other card's; the
    balance only selects the row. The full-band tier is the group's own 10 /
    act_now, which test_scores/test_statuses_are_read_from_config_verbatim pin."""
    assert _deploy_group()["floor_tiers"] == {
        "below_floor":    {"score": None, "status": "accepted"},
        "under_one_band": {"score": 6,    "status": "evaluate"},
    }


@pytest.mark.parametrize("cash, tier, score, status", [
    (0.0,      "zero",           None, "accepted"),   # #313: $0 is below the floor
    (0.01,     "below_floor",    None, "accepted"),
    (199.99,   "below_floor",    None, "accepted"),
    (200.00,   "under_one_band", 6,    "evaluate"),
    (1_999.99, "under_one_band", 6,    "evaluate"),
    (2_000.00, "full_band",      10,   "act_now"),
    (7_000.00, "full_band",      10,   "act_now"),
])
def test_the_balance_selects_the_tier_two_sided_at_both_thresholds(cash, tier, score, status):
    st_ = _tier(cash)
    assert (st_["tier"], st_["score"], st_["status"]) == (tier, score, status)


def test_the_floor_is_ten_percent_of_the_tightest_band_of_the_household():
    st_ = _tier(50.0)
    assert st_["band"] == 0.02                       # the tightest POSITIVE band
    assert st_["full_band"] == pytest.approx(2_000.0)
    assert st_["floor"] == pytest.approx(200.0)


def test_a_floor_without_its_anchor_raises():
    with pytest.raises(ValueError, match="tolerance_band"):
        deploy_state(_deploy_group(), _synthetic(50.0), _TARGETS.drop(columns=["tolerance_band"]))


def test_the_effect_is_one_share_of_every_gap():
    """Gap-proportional buys close the SAME share of every gap: cash / Σgap."""
    st_ = _tier(1_000.0)
    assert st_["gap_share"] == pytest.approx(0.25)
    lg = st_["largest"]
    assert lg["current"] == pytest.approx(0.05) and lg["target"] == pytest.approx(0.08)
    assert lg["after"] == pytest.approx((5_000 + 750) / _HH)
    assert _tier(10_000.0)["gap_share"] == 1.0       # capped: the residual carries the rest


def _card(cash):
    g = _deploy_group()
    st_ = _tier(cash)
    resolved = {"value": _fmt_dollars(cash),
                **__import__("src.location_actions", fromlist=["x"])._deploy_placeholders(st_)}
    return deploy_card_text(g, st_, resolved)


def test_below_the_floor_the_card_renders_its_third_state():
    """The RENDERED card below the floor: no score, not Act now, no buys, no Against,
    and the action line says no buy is proposed."""
    c = _card(7.93)
    assert "/10" not in c["subheader"], f"a below-floor card must not carry a score: {c['subheader']!r}"
    assert c["status_label"] == "Below floor"
    # The card strings are Markdown, so "$" arrives escaped.
    assert c["action"].replace(r"\$", "$").startswith(
        "No buy proposed: $7.93 of Roth cash is below the $200 deploy floor")
    assert not c["show_against"] and not c["show_buys"]


def test_the_below_floor_body_forbids_the_reassuring_misreading():
    c = _card(7.93)
    assert "the balance is not lost" in c["body"]
    assert "The Roth is not empty" in c["body"]
    assert "sits in the Roth's cash sleeve" in c["body"]
    assert "Nothing to deploy" not in c["body"], "the zero state is false at $7.93"


def test_no_buy_proposed_is_said_once_not_twice():
    """The action line says it; the body must not repeat it."""
    c = _card(7.93)
    assert "No buy" not in c["body"] and "no buy" not in c["body"].lower()


def test_below_the_floor_asserts_no_provenance():
    """How the cash got there is unknowable (no contribution ledger), so the body
    must not claim it was a dividend, a contribution, or left over from a deploy."""
    body = _card(7.93)["body"].lower()
    for word in ("dividend", "contribution", "accumulated", "deployment complete", "leftover"):
        assert word not in body, f"below-floor body asserts provenance: {word!r}"


def test_above_the_floor_the_prose_states_the_effect_not_urgency():
    c = _card(1_000.0)
    assert c["subheader"].endswith("·  6/10") and c["status_label"] == "Evaluate"
    assert "closes 25.0% of every underweight sleeve's gap" in c["body"]
    assert "moves from 5.0% to 5.8% of the household against its 8.0% target" in c["body"]
    assert "compounding you don't get back" not in c["body"]
    full = _card(2_000.0)
    assert full["subheader"].endswith("·  10/10") and full["status_label"] == "Act now"


def test_deploy_prose_lengths_pinned_at_a_fixed_balance():
    """The pin moved here from the live table: at a FIXED state it measures the
    prose, not the book (#309). Argument at $1,000; below-floor state at $7.93."""
    assert (len(_card(1_000.0)["body"]), len(_card(7.93)["body"])) == PINNED_DEPLOY_LENGTHS


PINNED_DEPLOY_LENGTHS = (565, 375)   # measured and READ at 2026-09-23, not transcribed


# ── cents: a nonzero amount never prints as $0 ─────────────────────────────────

def test_a_nonzero_buy_never_prints_as_zero_in_the_buy_tables():
    """The tables format with DEPLOY_BUY_FORMAT. "$%.0f" printed a $0.40 leg as
    "$0" — a real amount shown as zero."""
    assert DEPLOY_BUY_FORMAT % 0.40 == "$0.40"
    assert DEPLOY_BUY_FORMAT % 1.13 == "$1.13"


def test_page14_buy_columns_use_the_cents_format():
    """Both buy columns read the constant; neither may carry a whole-dollar literal."""
    text = (ROOT / "pages" / "14_Asset_Location.py").read_text(encoding="utf-8")
    for col in ('"Amount ($)"', '"Buy ($)"'):
        lines = [ln for ln in text.splitlines() if col in ln and "NumberColumn" in ln]
        assert lines, f"{col} column_config not found"
        for ln in lines:
            assert "DEPLOY_BUY_FORMAT" in ln and "$%.0f" not in ln, ln


@pytest.mark.parametrize("x, want", [
    (0.40, "$0.40"), (0.01, "$0.01"), (-0.2, "-$0.20"),
    (0.0, "$0"), (0.5, "$0"), (7.93, "$8"), (51, "$51"), (447.56, "$448"),
])
def test_fmt_dollars_keeps_cents_only_where_rounding_would_print_zero(x, want):
    """Narrow on purpose: whole dollars everywhere else, so no other card's
    rendered length moves. (0.5 rounds half-to-even to $0: a known edge of the
    whole-dollar branch, not a nonzero-shown-as-zero case under 0.5.)"""
    assert _fmt_dollars(x) == want


def test_page14_live_below_floor_card_renders_its_third_state(monkeypatch):
    """The RENDERED page on the live book, when the live balance is below the floor
    (it is, at $7.93 on 2026-09-23): the card leaves Act now, carries no score, and
    renders the below-floor action and body."""
    from src.household_data import find_latest_positions_csv
    if find_latest_positions_csv() is None or not TRACKER_DB.exists() \
            or TRACKER_DB.stat().st_size == 0:
        pytest.skip("personal-mode inputs absent")
    pos, acct, sec, _reg = _live()
    _dep, tier = _live_tier(pos, acct, sec)
    if tier["tier"] != "below_floor":
        pytest.skip(f"live balance is {tier['tier']}, not below the floor")
    import src.config
    import src.db
    monkeypatch.setattr(src.config, "IS_DEMO", False)
    monkeypatch.setattr(src.db, "DB_PATH", TRACKER_DB)
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(ROOT / "pages" / "14_Asset_Location.py"), default_timeout=90).run()
    assert not at.exception, f"page raised: {at.exception}"
    subs = [s.value for s in at.subheader]
    deploy_sub = [s for s in subs if s.startswith("Deploy idle Roth cash")]
    assert deploy_sub == ["Deploy idle Roth cash"], f"below-floor card carries a score: {deploy_sub}"
    md = " ||| ".join(m.value for m in at.markdown)
    assert "No buy proposed" in md and "the balance is not lost" in md
    # It is not in the Act now bucket: its subheader comes after the Accepted header.
    order = [(e.type, e.value) for e in at.main if getattr(e, "type", "") in ("header", "subheader")]
    heads = [v for t, v in order if t == "header"]
    pos_accepted = next(i for i, (t, v) in enumerate(order) if t == "header" and v == "Accepted")
    pos_card = next(i for i, (t, v) in enumerate(order) if v == "Deploy idle Roth cash")
    assert pos_card > pos_accepted, f"below-floor card rendered outside Accepted: {heads}"


def test_page14_live_kpi_states_the_same_below_floor_balance_as_the_card(monkeypatch):
    """One balance, one rendering: the Act-now KPI line and the card both say $7.93,
    not "$8" in one place and "$7.93" in the other."""
    from src.household_data import find_latest_positions_csv
    if find_latest_positions_csv() is None or not TRACKER_DB.exists() \
            or TRACKER_DB.stat().st_size == 0:
        pytest.skip("personal-mode inputs absent")
    pos, acct, sec, _reg = _live()
    _dep, tier = _live_tier(pos, acct, sec)
    if tier["tier"] != "below_floor":
        pytest.skip(f"live balance is {tier['tier']}, not below the floor")
    import src.config
    import src.db
    monkeypatch.setattr(src.config, "IS_DEMO", False)
    monkeypatch.setattr(src.db, "DB_PATH", TRACKER_DB)
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(ROOT / "pages" / "14_Asset_Location.py"), default_timeout=90).run()
    exact = escape_md(f"${tier['cash']:,.2f}")
    kpi = [m.value for m in at.markdown if m.value.startswith("Deploy **")]
    assert kpi and exact in kpi[0], f"KPI does not state {exact}: {kpi}"


def test_a_measured_zero_is_scored_below_the_floor_but_keeps_its_wording():
    """#313. $0 is below the floor, so it cannot outrank $7.93: no score, Accepted.
    Its WORDING is the zero state's, unchanged -- only the routing moved."""
    zero, small = _card(0.0), _card(7.93)
    assert zero["subheader"] == small["subheader"] == _deploy_group()["title"]
    assert _tier(0.0)["status"] == _tier(7.93)["status"] == "accepted"
    assert zero["action"] == "Nothing to deploy — the Roth's cash sleeve is empty."
    assert zero["body"] == render_prose_md(_deploy_group()["zero_state"], {})
    assert zero["status_label"] == "Accepted"
