"""Household account display, look-through, and aggregation helpers.

Accounts are keyed by pseudonym; raw Fidelity account numbers are never
stored or joined on (ingestion resolves them to pseudonyms). UI code renders
display_name via get_account_display() and never surfaces the join key.
"""
from pathlib import Path

import pandas as pd

_PERF_COLS  = ["account_pseudonym", "return_type", "period", "return_pct", "si_start_date", "source"]
_BENCH_COLS = ["benchmark_name", "period", "return_pct", "as_of_range", "source"]
_PERF_PERIODS = ("1M", "3M", "YTD", "1Y", "3Y", "5Y", "SI")


def get_account_display(pseudonym: str, accounts_df: pd.DataFrame) -> dict:
    """Return display metadata for an account pseudonym.

    Returns dict with keys: pseudonym, display_name, tax_treatment, managed_by.
    All values are strings; empty strings are returned for unknown accounts.
    """
    match = accounts_df[accounts_df["pseudonym"] == pseudonym]
    if match.empty:
        return {"pseudonym": "", "display_name": "", "tax_treatment": "", "managed_by": ""}
    row = match.iloc[0]
    return {
        "pseudonym":     str(row.get("pseudonym", "") or ""),
        "display_name":  str(row.get("display_name", "") or ""),
        "tax_treatment": str(row.get("tax_treatment", "") or ""),
        "managed_by":    str(row.get("managed_by", "") or ""),
    }


def look_through_position(
    symbol: str,
    dollar_value: float,
    compositions_df: pd.DataFrame,
    securities_df: pd.DataFrame,
) -> pd.DataFrame:
    """Decompose a holding into sleeve-level dollar values.

    If the symbol has rows in compositions_df, expands into N rows where each
    row's dollar_value = input * weight. Rows sum to input dollar_value
    (within floating-point rounding).

    If no composition exists, returns a single row using the security's
    sleeve_category from securities_df.

    Raises ValueError if the symbol has neither a composition nor a
    sleeve_category — this is a hard guard against silent mis-aggregation.
    """
    comp = compositions_df[compositions_df["fund_symbol"] == symbol]
    if not comp.empty:
        return pd.DataFrame({
            "sleeve":       comp["underlying_sleeve"].values,
            "dollar_value": dollar_value * comp["weight"].values,
        })

    sec_match = securities_df[securities_df["ticker"] == symbol]
    if not sec_match.empty:
        sleeve = sec_match.iloc[0].get("sleeve_category")
        if sleeve:
            return pd.DataFrame([{"sleeve": sleeve, "dollar_value": dollar_value}])

    raise ValueError(
        f"look_through_position: no composition or sleeve_category for symbol {symbol!r}. "
        "Ensure the symbol is loaded in securities and fund_compositions tables."
    )


