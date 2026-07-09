"""Tests for the authored Asset Location action groups (src/location_actions.py).

Live-data tests build the real register from the newest CSV + tracker.db and
skip when those personal-mode inputs are absent. Pure tests (placeholder-raise,
deploy exclusion, score pinning) run everywhere.
"""
import pathlib
import sqlite3

import pandas as pd
import pytest

from src.location_actions import (
    ACTION_GROUPS,
    INFORMATIONAL_KEYS,
    ROTH_DEPLOY_EXCLUDED_SLEEVES,
    build_roth_deploy_answer,
    resolve_placeholders,
    render_prose,
    filter_register_for_group,
)
from src.location_config import (
    TAX_PROFILE,
    SLEEVE_LOCATION_PRIORITY,
    ACCOUNT_SHELTER_PRIORITY,
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
        resolved = resolve_placeholders(g, pos, acct, reg, roth_idle_cash=deploy["idle_cash"])
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
    reg = pd.DataFrame(columns=_REGISTER_COLS)

    resolved = resolve_placeholders(bad_group, pos, acct, reg)
    assert resolved["value"] is None, "empty subset must resolve to None, not 0"
    with pytest.raises(ValueError):
        render_prose(bad_group["pros"], resolved)


def test_resolved_zero_is_not_confused_with_unresolvable():
    # A genuinely-present holding with a computed value renders; only *missing*
    # data raises. (Sanity: a real symbol resolves.)
    bad_group = {
        "key": "synthetic", "symbols": ["VOO"], "case_filter": None,
        "accounts": None, "pros": "value is {value}", "cons": "",
    }
    pos = pd.DataFrame([{
        "pseudonym": "a", "symbol": "VOO", "current_value": 100.0,
        "total_gain_loss": 10.0, "cost_basis_total": 90.0,
    }])
    acct = pd.DataFrame([{"pseudonym": "a", "display_name": "A", "tax_treatment": "taxable"}])
    reg = pd.DataFrame(columns=_REGISTER_COLS)
    resolved = resolve_placeholders(bad_group, pos, acct, reg)
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

    d = resolve_placeholders(by_key["deploy_roth_cash"], pos, acct, reg,
                             roth_idle_cash=deploy["idle_cash"])
    deploy_pros = render_prose(by_key["deploy_roth_cash"]["pros"], d)
    assert f"${deploy['idle_cash']:,.0f}" in deploy_pros

    r = resolve_placeholders(by_key["rollover_401k"], pos, acct, reg)
    # rollover prose is all literals (no placeholders) -> renders unchanged.
    assert render_prose(by_key["rollover_401k"]["pros"], r) == by_key["rollover_401k"]["pros"]
