"""Tests for Phase 25.6: Household View page data-prep helpers.

Tests the pure data-shaping functions in src/household.py that back the
Household View page — does NOT test Streamlit rendering directly.
"""
import pathlib
import sqlite3

import pandas as pd
import pytest

ROOT        = pathlib.Path(__file__).resolve().parent.parent
TRACKER_DB  = ROOT / "data" / "tracker.db"
HOLDINGS_CSV = ROOT / "data" / "uploads" / "Portfolio_Positions_May-27-2026.csv"

RAW_ACCOUNT_NUMBERS = [
    "636036466",
    "645582938",
    "636741436",
    "Z52398870",
    "74369",
    "628679469",
    "b4602e2a-4bba-4b4c-9058-2e5b0f58b1da",
]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _skip_if_no_holdings():
    if not HOLDINGS_CSV.exists():
        pytest.skip("Holdings CSV not present (personal-mode only)")


def _load_fixtures():
    from src.ingestion.fidelity import parse_fidelity_csv
    positions_df = parse_fidelity_csv(HOLDINGS_CSV)
    conn = sqlite3.connect(str(TRACKER_DB))
    accounts_df   = pd.read_sql_query("SELECT * FROM accounts", conn)
    securities_df = pd.read_sql_query("SELECT * FROM securities", conn)
    conn.close()
    return positions_df, accounts_df, securities_df


# ── build_location_flags ───────────────────────────────────────────────────────

def _mock_location_data(symbol, tax_efficiency, tax_treatment, managed_by="external"):
    pos = pd.DataFrame([{
        "account_number": "MOCK_ACCT",
        "symbol": symbol,
        "description": f"{symbol} mock",
        "current_value": 5000.0,
    }])
    acct = pd.DataFrame([{
        "account_number": "MOCK_ACCT",
        "display_name":   f"Mock {tax_treatment.title()} Account",
        "tax_treatment":  tax_treatment,
        "managed_by":     managed_by,
    }])
    sec = pd.DataFrame([{
        "ticker":          symbol,
        "name":            f"{symbol} Name",
        "tax_efficiency":  tax_efficiency,
        "sleeve_category": "mock_sleeve",
    }])
    return pos, acct, sec


def test_location_flag_low_efficiency_in_taxable():
    from src.household import build_location_flags
    pos, acct, sec = _mock_location_data("VNQ", "low", "taxable")
    flags = build_location_flags(pos, acct, sec)
    assert len(flags) == 1, "Low-efficiency asset in taxable account should be flagged"
    assert flags.iloc[0]["Symbol"] == "VNQ"
    assert "Tax-inefficient" in flags.iloc[0]["Note"]


def test_location_flag_high_efficiency_in_taxable_not_flagged():
    from src.household import build_location_flags
    pos, acct, sec = _mock_location_data("VOO", "high", "taxable")
    flags = build_location_flags(pos, acct, sec)
    assert len(flags) == 0, "High-efficiency asset in taxable account should NOT be flagged"


def test_location_flag_high_efficiency_in_roth_ira_flagged():
    from src.household import build_location_flags
    pos, acct, sec = _mock_location_data("VOO", "high", "roth_ira")
    flags = build_location_flags(pos, acct, sec)
    assert len(flags) == 1, "High-efficiency asset in Roth IRA should be flagged"
    assert "VOO" in flags["Symbol"].values
    assert "Mock Roth_Ira Account" in flags["Account"].values


def test_location_flag_high_efficiency_in_traditional_ira_flagged():
    from src.household import build_location_flags
    pos, acct, sec = _mock_location_data("VOO", "high", "traditional_ira")
    flags = build_location_flags(pos, acct, sec)
    assert len(flags) == 1


def test_location_flag_low_efficiency_in_roth_not_flagged():
    from src.household import build_location_flags
    pos, acct, sec = _mock_location_data("VGIT", "low", "roth_ira")
    flags = build_location_flags(pos, acct, sec)
    assert len(flags) == 0, "Low-efficiency in tax-advantaged is correct placement — no flag"


def test_location_flag_account_column_uses_display_name():
    from src.household import build_location_flags
    pos, acct, sec = _mock_location_data("VNQ", "low", "taxable")
    flags = build_location_flags(pos, acct, sec)
    assert "Account" in flags.columns
    assert "MOCK_ACCT" not in flags.to_string(), "Raw account_number must not appear in output"
    assert "Mock Taxable Account" in flags["Account"].values