def compute_household_allocation(
    positions_df: pd.DataFrame,
    accounts_df: pd.DataFrame,
    securities_df: pd.DataFrame,
    compositions_df: pd.DataFrame,
    saa_targets_df: pd.DataFrame,
    mode: str = "look_through",
    scope: str = "total",
) -> pd.DataFrame:
    """Resolve household positions to SAA sleeves and off-SAA buckets.

    Parameters
    ----------
    positions_df : ingestion output (pseudonym, symbol, current_value, ...)
    accounts_df  : accounts table with managed_by column
    securities_df: securities table with sleeve_category, is_in_saa, asset_class_id
    compositions_df: fund_compositions table
    saa_targets_df: asset_classes rows with parent_id IS NOT NULL
                    must have columns [asset_class_id, name, target_weight]
    mode         : 'look_through' — decompose fund compositions into underlying sleeves
                   'as_held'      — treat every holding by its own sleeve_category
    scope        : 'total'         — all accounts
                   'self_only'     — accounts where managed_by='self'
                   'external_only' — accounts where managed_by='external'

    Returns
    -------
    DataFrame with columns:
        sleeve, dollar_value, percent_weight, target_weight,
        drift_pp, drift_bps, rationale, is_off_saa

    sleeve names: SAA sleeves use asset_classes.name ("US Large Core", "Real Assets", …);
    off-SAA entries keep their sleeve_category string.
    """
    # ── 1. scope filter ────────────────────────────────────────────────────
    if scope == "self_only":
        valid_accts = set(
            accounts_df[accounts_df["managed_by"] == "self"]["pseudonym"].dropna()
        )
    elif scope == "external_only":
        valid_accts = set(
            accounts_df[accounts_df["managed_by"] == "external"]["pseudonym"].dropna()
        )
    else:
        valid_accts = None

    if valid_accts is not None:
        scoped = positions_df[positions_df["pseudonym"].isin(valid_accts)].copy()
    else:
        scoped = positions_df.copy()

    scoped_total = float(scoped["current_value"].sum())

    # ── 2. derive sleeve_category → SAA output name mapping ────────────────
    # Join securities (is_in_saa=1) with saa_targets_df on asset_class_id.
    # This naturally collapses real_assets_reit + real_assets_commodities → "Real Assets".
    saa_secs = (
        securities_df[securities_df["is_in_saa"] == 1][["sleeve_category", "asset_class_id"]]
        .drop_duplicates("sleeve_category")
    )
    saa_join = saa_secs.merge(
        saa_targets_df[["asset_class_id", "name", "target_weight"]],
        on="asset_class_id",
        how="left",
    )
    sleeve_to_saa_name: dict[str, str] = dict(
        zip(saa_join["sleeve_category"], saa_join["name"])
    )
    # SAA target per output name (deduplicated — Real Assets appears twice in saa_join)
    saa_name_to_target: dict[str, float] = (
        saa_join.drop_duplicates("name").set_index("name")["target_weight"].to_dict()
    )
    saa_output_names: set[str] = set(saa_name_to_target.keys())

    # ── 3. resolve each position to sleeve-level rows ──────────────────────
    parts: list[pd.DataFrame] = []
    for _, pos in scoped.iterrows():
        symbol = str(pos["symbol"])
        dollar = float(pos["current_value"])

        if mode == "look_through":
            parts.append(look_through_position(symbol, dollar, compositions_df, securities_df))
        else:
            sec = securities_df[securities_df["ticker"] == symbol]
            sleeve = (
                str(sec.iloc[0]["sleeve_category"])
                if not sec.empty and sec.iloc[0].get("sleeve_category")
                else "unknown"
            )
            parts.append(pd.DataFrame([{"sleeve": sleeve, "dollar_value": dollar}]))

    if not parts:
        empty = pd.DataFrame(
            columns=["sleeve", "dollar_value", "percent_weight", "target_weight",
                     "drift_pp", "drift_bps", "rationale", "is_off_saa"]
        )
        return empty

    resolved = pd.concat(parts, ignore_index=True)

    # ── 4. map sleeve_category → SAA output name (off-SAA keeps own name) ──
    resolved["output_sleeve"] = resolved["sleeve"].map(sleeve_to_saa_name).fillna(resolved["sleeve"])

    # ── 5. group by output sleeve ──────────────────────────────────────────
    grouped = (
        resolved.groupby("output_sleeve", sort=False)["dollar_value"]
        .sum()
        .reset_index()
        .rename(columns={"output_sleeve": "sleeve"})
    )

    # ── 6. internal sum assertion ──────────────────────────────────────────
    resolved_total = float(grouped["dollar_value"].sum())
    assert abs(resolved_total - scoped_total) < 1.0, (
        f"Sleeve sum {resolved_total:.2f} ≠ scoped portfolio total {scoped_total:.2f}"
    )

    # ── 7. weights, targets, drift ─────────────────────────────────────────
    grouped["percent_weight"] = grouped["dollar_value"] / scoped_total * 100
    grouped["is_off_saa"] = ~grouped["sleeve"].isin(saa_output_names)
    grouped["target_weight"] = grouped["sleeve"].map(saa_name_to_target).fillna(0.0)

    def _drift_pp(row: pd.Series) -> float:
        if row["is_off_saa"]:
            return 0.0
        return float(row["percent_weight"]) - float(row["target_weight"]) * 100.0

    grouped["drift_pp"] = grouped.apply(_drift_pp, axis=1)
    grouped["drift_bps"] = (grouped["drift_pp"] * 100).round(0)

    # ── 8. rationale ───────────────────────────────────────────────────────
    _ALLOWED = {"no_exposure", "on_target", "underweight", "overweight", "off_saa_exposure"}

    def _rationale(row: pd.Series) -> str:
        if row["is_off_saa"]:
            return "off_saa_exposure"
        if row["dollar_value"] == 0 and row["target_weight"] > 0:
            return "no_exposure"
        if abs(row["drift_pp"]) < 1.0:
            return "on_target"
        if row["drift_pp"] <= -1.0:
            return "underweight"
        return "overweight"

    grouped["rationale"] = grouped.apply(_rationale, axis=1)

    # ── 9. add SAA sleeves with zero exposure (no_exposure rows) ───────────
    present = set(grouped["sleeve"])
    zero_rows = []
    for saa_name, tgt in saa_name_to_target.items():
        if saa_name not in present and tgt > 0:
            zero_rows.append({
                "sleeve":         saa_name,
                "dollar_value":   0.0,
                "percent_weight": 0.0,
                "target_weight":  tgt,
                "is_off_saa":     False,
                "drift_pp":       -tgt * 100.0,
                "drift_bps":      round(-tgt * 10000.0, 0),
                "rationale":      "no_exposure",
            })
    if zero_rows:
        grouped = pd.concat([grouped, pd.DataFrame(zero_rows)], ignore_index=True)

    cols = ["sleeve", "dollar_value", "percent_weight", "target_weight",
            "drift_pp", "drift_bps", "rationale", "is_off_saa"]
    return (
        grouped[cols]
        .sort_values("dollar_value", ascending=False)
        .reset_index(drop=True)
    )


