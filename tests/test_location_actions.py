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
                  "annual_benefit", "embedded_gain", "cost_to_realize", "is_free", "payback_months"]


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
    conn.close()
    reg = build_location_register(pos, acct, sec, TAX_PROFILE,
                                  SLEEVE_PRIORITY_BY_ACCOUNT_TYPE, ACCOUNT_SHELTER_PRIORITY)
    return pos, acct, sec, reg


def _live_saa():
    """The two SAA tables the gap-proportional deploy needs (compositions +
    per-sleeve targets), loaded exactly as the page does. Call alongside _live()
    only in tests that inspect the deploy TABLE (not those needing idle_cash only)."""
    conn = sqlite3.connect(str(TRACKER_DB))
    comps = pd.read_sql_query("SELECT * FROM fund_compositions", conn)
    targets = pd.read_sql_query(
        "SELECT asset_class_id, name, target_weight FROM asset_classes "
        "WHERE parent_id IS NOT NULL AND target_weight > 0", conn)
    conn.close()
    return comps, targets


# ── Scores are authored config, never computed ─────────────────────────────────

def test_scores_are_read_from_config_verbatim():
    got = {g["key"]: g["score"] for g in ACTION_GROUPS}
    assert got == {
        "deploy_roth_cash": 10,
        "clear_roth_non_equity": 9,
        "relocate_loss_side": 9,
        "relocate_gain_side": 3,
        "thematic_sprawl": 2,
        "rollover_401k": 3,
        "frozen_tod_income": 5,
        "saa_sleeves_taxable": 1,
        "predeploy_stranded_equity": 4,
    }


def test_statuses_are_read_from_config_verbatim():
    got = {g["key"]: g["status"] for g in ACTION_GROUPS}
    assert got == {
        "deploy_roth_cash": "act_now",
        "clear_roth_non_equity": "act_now",
        "relocate_loss_side": "act_now",
        "relocate_gain_side": "blocked",
        "thematic_sprawl": "accepted",
        "rollover_401k": "blocked",
        "frozen_tod_income": "accepted",
        "saa_sleeves_taxable": "accepted",
        "predeploy_stranded_equity": "evaluate",
    }


# ── No two groups render identical prose (guard against templated filler) ───────

def test_no_two_groups_render_identical_prose():
    pos, acct, sec, reg = _live()
    deploy = build_roth_deploy_answer(pos, acct, sec)
    rendered = []
    for g in ACTION_GROUPS:
        resolved = resolve_placeholders(g, pos, acct, sec, reg, roth_idle_cash=deploy["idle_cash"])
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
    # International appears for taxable, never for roth (a Roth forfeits the FTC).
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
    deploy = build_roth_deploy_answer(pos, acct, sec)
    by_key = {g["key"]: g for g in ACTION_GROUPS}

    d = resolve_placeholders(by_key["deploy_roth_cash"], pos, acct, sec, reg,
                             roth_idle_cash=deploy["idle_cash"])
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
    assert DIRECTABLE_PSEUDONYMS == frozenset({"acct_taxable_01", "acct_roth_01", "acct_trad_ira_01"})
    # Directable: self-directed taxable + both IRAs.
    assert is_directable("acct_taxable_01") is True
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
    dep = build_roth_deploy_answer(pos, acct, sec)
    out = {}
    for g in ACTION_GROUPS:
        r = resolve_placeholders(g, pos, acct, sec, reg, roth_idle_cash=dep["idle_cash"])
        out[g["key"]] = (render_prose_md(g["pros"], r), render_prose_md(g["cons"], r), r, g)
    return out


def test_no_rendered_prose_contains_comma_emdash():
    """A dropped clause renders as valid Markdown ('accepted, — not to fix it') and
    is invisible in review. This is the canary."""
    for key, (pros, cons, _r, _g) in _rendered_all().items():
        assert ", —" not in pros, f"{key} pros contains ', —' (dropped clause): {pros!r}"
        assert ", —" not in cons, f"{key} cons contains ', —' (dropped clause): {cons!r}"


# Exact rendered lengths against the live Jul-08 CSV — a brittle-on-purpose canary
# for silent prose corruption (dropped words render as valid Markdown).
RENDERED_PROSE_LEN = {
    "deploy_roth_cash":          (514, 577),   # gap-proportional prose + EM/FTC clause
    "clear_roth_non_equity":     (1169, 887),  # + named VTI Roth rebuy (1:1 to hedged equity) — cons unchanged
    "relocate_loss_side":        (410, 859),   # cons: + rebuy rec (BND/AGG/FXNAX) + duration-risk note
    "relocate_gain_side":        (311, 404),   # rewritten: 22%/15% capacity-defer, no 0% headroom
    "thematic_sprawl":           (217, 475),   # cons: 0% headroom -> 15% cap-gains language
    "rollover_401k":             (338, 526),
    "frozen_tod_income":         (382, 477),   # cons: 0% headroom -> 15% cap-gains language
    "saa_sleeves_taxable":       (261, 665),
    "predeploy_stranded_equity": (328, 530),
}