def test_location_flag_medium_efficiency_not_flagged_anywhere():
    from src.household import build_location_flags
    pos, acct, sec = _mock_location_data("JEPQ", "medium", "taxable")
    flags = build_location_flags(pos, acct, sec)
    assert len(flags) == 0, "Medium-efficiency assets are not flagged"


# ── build_drift_table ──────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def alloc_df_total():
    _skip_if_no_holdings()
    from src.ingestion.fidelity import parse_fidelity_csv
    conn = sqlite3.connect(str(TRACKER_DB))
    pos   = parse_fidelity_csv(HOLDINGS_CSV)
    acct  = pd.read_sql_query("SELECT * FROM accounts", conn)
    sec   = pd.read_sql_query("SELECT * FROM securities", conn)
    comp  = pd.read_sql_query("SELECT * FROM fund_compositions", conn)
    saa   = pd.read_sql_query(
        "SELECT asset_class_id, name, target_weight FROM asset_classes "
        "WHERE parent_id IS NOT NULL AND target_weight > 0", conn
    )
    conn.close()
    from src.household import compute_household_allocation
    return compute_household_allocation(pos, acct, sec, comp, saa, mode="look_through", scope="total")


def test_drift_table_separates_saa_and_off_saa(alloc_df_total):
    from src.household import build_drift_table
    saa_tbl, off_tbl = build_drift_table(alloc_df_total)
    assert len(saa_tbl) > 0, "SAA table should have rows"
    assert len(off_tbl) > 0, "Off-SAA table should have rows"


def test_drift_table_saa_has_drift_column(alloc_df_total):
    from src.household import build_drift_table
    saa_tbl, off_tbl = build_drift_table(alloc_df_total)
    assert "Drift (pp)" in saa_tbl.columns
    assert "Drift (pp)" not in off_tbl.columns


def test_drift_table_saa_sorted_by_abs_drift_desc(alloc_df_total):
    from src.household import build_drift_table
    saa_tbl, _ = build_drift_table(alloc_df_total)
    drifts = saa_tbl["Drift (pp)"].abs()
    assert (drifts.diff().dropna() <= 0.01).all(), (
        "SAA table should be sorted by abs(drift_pp) descending"
    )


def test_drift_table_off_saa_sorted_by_dollar_value_desc(alloc_df_total):
    from src.household import build_drift_table
    _, off_tbl = build_drift_table(alloc_df_total)
    if len(off_tbl) > 1:
        dollars = off_tbl["Actual ($)"]
        assert (dollars.diff().dropna() <= 0).all(), (
            "Off-SAA table should be sorted by dollar_value descending"
        )


# ── build_account_breakdown ────────────────────────────────────────────────────

def test_account_breakdown_no_raw_account_numbers():
    _skip_if_no_holdings()
    from src.household import build_account_breakdown
    pos, acct, sec = _load_fixtures()
    result = build_account_breakdown(pos, acct, sec)
    result_str = result.to_string(index=False)
    for acct_num in RAW_ACCOUNT_NUMBERS:
        assert acct_num not in result_str, (
            f"Raw account number {acct_num!r} leaked into account breakdown output"
        )


def test_account_breakdown_has_display_name_column():
    _skip_if_no_holdings()
    from src.household import build_account_breakdown
    pos, acct, sec = _load_fixtures()
    result = build_account_breakdown(pos, acct, sec)
    assert "Account" in result.columns
    assert "account_number" not in result.columns


def test_account_breakdown_has_expected_columns():
    _skip_if_no_holdings()
    from src.household import build_account_breakdown
    pos, acct, sec = _load_fixtures()
    result = build_account_breakdown(pos, acct, sec)
    for col in ("Account", "Managed By", "Tax Treatment", "Dominant Sleeve", "Total AUM ($)"):
        assert col in result.columns, f"Missing column: {col!r}"


def test_account_breakdown_has_7_rows():
    _skip_if_no_holdings()
    from src.household import build_account_breakdown
    pos, acct, sec = _load_fixtures()
    result = build_account_breakdown(pos, acct, sec)
    assert len(result) == 7, f"Expected 7 account rows, got {len(result)}"


# ── should_render_household ────────────────────────────────────────────────────

def test_should_render_household_personal_mode(monkeypatch):
    import src.config as cfg
    monkeypatch.setattr(cfg, "is_demo", lambda: False)
    from src.household import should_render_household
    assert should_render_household() is True


