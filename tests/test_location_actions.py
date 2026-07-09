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
    ROTH_DEPLOY_EXCLUDED_SLEEVES,
    build_roth_deploy_answer,
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
)
from src.location_config import (
    TAX_PROFILE,
    SLEEVE_LOCATION_PRIORITY,
    ACCOUNT_SHELTER_PRIORITY,
    DIRECTABLE_PSEUDONYMS,
    is_directable,
)
from src.household import build_location_register

ROOT       = pathlib.Path(__file__).resolve().parent.parent
TRACKER_DB = ROOT / "data" / "tracker.db"

_REGISTER_COLS = ["holding", "symbol", "account", "sleeve", "case", "annual_benefit",
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
    conn.close()
    reg = build_location_register(pos, acct, sec, TAX_PROFILE,
                                  SLEEVE_LOCATION_PRIORITY, ACCOUNT_SHELTER_PRIORITY)
    return pos, acct, sec, reg


# ── Scores are authored config, never computed ─────────────────────────────────

def test_scores_are_read_from_config_verbatim():
    got = {g["key"]: g["score"] for g in ACTION_GROUPS}
    assert got == {
        "deploy_roth_cash": 10,
        "clear_roth_non_equity": 9,
        "relocate_loss_side": 9,
        "relocate_gain_side": 5,
        "thematic_sprawl": 2,
        "rollover_401k": 3,
    }


def test_statuses_are_read_from_config_verbatim():
    got = {g["key"]: g["status"] for g in ACTION_GROUPS}
    assert got == {
        "deploy_roth_cash": "act_now",
        "clear_roth_non_equity": "act_now",
        "relocate_loss_side": "act_now",
        "relocate_gain_side": "evaluate",
        "thematic_sprawl": "accepted",
        "rollover_401k": "blocked",
    }


# ── No two groups render identical prose (guard against templated filler) ───────

def test_no_two_groups_render_identical_prose():
    pos, acct, sec, reg = _live()
    deploy = build_roth_deploy_answer(pos, acct, sec, SLEEVE_LOCATION_PRIORITY)
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
    securities = pd.DataFrame([
        {"ticker": "AVUV", "sleeve_category": "us_small_value",   "is_in_saa": 1},
        {"ticker": "IEMG", "sleeve_category": "emerging_markets", "is_in_saa": 1},
        {"ticker": "SPAXX", "sleeve_category": "cash",            "is_in_saa": 1},
    ])
    accounts = pd.DataFrame([{"pseudonym": "r", "tax_treatment": "roth_ira", "display_name": "Roth"}])
    positions = pd.DataFrame([{
        "pseudonym": "r", "symbol": "SPAXX", "current_value": 1000.0,
        "total_gain_loss": float("nan"), "cost_basis_total": float("nan"),
    }])
    return positions, accounts, securities


def test_deploy_excludes_ineligible_even_at_top_priority():
    pos, acct, sec = _deploy_fixture()
    # hedged_equity ranked TOP (0) must still be excluded, not deployed into.
    prio = {"hedged_equity": 0, "us_small_core": 1, "emerging_markets": 2}
    ans = build_roth_deploy_answer(pos, acct, sec, prio, n_sleeves=2)
    sleeves = set(ans["table"]["sleeve"])
    assert not (sleeves & ROTH_DEPLOY_EXCLUDED_SLEEVES), "an ineligible sleeve reached the deploy answer"
    assert list(ans["table"]["ticker"]) == ["AVUV", "IEMG"]
    assert ans["idle_cash"] == 1000.0
    assert ans["table"]["dollar"].tolist() == [500.0, 500.0], "must split 50/50"


def test_deploy_answer_live_excludes_all_banned_sleeves():
    pos, acct, sec, _reg = _live()
    ans = build_roth_deploy_answer(pos, acct, sec, SLEEVE_LOCATION_PRIORITY)
    sleeves = set(ans["table"]["sleeve"])
    assert not (sleeves & ROTH_DEPLOY_EXCLUDED_SLEEVES)
    # No cash / hedged_equity / FI / real asset / international, explicitly.
    for banned in ("cash", "hedged_equity", "intl_developed", "intl_all_exus",
                   "core_fi_treasury", "tips", "real_assets_reit"):
        assert banned not in sleeves
    assert list(ans["table"]["ticker"]) == ["AVUV", "IEMG"]


# ── Informational groups render (deploy uses {value}; rollover is literal) ─────

def test_deploy_and_rollover_render():
    pos, acct, sec, reg = _live()
    deploy = build_roth_deploy_answer(pos, acct, sec, SLEEVE_LOCATION_PRIORITY)
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
    assert _fmt_dollars(77690) == "$77,690"
    assert _fmt_dollars(-51) == "-$51"        # negative is "-$51", never "- 51"
    assert _fmt_dollars(-51.4) == "-$51"


def test_render_prose_md_escapes_every_dollar():
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
    dep = build_roth_deploy_answer(pos, acct, sec, SLEEVE_LOCATION_PRIORITY)
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
    "deploy_roth_cash":      (382, 336),
    "clear_roth_non_equity": (477, 335),
    "relocate_loss_side":    (409, 363),
    "relocate_gain_side":    (201, 456),
    "thematic_sprawl":       (217, 465),
    "rollover_401k":         (341, 526),
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

def test_only_thematic_uses_matched_symbols_population():
    for g in ACTION_GROUPS:
        if g["key"] == "thematic_sprawl":
            assert g.get("population") == "matched_symbols"
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
    whose symbols are all mislocations (their two populations coincide)."""
    pos, acct, sec, reg = _live()
    for g in ACTION_GROUPS:
        if g["key"] in ("deploy_roth_cash", "rollover_401k", "thematic_sprawl"):
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


# ── Bug 3: no hardcoded headroom / dollar literals ─────────────────────────────

_DOLLAR_LITERAL = re.compile(r"\$[\d,]")


def test_gain_side_headroom_templated_no_literal():
    g4 = next(g for g in ACTION_GROUPS if g["key"] == "relocate_gain_side")
    assert not _DOLLAR_LITERAL.search(g4["pros"] + g4["cons"]), "group 4 must have no $ literal"
    assert "{headroom_total}" in g4["cons"] and "{headroom_remaining}" in g4["cons"]


def test_gain_side_prose_headroom_matches_computed():
    pos, acct, sec, reg = _live()
    hr = capital_gains_headroom(reg)
    g4 = next(g for g in ACTION_GROUPS if g["key"] == "relocate_gain_side")
    dep = build_roth_deploy_answer(pos, acct, sec, SLEEVE_LOCATION_PRIORITY)
    cons = render_prose_md(g4["cons"], resolve_placeholders(g4, pos, acct, sec, reg, roth_idle_cash=dep["idle_cash"]))
    assert escape_md(_fmt_dollars(hr["total"])) in cons
    assert escape_md(_fmt_dollars(hr["remaining"])) in cons


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

_EXPECTED_HOUSEHOLD = {
    "trad_ira_equity":       "$8,396",   # equity-sleeve holdings in the Traditional IRA
    "pretax_capacity":       "$10,194",  # Traditional IRA total
    "workplace_plan_value":  "$77,690",  # largest workplace-plan account (the RFUTX 401k)
    "pretax_capacity_after": "$87,884",  # the two summed
}


def test_household_placeholders_resolve_from_positions():
    from src.location_actions import _household_placeholders
    pos, acct, sec, _reg = _live()
    hp = _household_placeholders(pos, acct, sec)
    for k, expected in _EXPECTED_HOUSEHOLD.items():
        assert hp[k] == expected, f"{k}: got {hp[k]!r}, expected {expected!r}"


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
        {"pseudonym": ROLLOVER_SOURCE_PSEUDONYM, "symbol": "RFUTX", "current_value": 77690.18,
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
    assert hp["workplace_plan_value"] == "$77,690", (
        f"argmax leak — a larger second workplace account changed the figure: {hp['workplace_plan_value']}"
    )
    # after stays derived: pretax_capacity ($10,000) + workplace_plan_value ($77,690)
    assert hp["pretax_capacity"] == "$10,000"
    assert hp["pretax_capacity_after"] == "$87,690"


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