def test_rendered_prose_char_lengths_pinned():
    for key, (pros, cons, _r, _g) in _rendered_all().items():
        assert (len(pros), len(cons)) == RENDERED_PROSE_LEN[key], (
            f"{key} rendered length drifted: got {(len(pros), len(cons))}, "
            f"pinned {RENDERED_PROSE_LEN[key]} — possible silent prose corruption"
        )


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
    # count == row_count holds regardless of the MIN_ANNUAL_BENEFIT surfacing flag: the
    # expander shows every backing row whether or not it clears the threshold (at higher
    # ordinary rates some of these sub-$1 sleeves now cross it — the invariant is unaffected).


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
    headroom-framed. The cons templates the IRA capacity; no $ figure in
    pros/cons/action is a literal; and the stale 0%-headroom language is gone."""
    g4 = next(g for g in ACTION_GROUPS if g["key"] == "relocate_gain_side")
    blob = g4["pros"] + g4["cons"] + g4["action"]
    assert not _DOLLAR_LITERAL.search(blob), f"group 4 must have no $ literal: {blob!r}"
    assert "{trad_ira_equity}" in g4["cons"], "gain-side cons must template the IRA capacity"
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


def test_exactly_one_allow_literals_group_and_all_commented():
    """After templating the five live literals, exactly one group keeps
    allow_literals (thematic's $76 fee estimate). A second one cannot be added
    without an explanatory comment on the same config line."""
    import src.location_actions as la
    allowed = [g for g in ACTION_GROUPS if g.get("allow_literals")]
    assert len(allowed) == 1, f"exactly one allow_literals group expected; got {[g['key'] for g in allowed]}"
    assert allowed[0]["key"] == "thematic_sprawl"

    src = pathlib.Path(la.__file__).read_text(encoding="utf-8")
    lit_lines = [ln for ln in src.splitlines() if re.search(r'"allow_literals":\s*True', ln)]
    assert len(lit_lines) == 1, f"expected exactly one allow_literals line in config, got {len(lit_lines)}"
    for ln in lit_lines:
        after_true = ln.split("True", 1)[1]
        assert "#" in after_true, (
            f"allow_literals must carry an explanatory comment on the same line: {ln.strip()!r}"
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
    assert "{trad_ira_equity}" in g3["cons"]
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


# ── Roth-cleanup card names VTI as the Roth equity rebuy (1:1, not gap-sized) ───

def test_clear_roth_names_vti_and_templates_the_rebuy_figure():
    """Config/template guard (runs in CI, no live data): the Roth-cleanup card carries
    the hedged-equity rebuy subset and both its action and For prose name VTI and
    template the {roth_equity_rebuy} figure — never a hardcoded dollar or a ticker-less
    'broad equity'. The subset is a strict subset of the 7 sold symbols (equity only)."""
    g = next(x for x in ACTION_GROUPS if x["key"] == "clear_roth_non_equity")
    assert g["equity_rebuy_symbols"] == ["JEPQ", "JEPI", "JHEQX", "HELO"]
    assert set(g["equity_rebuy_symbols"]) < set(g["symbols"]), "rebuy subset must be equity-only, a subset of the 7"
    assert "{roth_equity_rebuy}" in g["action"] and "{roth_equity_rebuy}" in g["pros"]
    assert "VTI" in g["action"] and "VTI" in g["pros"]
    assert "broad equity there" not in g["action"], "the ticker-less 'broad equity' clause must be replaced"
    assert not _DOLLAR_LITERAL.search(g["action"]) and not _DOLLAR_LITERAL.search(g["pros"]), "no $ literal"


def test_clear_roth_rebuy_sized_1to1_to_hedged_equity_live():
    """The VTI rebuy is sized 1:1 to the hedged, covered-call equity sold
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
    assert "VTI" in action and "VTI" in pros
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
        r = resolve_placeholders(g, pos, acct, sec, reg, roth_idle_cash=dep["idle_cash"])
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
    # The deploy action line is gap-proportional now — assert its stable phrasing
    # (the tickers/order are data-driven), plus the other cards' fixed action lines.
    for snippet in ("sized to its household underweight gap", "not into a Traditional IRA",
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
    assert "Deploy" in md and "in-shelter (free)" in md and "harvest the loss side" in md, (
        "Act-now actionable-dollars summary line is missing"
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
    """The loss-side action states the NET loss via {embedded_gain} (never a $ literal),
    so 'the block nets to a small loss (...)' stays live-accurate as the data moves."""
    g = next(x for x in ACTION_GROUPS if x["key"] == "relocate_loss_side")
    assert "{embedded_gain}" in g["action"], "loss-side action must template the net loss figure"
    assert not _DOLLAR_LITERAL.search(g["action"]), f"no $ literal in loss-side action: {g['action']!r}"


def test_loss_side_action_names_only_loss_lots_at_a_loss_live():
    """The loss-side action must not name a GAIN lot as sold 'at a loss'. JEPI is a
    +$ gain lot in this group; every ticker in the 'at a loss' clause must be at an
    embedded loss, and JEPI must not appear there. The group still nets to a loss."""
    pos, acct, sec, reg = _live()
    g = next(x for x in ACTION_GROUPS if x["key"] == "relocate_loss_side")
    rr = filter_register_for_group(reg, g)
    action = render_prose(g["action"], resolve_placeholders(g, pos, acct, sec, reg))
    assert "at a loss" in action, action
    before = action.split("at a loss")[0]
    for sym, eg in zip(rr["symbol"], rr["embedded_gain"]):
        if sym in before:
            assert eg is not None and float(eg) < 0, (
                f"{sym} is named 'at a loss' but embedded_gain={eg} (a gain, not a loss)"
            )
    gains = [s for s, e in zip(rr["symbol"], rr["embedded_gain"]) if e is not None and float(e) > 0]
    for s in gains:
        assert s not in before, f"gain lot {s} must not be named 'at a loss'"
    assert float(rr["embedded_gain"].sum()) < 0, "group must still net to a loss (net framing preserved)"