def test_should_render_household_demo_mode(monkeypatch):
    import src.config as cfg
    monkeypatch.setattr(cfg, "is_demo", lambda: True)
    from src.household import should_render_household
    assert should_render_household() is False


# ── build_strategic_comparison ────────────────────────────────────────────────

def test_strategic_comparison_shape():
    from src.household import build_strategic_comparison
    df = build_strategic_comparison()
    assert df.shape == (9, 3), f"Expected (9, 3), got {df.shape}"
    assert list(df.columns) == ["Dimension", "SAA Framework", "Advisor Book"]


def test_strategic_comparison_dimension_labels():
    from src.household import build_strategic_comparison
    expected = [
        "Equity tilts",
        "Equity risk mgmt",
        "Fixed income role",
        "Fixed income duration",
        "Real assets",
        "Thematic",
        "Single stocks",
        "Active management",
        "Tax-location",
    ]
    assert list(build_strategic_comparison()["Dimension"]) == expected


def test_strategic_comparison_key_cells():
    from src.household import build_strategic_comparison
    df = build_strategic_comparison()
    assert df.iloc[0]["SAA Framework"] == "Factor (Quality, Value, Small Value)"
    assert df.iloc[2]["SAA Framework"] == "Duration ballast (Treasury + TIPS)"
    assert df.iloc[4]["SAA Framework"] == "Strategic 10% (commodities + REIT)"


# ── sleeve_display_name ───────────────────────────────────────────────────────

def test_sleeve_display_name_known_key():
    from src.household import sleeve_display_name
    assert sleeve_display_name("intl_all_exus") == "International (All ex-US)"
    assert sleeve_display_name("us_large_core") == "US Large Core"


def test_sleeve_display_name_fallback_title_case():
    from src.household import sleeve_display_name
    result = sleeve_display_name("something_unknown")
    assert result == "Something Unknown", f"Expected title-case fallback, got {result!r}"


# ── get_substitution_note ─────────────────────────────────────────────────────

def test_substitution_note_known():
    from src.household import get_substitution_note
    note = get_substitution_note("intl_all_exus")
    assert "Intl Dev + EM" in note
    assert "high" in note


def test_substitution_note_none():
    from src.household import get_substitution_note
    assert get_substitution_note("crypto") == "—"
    assert get_substitution_note("thematic") == "—"


# ── build_tax_drag_ranking ────────────────────────────────────────────────────

def test_tax_drag_reit_in_taxable_nonzero():
    from src.household import build_tax_drag_ranking
    pos = pd.DataFrame([{
        "account_number": "MOCK",
        "symbol": "VNQ",
        "description": "Vanguard REIT ETF",
        "current_value": 10000.0,
    }])
    acct = pd.DataFrame([{
        "account_number": "MOCK",
        "display_name":   "Mock Taxable",
        "tax_treatment":  "taxable",
        "managed_by":     "external",
    }])
    sec = pd.DataFrame([{
        "ticker":          "VNQ",
        "name":            "Vanguard Real Estate ETF",
        "tax_efficiency":  "low",
        "sleeve_category": "real_assets_reit",
    }])
    result = build_tax_drag_ranking(pos, acct, sec, top_n=10)
    assert len(result) == 1, "REIT in taxable should produce one flagged row"
    assert result.iloc[0]["Est. Annual Drag ($)"] > 0, "Estimated drag should be positive"


def test_tax_drag_equity_in_taxable_not_flagged():
    from src.household import build_tax_drag_ranking
    pos = pd.DataFrame([{
        "account_number": "MOCK",
        "symbol": "VOO",
        "description": "Vanguard S&P 500",
        "current_value": 10000.0,
    }])
    acct = pd.DataFrame([{
        "account_number": "MOCK",
        "display_name":   "Mock Taxable",
        "tax_treatment":  "taxable",
        "managed_by":     "self",
    }])
    sec = pd.DataFrame([{
        "ticker":          "VOO",
        "name":            "Vanguard S&P 500 ETF",
        "tax_efficiency":  "high",
        "sleeve_category": "us_large_core",
    }])
    result = build_tax_drag_ranking(pos, acct, sec, top_n=10)
    assert len(result) == 0, "High-efficiency in taxable should not be flagged"


# ── build_top_holdings ────────────────────────────────────────────────────────

def test_top_holdings_returns_five_rows():
    _skip_if_no_holdings()
    from src.household import build_top_holdings
    pos, acct, sec = _load_fixtures()
    result = build_top_holdings(pos, acct, sec, n=5)
    assert len(result) == 5, f"Expected 5 rows, got {len(result)}"
    assert list(result.columns) == ["Holding", "Symbol", "Account", "$ Value", "% of Household"]