def household_summary(
    positions_df: pd.DataFrame,
    accounts_df: pd.DataFrame,
    as_of_date: str = "",
) -> dict:
    """Return high-level KPI dict for the Household View header.

    Keys: total_aum, account_count, as_of_date, self_aum, external_aum.
    """
    total_aum = float(positions_df["current_value"].sum())
    self_accts = set(
        accounts_df[accounts_df["managed_by"] == "self"]["pseudonym"].dropna()
    )
    ext_accts = set(
        accounts_df[accounts_df["managed_by"] == "external"]["pseudonym"].dropna()
    )
    self_aum = float(
        positions_df[positions_df["pseudonym"].isin(self_accts)]["current_value"].sum()
    )
    external_aum = float(
        positions_df[positions_df["pseudonym"].isin(ext_accts)]["current_value"].sum()
    )
    return {
        "total_aum":     round(total_aum, 2),
        "account_count": int(positions_df["pseudonym"].nunique()),
        "as_of_date":    as_of_date,
        "self_aum":      round(self_aum, 2),
        "external_aum":  round(external_aum, 2),
    }


# ── Presentation helpers (used by Household View page) ────────────────────────

def build_drift_table(
    alloc_df: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split allocation DataFrame into presentation-ready SAA and off-SAA tables.

    Returns (saa_table, off_saa_table):
      saa_table    — SAA sleeves sorted by abs(drift_pp) descending, with Drift column
      off_saa_table — Off-SAA sleeves sorted by dollar_value descending, with Substitutes For
    """
    saa = alloc_df[~alloc_df["is_off_saa"]].copy()
    off = alloc_df[alloc_df["is_off_saa"]].copy()

    saa = saa.sort_values("drift_pp", key=lambda s: s.abs(), ascending=False).reset_index(drop=True)
    off = off.sort_values("dollar_value", ascending=False).reset_index(drop=True)

    saa_tbl = pd.DataFrame({
        "Sleeve":      saa["sleeve"],
        "Actual (%)":  saa["percent_weight"].round(1),
        "Actual ($)":  saa["dollar_value"].round(0).astype(int),
        "Target (%)":  (saa["target_weight"] * 100).round(1),
        "Drift (pp)":  saa["drift_pp"].round(1),
        "Rationale":   saa["rationale"],
    })
    off_tbl = pd.DataFrame({
        "Sleeve":          off["sleeve"].map(sleeve_display_name),
        "Actual (%)":      off["percent_weight"].round(1),
        "Actual ($)":      off["dollar_value"].round(0).astype(int),
        "Substitutes For": off["sleeve"].map(get_substitution_note),
    })
    return saa_tbl, off_tbl


def build_account_breakdown(
    positions_df: pd.DataFrame,
    accounts_df: pd.DataFrame,
    securities_df: pd.DataFrame,
) -> pd.DataFrame:
    """Per-account exposure summary using display_name only.

    The pseudonym join key is dropped; only display_name identifies accounts.
    Columns: Account, Managed By, Tax Treatment, Dominant Sleeve, Total AUM ($).
    """
    sec = securities_df[["ticker", "sleeve_category"]].copy()
    acct = (
        accounts_df[["pseudonym", "display_name", "managed_by", "tax_treatment"]]
        .dropna(subset=["pseudonym"])
        .copy()
    )

    joined = positions_df.merge(sec, left_on="symbol", right_on="ticker", how="left")
    joined["sleeve_category"] = joined["sleeve_category"].fillna("unknown")

    totals = positions_df.groupby("pseudonym")["current_value"].sum().reset_index()

    sleeve_by_acct = (
        joined.groupby(["pseudonym", "sleeve_category"])["current_value"]
        .sum()
        .reset_index()
    )
    dom_idx = sleeve_by_acct.groupby("pseudonym")["current_value"].idxmax()
    dominant = (
        sleeve_by_acct.loc[dom_idx, ["pseudonym", "sleeve_category"]]
        .rename(columns={"sleeve_category": "dominant_sleeve"})
    )

    result = (
        totals
        .merge(dominant, on="pseudonym", how="left")
        .merge(acct, on="pseudonym", how="left")
        .drop(columns=["pseudonym"])
        .rename(columns={
            "current_value":  "Total AUM ($)",
            "display_name":   "Account",
            "managed_by":     "Managed By",
            "tax_treatment":  "Tax Treatment",
            "dominant_sleeve": "Dominant Sleeve",
        })
        .sort_values("Total AUM ($)", ascending=False)
        .reset_index(drop=True)
    )
    result["Dominant Sleeve"] = result["Dominant Sleeve"].map(sleeve_display_name)
    return result[["Account", "Managed By", "Tax Treatment", "Dominant Sleeve", "Total AUM ($)"]]


def build_location_flags(
    positions_df: pd.DataFrame,
    accounts_df: pd.DataFrame,
    securities_df: pd.DataFrame,
) -> pd.DataFrame:
    """Flag holdings with suboptimal tax location.

    Flags:
      - tax_efficiency='low'  AND tax_treatment='taxable'
        (tax-inefficient asset held in a taxable account)
      - tax_efficiency='high' AND tax_treatment in ('traditional_ira', 'roth_ira')
        (tax-efficient asset occupying scarce tax-advantaged space)

    Returns a DataFrame with columns:
        Holding, Symbol, Account, Tax Efficiency, Account Type, Note
    Account column uses display_name, never the pseudonym join key.
    """
    sec = securities_df[["ticker", "name", "tax_efficiency"]].copy()
    acct = (
        accounts_df[["pseudonym", "display_name", "tax_treatment"]]
        .dropna(subset=["pseudonym"])
        .copy()
    )

    joined = (
        positions_df
        .merge(sec, left_on="symbol", right_on="ticker", how="left")
        .merge(acct, on="pseudonym", how="left")
    )

    flags: list[dict] = []
    for _, row in joined.iterrows():
        te = row.get("tax_efficiency")
        tt = row.get("tax_treatment")
        if not te or not tt:
            continue

        note = None
        if te == "low" and tt == "taxable":
            note = "Tax-inefficient asset in taxable account"
        elif te == "high" and tt in ("traditional_ira", "roth_ira"):
            note = "Tax-efficient asset in tax-advantaged account"

        if note:
            flags.append({
                "Holding":        str(row.get("name") or row.get("description") or row["symbol"]),
                "Symbol":         row["symbol"],
                "Account":        str(row.get("display_name") or ""),
                "Tax Efficiency": te,
                "Account Type":   tt,
                "Note":           note,
            })

    cols = ["Holding", "Symbol", "Account", "Tax Efficiency", "Account Type", "Note"]
    return pd.DataFrame(flags, columns=cols) if flags else pd.DataFrame(columns=cols)


def build_strategic_comparison() -> pd.DataFrame:
    """Return the SAA-vs-advisor-book comparison table.

    Static editorial content — not derived from holdings.
    """
    rows = [
        ("Equity tilts",          "Factor (Quality, Value, Small Value)",       "Market-cap broad"),
        ("Equity risk mgmt",      "Diversification",                            "Active hedging overlays"),
        ("Fixed income role",     "Duration ballast (Treasury + TIPS)",         "Yield generation (credit, floating, HY)"),
        ("Fixed income duration", "Intermediate Treasury + TIPS",               "Mixed; mostly credit-spread risk"),
        ("Real assets",           "Strategic 10% (commodities + REIT)",         "Token gestures (gold + REIT, ~1.5%)"),
        ("Thematic",              "None (Phase 10 Asset Eval declined Bitcoin)", "$9.7k across ~12 themes"),
        ("Single stocks",         "None",                                       "One position (MCO)"),
        ("Active management",     "None",                                       "~$25k (hedged equity + multi-sector FI + GAOSX)"),
        ("Tax-location",          "Designed-in",                                "Not the priority"),
    ]
    return pd.DataFrame(rows, columns=["Dimension", "SAA Framework", "Advisor Book"])


def should_render_household() -> bool:
    """Return True when running in personal mode (household data is available)."""
    from src.config import is_demo
    return not is_demo()


# ── Sleeve display names ───────────────────────────────────────────────────────

_SLEEVE_DISPLAY_NAMES: dict[str, str] = {
    "us_large_core":           "US Large Core",
    "us_large_quality":        "US Large Quality",
    "us_large_value":          "US Large Value",
    "us_large_growth":         "US Large Growth",
    "us_small_core":           "US Small Core",
    "us_small_value":          "US Small Value",
    "us_mid_cap":              "US Mid Cap",
    "us_sector_tech":          "US Sector — Tech",
    "us_sector_healthcare":    "US Sector — Healthcare",
    "intl_developed":          "International Developed",
    "intl_all_exus":           "International (All ex-US)",
    "emerging_markets":        "Emerging Markets",
    "core_fi_treasury":        "Core FI — Treasury",
    "core_fi_credit":          "Core FI — Credit",
    "high_yield_fi":           "High Yield FI",
    "high_yield_muni":         "High Yield Muni",
    "floating_rate":           "Floating Rate",
    "multi_sector_fi":         "Multi-Sector FI",
    "tips":                    "TIPS",
    "real_assets_commodities": "Real Assets — Commodities",
    "real_assets_reit":        "Real Assets — REIT",
    "real_assets_gold":        "Real Assets — Gold",
    "hedged_equity":           "Hedged Equity",
    "liquid_alt":              "Liquid Alternatives",
    "thematic":                "Thematic",
    "multi_asset":             "Multi-Asset",
    "target_date":             "Target Date",
    "cash":                    "Cash",
    "single_stock":            "Single Stock",
    "crypto":                  "Crypto",
}


def sleeve_display_name(sleeve_key: str) -> str:
    """Map a snake_case sleeve key to a UI display label.

    Falls back to title-cased, space-separated form for unknown keys.
    """
    return _SLEEVE_DISPLAY_NAMES.get(sleeve_key, sleeve_key.replace("_", " ").title())


# ── Off-SAA substitution mapping ──────────────────────────────────────────────

_OFF_SAA_SUBSTITUTIONS: dict[str, tuple[str, str]] = {
    "intl_all_exus":        ("Intl Dev + EM", "high"),
    "us_small_core":        ("US Small Value", "medium"),
    "us_mid_cap":           ("US Large Core", "low"),
    "us_large_growth":      ("US Large Core", "medium"),
    "us_sector_tech":       ("US Large Core", "low"),
    "us_sector_healthcare": ("US Large Core", "low"),
    "core_fi_credit":       ("Core FI — Treasury", "low"),
    "high_yield_fi":        ("—", "none"),
    "high_yield_muni":      ("—", "none"),
    "floating_rate":        ("—", "none"),
    "multi_sector_fi":      ("Core FI — Treasury", "low"),
    "hedged_equity":        ("US Large Core", "medium"),
    "liquid_alt":           ("Cash", "medium"),
    "real_assets_gold":     ("Real Assets — Commodities", "medium"),
    "thematic":             ("—", "none"),
    "multi_asset":          ("—", "none"),
    "target_date":          ("—", "none"),
    "single_stock":         ("—", "none"),
    "crypto":               ("—", "none"),
}


def get_substitution_note(sleeve_key: str) -> str:
    """Return a formatted substitution note for an off-SAA sleeve key.

    Returns a string like "Intl Dev + EM (high)" or "—" if no substitute.
    """
    entry = _OFF_SAA_SUBSTITUTIONS.get(sleeve_key)
    if entry is None or entry[1] == "none":
        return "—"
    saa_equiv, equiv_level = entry
    return f"{saa_equiv} ({equiv_level})"


# ── Tax drag ranking ───────────────────────────────────────────────────────────

_SLEEVE_ASSUMED_YIELD: dict[str, float] = {
    "real_assets_reit":        0.040,
    "real_assets_commodities": 0.015,
    "real_assets_gold":        0.000,
    "high_yield_fi":           0.060,
    "high_yield_muni":         0.045,
    "floating_rate":           0.055,
    "multi_sector_fi":         0.040,
    "core_fi_credit":          0.035,
    "core_fi_treasury":        0.040,
    "tips":                    0.025,
    "cash":                    0.045,
    "hedged_equity":           0.060,
    "single_stock":            0.020,
    "crypto":                  0.000,
    "liquid_alt":              0.020,
}
_EQUITY_DEFAULT_YIELD = 0.018
_MARGINAL_RATE = 0.41


def _assumed_yield(sleeve_key: str) -> float:
    return _SLEEVE_ASSUMED_YIELD.get(sleeve_key, _EQUITY_DEFAULT_YIELD)


def build_tax_drag_ranking(
    positions_df: pd.DataFrame,
    accounts_df: pd.DataFrame,
    securities_df: pd.DataFrame,
    top_n: int = 10,
) -> pd.DataFrame:
    """Rank suboptimally-located holdings by estimated annual tax drag.

    Columns: Holding, Symbol, Account, Current Issue, Suggested Move, Est. Annual Drag ($)
    Sorted by Est. Annual Drag descending, capped at top_n.

    Drag formula: dollar_value * assumed_yield * abs(marginal_rate - alt_rate)
      low-efficiency in taxable:           alt_rate = 0.20 (LTCG + state)
      high-efficiency in tax-advantaged:   alt_rate = 0.41 (marginal; drag = 0 by formula)
    Rows with zero estimated drag are excluded.
    """
    sec = securities_df[["ticker", "name", "tax_efficiency", "sleeve_category"]].copy()
    acct = (
        accounts_df[["pseudonym", "display_name", "tax_treatment"]]
        .dropna(subset=["pseudonym"])
        .copy()
    )
    joined = (
        positions_df
        .merge(sec, left_on="symbol", right_on="ticker", how="left")
        .merge(acct, on="pseudonym", how="left")
    )

    rows: list[dict] = []
    for _, row in joined.iterrows():
        te = str(row.get("tax_efficiency") or "")
        tt = str(row.get("tax_treatment") or "")
        dollar = float(row.get("current_value", 0) or 0)
        sleeve = str(row.get("sleeve_category") or "")
        if not te or not tt:
            continue

        issue = None
        suggested = None
        alt_rate = 0.0

        if te == "low" and tt == "taxable":
            issue = "Low-efficiency in taxable"
            suggested = "Move to Traditional IRA or Roth IRA"
            alt_rate = 0.20
        elif te == "high" and tt in ("traditional_ira", "roth_ira"):
            issue = "High-efficiency in tax-advantaged"
            suggested = "Move to Taxable"
            alt_rate = _MARGINAL_RATE

        if issue is None:
            continue

        drag = dollar * _assumed_yield(sleeve) * abs(_MARGINAL_RATE - alt_rate)
        if drag <= 0:
            continue

        rows.append({
            "Holding":              str(row.get("name") or row.get("description") or row["symbol"]),
            "Symbol":               row["symbol"],
            "Account":              str(row.get("display_name") or ""),
            "Current Issue":        issue,
            "Suggested Move":       suggested,
            "Est. Annual Drag ($)": round(drag, 0),
        })

    cols = ["Holding", "Symbol", "Account", "Current Issue", "Suggested Move", "Est. Annual Drag ($)"]
    if not rows:
        return pd.DataFrame(columns=cols)

    return (
        pd.DataFrame(rows, columns=cols)
        .sort_values("Est. Annual Drag ($)", ascending=False)
        .head(top_n)
        .reset_index(drop=True)
    )


# ── Concentration panel ────────────────────────────────────────────────────────

def _infer_issuer(description: str, symbol: str) -> str:
    """Infer fund family / issuer from holding description or symbol."""
    desc = str(description).upper()
    if "VANGUARD" in desc:
        return "Vanguard"
    if "ISHARES" in desc or "BLACKROCK" in desc:
        return "iShares / BlackRock"
    if "JPMORGAN" in desc or "JP MORGAN" in desc or "J.P. MORGAN" in desc:
        return "JPMorgan"
    if "FIDELITY" in desc:
        return "Fidelity"
    if "AVANTIS" in desc:
        return "Avantis"
    if "SCHWAB" in desc:
        return "Schwab"
    if "INVESCO" in desc:
        return "Invesco"
    if "SPDR" in desc or "STATE STREET" in desc:
        return "State Street / SPDR"
    if "INNOVATOR" in desc:
        return "Innovator"
    return "Other"


def build_top_holdings(
    positions_df: pd.DataFrame,
    accounts_df: pd.DataFrame,
    securities_df: pd.DataFrame,
    n: int = 5,
) -> pd.DataFrame:
    """Return top-N holdings by dollar value.

    Columns: Holding, Symbol, Account, $ Value, % of Household
    """
    total_aum = float(positions_df["current_value"].sum())
    sec = securities_df[["ticker", "name"]].copy()
    acct = accounts_df[["pseudonym", "display_name"]].copy()

    df = (
        positions_df
        .merge(sec, left_on="symbol", right_on="ticker", how="left")
        .merge(acct, on="pseudonym", how="left")
        .sort_values("current_value", ascending=False)
        .head(n)
        .reset_index(drop=True)
    )

    desc_col = df.get("description", pd.Series("", index=df.index)).fillna("")
    holding_names = df["name"].fillna(desc_col).fillna(df["symbol"])

    return pd.DataFrame({
        "Holding":         holding_names,
        "Symbol":          df["symbol"],
        "Account":         df["display_name"].fillna(""),
        "$ Value":         df["current_value"].round(0).astype(int),
        "% of Household":  (df["current_value"] / total_aum * 100).round(1),
    })


def build_issuer_concentration(
    positions_df: pd.DataFrame,
    n: int = 3,
) -> pd.DataFrame:
    """Return top-N issuers by dollar value.

    Columns: Issuer, $ Total, % of Household
    """
    total_aum = float(positions_df["current_value"].sum())
    desc_col = (
        positions_df["description"]
        if "description" in positions_df.columns
        else pd.Series("", index=positions_df.index)
    )
    issuers = [
        _infer_issuer(str(desc), str(sym))
        for desc, sym in zip(desc_col.fillna(""), positions_df["symbol"])
    ]
    df = positions_df.copy()
    df["issuer"] = issuers

    grouped = (
        df.groupby("issuer")["current_value"]
        .sum()
        .reset_index()
        .sort_values("current_value", ascending=False)
        .head(n)
        .reset_index(drop=True)
    )
    return pd.DataFrame({
        "Issuer":          grouped["issuer"],
        "$ Total":         grouped["current_value"].round(0).astype(int),
        "% of Household":  (grouped["current_value"] / total_aum * 100).round(1),
    })


def build_single_stock_summary(
    positions_df: pd.DataFrame,
    securities_df: pd.DataFrame,
) -> str:
    """Return a one-line single-stock exposure summary.

    Example: "Single-stock exposure: $5,000 (2.5%) — Moody's (MCO)."
    """
    sec = securities_df[["ticker", "name", "sleeve_category"]].copy()
    single_stocks = (
        positions_df
        .merge(sec, left_on="symbol", right_on="ticker", how="left")
        .query("sleeve_category == 'single_stock'")
    )

    if single_stocks.empty:
        return "Single-stock exposure: none."

    total_aum = float(positions_df["current_value"].sum())
    ss_total = float(single_stocks["current_value"].sum())
    pct = ss_total / total_aum * 100

    names = []
    for _, row in single_stocks.iterrows():
        holding_name = str(row.get("name") or row["symbol"])
        sym = str(row["symbol"])
        names.append(f"{holding_name} ({sym})")

    return f"Single-stock exposure: ${ss_total:,.0f} ({pct:.1f}%) — {', '.join(names)}."


def methodology_note_markdown() -> str:
    """Returns the household asset-location methodology note as a markdown string.
    Personal-mode editorial content — must never render in demo mode."""
    return """\
### Methodology Note — Household-Level Asset Location
*An allocator's analysis of an inherited, multi-manager household*

**The setup.** I'm a 27-year-old CFA charterholder on an allocator track. My mother, also a CFA, has managed the bulk of my investable assets for years at no fee — a genuine gift, and a good portfolio. As I built out my own strategic asset allocation framework, I wanted to answer a question most retail tooling can't: not "how is each account doing," but "what does my entire household look like as a single allocation, and how far does it sit from the framework I would run if I controlled every dollar?" This note documents what the household-aggregation view revealed and the methodology decisions behind it.

**The structural fact that shapes everything.** Of ~$202,450 across seven accounts, exactly $1,000 — the self-directed brokerage I opened in May 2026 — is under my discretion. The other 99.5% is externally managed: six accounts run by my mother, plus one employer-default target-date fund (RFUTX) on auto-pilot in a profit-sharing plan. This means household drift against my SAA is largely *observed*, not *actionable*. The framework is mine; the dollars mostly aren't. A tool that surfaced drift as if I could rebalance it would imply a control I don't have, so this page distinguishes self-directed (actionable) exposure from externally-managed (observed) context throughout.

**Methodology decision 1 — look-through over as-held.** Three holdings are funds-of-funds: RFUTX (American Funds 2060, my single largest position at ~$66k), GAOSX (JPMorgan Global Allocation), and a Fidelity Freedom 2065 fund. Treating these as opaque "target-date" or "multi-asset" blobs would misstate the household's true exposure by roughly a third. I decompose each into underlying sleeves at approximate prospectus glide-path weights, sourced and flagged as estimates. The look-through reveals that a large share of the household's apparent US-equity and international-developed exposure is actually one workplace fund's glide path, not discretionary positioning — invisible at the account level, obvious once decomposed.

**Methodology decision 2 — honest off-SAA reporting over forced mapping.** My SAA prescribes factor tilts: quality (SPHQ), value (VTV), small-value (AVUV). The advisor book holds broad-market equivalents — IXUS/VXUS for international, broad small-core rather than small-value, a range of thematic and hedged-equity strategies. The temptation is to map these onto the nearest SAA sleeve and call it close enough. I don't. Exposures that don't map cleanly to a factor-tilted SAA target are reported as "off-SAA" — held, real, but counted against no target. Roughly $87k of the household sits in off-SAA categories: broad international, small-core, hedged equity (JHEQX, JEPI, JEPQ, HELO), thematic ETFs, multi-sector and high-yield fixed income. Forcing these into SAA sleeves would manufacture false precision; reporting them honestly is the analytical point.

**Methodology decision 3 — substitution analysis as a separate layer.** Off-SAA doesn't mean unwanted. IXUS + VXUS is a high-equivalence substitute for my VEA+IEMG international target. Broad small-core is a medium-equivalence stand-in for small-value (same size factor, different value tilt). Hedged equity is a medium substitute for US large core with downside dampening. Multi-sector credit is a low-equivalence substitute for Treasury duration — it generates yield but doesn't provide the recession ballast my framework wants from fixed income. Separating "off-SAA" from "off-SAA but a reasonable substitute" keeps the drift numbers honest while acknowledging that two allocators can express the same view through different instruments.

**The two-philosophy finding.** The deepest takeaway isn't a number — it's that my mother's book and my framework are two coherent, defensible philosophies that happen to differ on nearly every axis. Her portfolio is a risk-aware, income-focused diversifier with explicit downside management: broad-market equity core, active hedged-equity overlays that price left-tail risk, a credit-tilted fixed-income book built for yield rather than duration ballast. Mine is academically-grounded: factor tilts, Treasury and TIPS for ballast, commodities and REITs at a strategic 10%, no hedging overlay, no thematics. Neither is wrong. Hers optimizes for "won't blow up, generates income, sleeps well at night" — exactly what a careful CFA managing a young family member's money should do. Mine optimizes for long-horizon factor premia and tax-aware location, which my situation arguably justifies. The framework's job here is to make the gap legible, not to declare a winner.

**The asset-location wrinkle.** Aggregating across accounts surfaces a tax-location pattern that's invisible account-by-account: low-tax-efficiency assets (REITs, commodities, multi-sector and high-yield bond funds, even a Bitcoin ETF) sit in the taxable account, while tax-efficient broad-equity ETFs sit in the Roth and Traditional IRAs. The textbook prescription is the reverse — shelter the tax-inefficient assets, hold the efficient ones in taxable. This isn't a criticism of the advisor; it's a common artifact of optimizing each account for diversification rather than optimizing location across the household. The page estimates the annual tax drag of the largest mislocations, ranked. The dollar figures are modest at current balances, but the pattern is the kind of thing that compounds and that only a household-level view catches.

**What this is and isn't.** This is a personal analytical tool, run on my own real positions, kept private. It's not investment advice, not a critique of a generous family arrangement, and not a coordination mandate — most of these accounts aren't mine to trade. It's an allocator's attempt to see his own total picture clearly, to articulate where his framework and his inherited reality diverge, and to be honest about which of those divergences are actionable and which are simply the shape of the household he has.
"""


def load_household_performance(csv_path) -> pd.DataFrame:
    """Load manually-entered Fidelity performance figures from seed CSV.

    Returns a tidy DataFrame with columns:
        account_pseudonym, return_type, period, return_pct, si_start_date, source

    Empty return_pct cells remain NaN — they are not-reported, not zero.
    Tolerates missing file — returns an empty DataFrame with correct columns.
    """
    p = Path(csv_path)
    if not p.exists():
        return pd.DataFrame(columns=_PERF_COLS)
    try:
        df = pd.read_csv(p, comment="#")
        return df[_PERF_COLS].copy()
    except Exception:
        return pd.DataFrame(columns=_PERF_COLS)


def build_performance_table(
    performance_df: pd.DataFrame,
    accounts_df: pd.DataFrame,
    return_type: str = "TWR",
) -> pd.DataFrame:
    """Pivot performance data for the given return_type to one row per account.

    Returns:
        Account | Managed By | 1M | 3M | YTD | 1Y | 3Y | 5Y | Since Inception | SI Start

    All accounts in accounts_df appear; those with no data show "—".
    Self-directed account sorts first, then alphabetically.
    Return columns use "+N.NN%" format; missing values show "—".
    """
    accts = accounts_df[["pseudonym", "display_name", "managed_by"]].copy()

    if not performance_df.empty:
        filtered = performance_df[performance_df["return_type"] == return_type].copy()
    else:
        filtered = pd.DataFrame(columns=_PERF_COLS)

    # Collect SI start dates per account
    si_map: dict = {}
    if not filtered.empty:
        si_rows = filtered[filtered["period"] == "SI"][["account_pseudonym", "si_start_date"]]
        si_map = (
            si_rows.dropna(subset=["si_start_date"])
            .drop_duplicates("account_pseudonym")
            .set_index("account_pseudonym")["si_start_date"]
            .to_dict()
        )

    # Pivot return_pct by period, indexed by account_pseudonym
    if not filtered.empty:
        pivot = filtered.pivot_table(
            index="account_pseudonym",
            columns="period",
            values="return_pct",
            aggfunc="first",
        )
        period_lookup = {p: pivot[p].to_dict() for p in pivot.columns}
    else:
        period_lookup = {}

    # Build result — left-join from accounts so all appear
    result = accts.copy()
    for period in _PERF_PERIODS:
        lookup = period_lookup.get(period, {})
        result[period] = result["pseudonym"].map(lookup)

    result["SI Start"] = result["pseudonym"].map(si_map)

    # Rename display columns
    result = result.rename(columns={
        "display_name": "Account",
        "managed_by":   "Managed By",
        "SI":           "Since Inception",
    })
    result["Managed By"] = (
        result["Managed By"]
        .map({"self": "Self", "external": "Advisor"})
        .fillna(result["Managed By"])
    )

    # Sort: self-directed first, then alphabetical
    result["_sort"] = result["Managed By"].map({"Self": 0}).fillna(1)
    result = (
        result.sort_values(["_sort", "Account"])
        .drop(columns=["_sort", "pseudonym"])
        .reset_index(drop=True)
    )

    # Format return columns: "+N.NN%" or "—" for NaN
    for col in ("1M", "3M", "YTD", "1Y", "3Y", "5Y", "Since Inception"):
        result[col] = result[col].apply(
            lambda x: "—" if (x is None or (isinstance(x, float) and pd.isna(x))) else f"+{x:.2f}%"
        )
    result["SI Start"] = result["SI Start"].apply(
        lambda x: "—" if (x is None or (isinstance(x, float) and pd.isna(x))) else str(x)
    )

    return result[["Account", "Managed By", "1M", "3M", "YTD", "1Y", "3Y", "5Y", "Since Inception", "SI Start"]]


def load_household_benchmarks(csv_path) -> pd.DataFrame:
    """Load household-vs-benchmark comparison data from seed CSV.

    Returns a DataFrame with columns:
        benchmark_name, period, return_pct, as_of_range, source

    Tolerates missing file — returns empty DataFrame with correct columns.
    """
    p = Path(csv_path)
    if not p.exists():
        return pd.DataFrame(columns=_BENCH_COLS)
    try:
        df = pd.read_csv(p, comment="#")
        return df[_BENCH_COLS].copy()
    except Exception:
        return pd.DataFrame(columns=_BENCH_COLS)


def build_benchmark_table(benchmarks_df: pd.DataFrame) -> pd.DataFrame:
    """Build a display-ready benchmark comparison table.

    Returns: Benchmark | 1Y Return
    Row order is preserved from the CSV (Household first, then equity, then bonds).
    """
    if benchmarks_df.empty:
        return pd.DataFrame()
    result = benchmarks_df[["benchmark_name", "return_pct"]].copy()
    result = result.rename(columns={"benchmark_name": "Benchmark"})
    result["1Y Return"] = result["return_pct"].apply(
        lambda x: "—" if pd.isna(x) else f"+{x:.2f}%"
    )
    return result[["Benchmark", "1Y Return"]].reset_index(drop=True)
