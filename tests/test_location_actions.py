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
        "relocate_gain_side": "evaluate",
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


def test_deploy_answer_uses_only_roth_map_sleeves():
    # Eligibility is structural now: no priority is injected and there is no
    # exclusion list. The answer can only contain roth-map sleeves with a ticker.
    pos, acct, sec = _deploy_fixture()
    ans = build_roth_deploy_answer(pos, acct, sec, n_sleeves=2)
    roth_map = SLEEVE_PRIORITY_BY_ACCOUNT_TYPE["roth_ira"]
    assert set(ans["table"]["sleeve"]).issubset(set(roth_map)), "a non-roth-map sleeve appeared"
    assert list(ans["table"]["ticker"]) == ["AVUV", "IEMG"]
    assert ans["idle_cash"] == 1000.0
    assert ans["table"]["dollar"].tolist() == [500.0, 500.0], "must split 50/50"


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
    ans = build_roth_deploy_answer(pos, acct, sec)
    sleeves = set(ans["table"]["sleeve"])
    for banned in ("cash", "hedged_equity", "intl_developed", "intl_all_exus",
                   "core_fi_treasury", "tips", "real_assets_reit"):
        assert banned not in sleeves
    assert list(ans["table"]["ticker"]) == ["AVUV", "IEMG"]
    # Ticker AND label from ONE key: AVUV -> us_small_value -> "US Small Value".
    labels = [sleeve_display_name(s) for s in ans["table"]["sleeve"]]
    assert labels[0] == "US Small Value", labels
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
    "deploy_roth_cash":          (382, 336),
    "clear_roth_non_equity":     (477, 335),
    "relocate_loss_side":        (409, 440),   # cons +{group_2_title} cross-ref sentence
    "relocate_gain_side":        (201, 372),   # cons rewritten: headroom-correct, no 15% claim
    "thematic_sprawl":           (217, 465),
    "rollover_401k":             (341, 526),
    "frozen_tod_income":         (382, 468),   # authored prose (pros re-pinned: muni dropped from the fund list)
    "saa_sleeves_taxable":       (261, 665),   # cons rewritten: capacity-constraint framing + {annual_benefit}
    "predeploy_stranded_equity": (328, 530),   # authored prose
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
    # The whole point of the fix: these are sub-threshold, yet must still be shown.
    assert not rows["surfaced"].any(), "SAA-sleeve rows are sub-threshold — that's why they were hidden"


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


def test_gain_side_headroom_templated_no_literal():
    g4 = next(g for g in ACTION_GROUPS if g["key"] == "relocate_gain_side")
    assert not _DOLLAR_LITERAL.search(g4["pros"] + g4["cons"]), "group 4 must have no $ literal"
    assert "{headroom_total}" in g4["cons"] and "{headroom_remaining}" in g4["cons"]


def test_gain_side_prose_headroom_matches_computed():
    pos, acct, sec, reg = _live()
    hr = capital_gains_headroom(reg)
    g4 = next(g for g in ACTION_GROUPS if g["key"] == "relocate_gain_side")
    dep = build_roth_deploy_answer(pos, acct, sec)
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
    ans = build_roth_deploy_answer(_pos, _acct, _sec)
    assert "thematic" not in set(ans["sleeves"]), "thematic leaked into the Roth deploy sleeves"
    assert "thematic" not in set(ans["table"]["sleeve"]), "thematic leaked into the Roth deploy table"


# ── Item 2: group 4 cons is headroom-correct (no 15% claim, no self-reference) ──

def test_gain_side_cons_templates_consumed_and_drops_false_claims():
    g4 = next(g for g in ACTION_GROUPS if g["key"] == "relocate_gain_side")
    assert "{headroom_consumed}" in g4["cons"], "group 4 must template the consumed headroom"
    for phrase in ("15%", "gain-side realization above"):
        assert phrase not in g4["cons"], f"group 4 cons must not contain {phrase!r} (factually wrong)"


# ── Item 3: group 3 cross-references group 2 by its LIVE title, not a hardcode ──

def test_loss_side_cons_references_current_group2_title():
    pos, acct, sec, reg = _live()
    dep = build_roth_deploy_answer(pos, acct, sec)
    g3 = next(g for g in ACTION_GROUPS if g["key"] == "relocate_loss_side")
    assert "{group_2_title}" in g3["cons"], "group 3 must template group 2's title, not hardcode it"
    cons = render_prose_md(g3["cons"], resolve_placeholders(g3, pos, acct, sec, reg, roth_idle_cash=dep["idle_cash"]))
    assert _group_title("clear_roth_non_equity") in cons, "rendered group 3 cons must show group 2's live title"