def test_top_holdings_sorted_descending():
    _skip_if_no_holdings()
    from src.household import build_top_holdings
    pos, acct, sec = _load_fixtures()
    result = build_top_holdings(pos, acct, sec, n=5)
    values = result["$ Value"].tolist()
    assert values == sorted(values, reverse=True), "Holdings should be sorted by $ value descending"


# ── build_single_stock_summary ────────────────────────────────────────────────

def test_single_stock_summary_contains_mco():
    _skip_if_no_holdings()
    from src.household import build_single_stock_summary
    pos, _, sec = _load_fixtures()
    result = build_single_stock_summary(pos, sec)
    assert "MCO" in result, f"Expected MCO in summary, got: {result!r}"
    assert result.startswith("Single-stock exposure:")


# ── methodology_note_markdown ─────────────────────────────────────────────────

def test_methodology_note_returns_nonempty_string():
    from src.household import methodology_note_markdown
    note = methodology_note_markdown()
    assert isinstance(note, str) and len(note) > 0


def test_methodology_note_contains_heading():
    from src.household import methodology_note_markdown
    note = methodology_note_markdown()
    assert "Methodology Note — Household-Level Asset Location" in note


def test_methodology_note_contains_load_bearing_phrases():
    from src.household import methodology_note_markdown
    note = methodology_note_markdown()
    assert "99.5% is externally managed" in note
    assert "look-through over as-held" in note
    assert "off-SAA" in note
    assert "two-philosophy finding" in note


def test_methodology_note_contains_real_ticker():
    from src.household import methodology_note_markdown
    note = methodology_note_markdown()
    assert "RFUTX" in note, "Expected RFUTX ticker in methodology note"


# ── load_household_performance / build_performance_table ─────────────────────

_PERF_CSV  = ROOT / "data" / "seed" / "household_performance.csv"
_BENCH_CSV = ROOT / "data" / "seed" / "household_benchmarks.csv"


def test_load_performance_missing_file_returns_empty_df(tmp_path):
    from src.household import load_household_performance
    df = load_household_performance(tmp_path / "nonexistent.csv")
    assert df.empty
    assert list(df.columns) == [
        "account_pseudonym", "return_type", "period", "return_pct", "si_start_date", "source"
    ]


def test_load_performance_seed_file_row_count():
    from src.household import load_household_performance
    df = load_household_performance(_PERF_CSV)
    assert len(df) == 49, f"Expected 49 seed rows (28 TWR + 21 MWR), got {len(df)}"


def test_load_performance_empty_return_is_nan_not_zero():
    from src.household import load_household_performance
    df = load_household_performance(_PERF_CSV)
    # acct_taxable_01 TWR 3M is empty in the seed file — must be NaN, not 0.0
    row = df[
        (df["account_pseudonym"] == "acct_taxable_01")
        & (df["return_type"] == "TWR")
        & (df["period"] == "3M")
    ]
    assert not row.empty, "Expected acct_taxable_01 TWR 3M row to exist"
    assert pd.isna(row.iloc[0]["return_pct"]), "Empty return_pct must be NaN, not 0.0"


def _make_accounts_df_7():
    return pd.DataFrame({
        "pseudonym": [
            "acct_taxable_01", "acct_taxable_02", "acct_roth_01", "acct_wkpl_01",
            "acct_trad_ira_01", "acct_hsa_01", "acct_wkpl_02",
        ],
        "display_name": [
            "Individual Taxable (Self-Directed)", "Individual Taxable (TOD)", "Roth IRA",
            "Moody's PPP", "Traditional IRA", "HSA", "Workplace Plan",
        ],
        "managed_by": [
            "self", "external", "external", "external",
            "external", "external", "external",
        ],
    })


def _make_perf_df_twr():
    """4 accounts with TWR data; 3 absent accounts should show '—'."""
    rows = []
    for pseudo in ["acct_taxable_01", "acct_taxable_02", "acct_roth_01", "acct_wkpl_01"]:
        for period in ["1M", "3M", "YTD", "1Y", "3Y", "5Y", "SI"]:
            rows.append({
                "account_pseudonym": pseudo,
                "return_type": "TWR",
                "period": period,
                "return_pct": 5.0,
                "si_start_date": "2020-01-01" if period == "SI" else float("nan"),
                "source": "Fidelity (time-weighted, pre-tax)",
            })
    return pd.DataFrame(rows)


def _make_perf_df_mwr():
    """3 accounts with MWR data; 4 absent accounts should show '—'."""
    rows = []
    for pseudo in ["acct_taxable_01", "acct_taxable_02", "acct_roth_01"]:
        for period in ["1M", "3M", "YTD", "1Y", "3Y", "5Y", "SI"]:
            rows.append({
                "account_pseudonym": pseudo,
                "return_type": "MWR",
                "period": period,
                "return_pct": 8.0,
                "si_start_date": "2020-01-01" if period == "SI" else float("nan"),
                "source": "Fidelity (money-weighted, pre-tax)",
            })
    return pd.DataFrame(rows)


def test_build_performance_table_twr_returns_all_7_accounts():
    from src.household import build_performance_table
    tbl = build_performance_table(_make_perf_df_twr(), _make_accounts_df_7(), return_type="TWR")
    assert len(tbl) == 7, f"Expected 7 rows (all accounts), got {len(tbl)}"
    # The 3 absent accounts should have "—" for all period columns
    absent = tbl[tbl["Account"].isin(["Traditional IRA", "HSA", "Workplace Plan"])]
    assert len(absent) == 3
    for col in ("1M", "3M", "YTD", "1Y", "3Y", "5Y", "Since Inception"):
        assert (absent[col] == "—").all(), f"Absent account has non-dash in {col}"


def test_build_performance_table_mwr_returns_all_7_accounts():
    from src.household import build_performance_table
    tbl = build_performance_table(_make_perf_df_mwr(), _make_accounts_df_7(), return_type="MWR")
    assert len(tbl) == 7, f"Expected 7 rows, got {len(tbl)}"
    # Moody's PPP (acct_wkpl_01) and 3 absent accounts should be all "—"
    no_data = tbl[tbl["Account"].isin(["Moody's PPP", "Traditional IRA", "HSA", "Workplace Plan"])]
    assert len(no_data) == 4
    for col in ("1M", "3M", "YTD", "1Y", "3Y", "5Y", "Since Inception"):
        assert (no_data[col] == "—").all(), f"MWR-absent account has non-dash in {col}"


def test_build_performance_table_no_raw_account_numbers():
    from src.household import build_performance_table
    tbl = build_performance_table(_make_perf_df_twr(), _make_accounts_df_7(), return_type="TWR")
    tbl_str = tbl.to_string()
    for raw in RAW_ACCOUNT_NUMBERS:
        assert raw not in tbl_str, f"Raw account number {raw!r} leaked into performance table"
    assert "acct_taxable_01" not in tbl_str, "Pseudonym leaked into performance table"


def test_build_performance_table_self_directed_sorts_first():
    from src.household import build_performance_table
    tbl = build_performance_table(_make_perf_df_twr(), _make_accounts_df_7(), return_type="TWR")
    assert tbl.iloc[0]["Account"] == "Individual Taxable (Self-Directed)"
    assert tbl.iloc[0]["Managed By"] == "Self"


def test_load_benchmarks_missing_file_returns_empty_df(tmp_path):
    from src.household import load_household_benchmarks
    df = load_household_benchmarks(tmp_path / "nonexistent.csv")
    assert df.empty
    assert list(df.columns) == ["benchmark_name", "period", "return_pct", "as_of_range", "source"]


def test_load_benchmarks_seed_file_row_count():
    from src.household import load_household_benchmarks
    df = load_household_benchmarks(_BENCH_CSV)
    assert len(df) == 6, f"Expected 6 benchmark rows, got {len(df)}"


def test_build_benchmark_table_household_row_first():
    from src.household import load_household_benchmarks, build_benchmark_table
    df = load_household_benchmarks(_BENCH_CSV)
    tbl = build_benchmark_table(df)
    assert len(tbl) == 6
    assert tbl.iloc[0]["Benchmark"] == "Household (time-weighted)"
    assert "+24.52%" in tbl.iloc[0]["1Y Return"]


# ── CI grep: page file has no raw account numbers ────────────────────────────

def test_household_view_page_has_no_raw_account_numbers():
    page = ROOT / "pages" / "13_Household_View.py"
    assert page.exists(), "pages/13_Household_View.py must exist"
    content = page.read_text(encoding="utf-8", errors="ignore")
    for acct_num in RAW_ACCOUNT_NUMBERS:
        assert acct_num not in content, (
            f"Raw account number {acct_num!r} found in pages/13_Household_View.py"
        )
