"""Trade Log page — trades, theses, and themes."""
import json
from collections import defaultdict
from datetime import date as dt_date

import pandas as pd
import streamlit as st

st.set_page_config(page_title="Trade Log", layout="wide")

from src.asof import as_of_banner
from src.config import get_demo_banner_text, IS_DEMO, is_write_enabled
from src.db import get_connection
from src.ui_helpers import render_footer, render_page_header, write_guard_toast
render_page_header()


STATUS_COLOR = {
    "active":      "#2d6a4f",
    "closed":      "#888888",
    "invalidated": "#c0392b",
}


def _stars(n: int) -> str:
    return "★" * int(n) + "☆" * (5 - int(n))


def _safe_md(text):
    if text:
        st.markdown(text.replace("$", r"\$"))


def _safe_cap(text):
    if text:
        st.caption(text.replace("$", r"\$"))


def _badge(status: str) -> str:
    c = STATUS_COLOR.get(status, "#888")
    return (
        f'<span style="background:{c}18;color:{c};'
        f'padding:1px 8px;border-radius:4px;'
        f'font-size:0.78rem;font-weight:500">{status}</span>'
    )


def _pills(names_csv: str) -> str:
    if not names_csv:
        return ""
    out = []
    for t in names_csv.split(", "):
        out.append(
            f'<span style="background:#e8eef4;color:#3D5A80;'
            f'padding:1px 8px;border-radius:4px;'
            f'font-size:0.76rem;margin-right:4px">{t.strip()}</span>'
        )
    return "".join(out)


# ── Data loading ──────────────────────────────────────────────────────────────

def load_all():
    with get_connection() as conn:
        trades = [dict(r) for r in conn.execute("""
            SELECT tr.trade_id, tr.account_id, tr.trade_date, tr.action, tr.ticker,
                   tr.shares, tr.price, tr.fees, tr.notes,
                   COALESCE(tr.lot_source, 'initial') AS lot_source,
                   pt.title  AS position_thesis,
                   it.title  AS investment_thesis
            FROM trades tr
            LEFT JOIN theses pt ON tr.thesis_id = pt.thesis_id
            LEFT JOIN theses it ON pt.parent_thesis_id = it.thesis_id
            ORDER BY tr.trade_date DESC, tr.trade_id DESC
        """).fetchall()]

        inv_theses = [dict(r) for r in conn.execute("""
            SELECT th.thesis_id, th.title, th.view_summary, th.conviction,
                   th.status, th.horizon_months, th.exit_conditions,
                   th.invalidation_conditions, th.expected_return_scenario,
                   th.target_sleeves, th.created_at,
                   GROUP_CONCAT(tm.name, ', ') AS theme_names
            FROM theses th
            LEFT JOIN thesis_themes tt ON th.thesis_id = tt.thesis_id
            LEFT JOIN themes tm ON tt.theme_id = tm.theme_id
            WHERE th.level = 'investment'
            GROUP BY th.thesis_id
            ORDER BY
                CASE WHEN th.title LIKE 'system:%' THEN 1 ELSE 0 END,
                th.conviction DESC,
                th.title
        """).fetchall()]

        pos_theses = [dict(r) for r in conn.execute("""
            SELECT pt.thesis_id, pt.title, pt.conviction, pt.status,
                   pt.vehicle_rationale, pt.target_weight, pt.target_sleeves,
                   it.title AS parent_title
            FROM theses pt
            LEFT JOIN theses it ON pt.parent_thesis_id = it.thesis_id
            WHERE pt.level = 'position'
            ORDER BY it.title, pt.title
        """).fetchall()]

        themes = [dict(r) for r in conn.execute("""
            SELECT t.theme_id, t.name, t.description,
                SUM(CASE WHEN th.status='active'      AND th.level='investment' THEN 1 ELSE 0 END) AS active_count,
                SUM(CASE WHEN th.status='closed'      AND th.level='investment' THEN 1 ELSE 0 END) AS closed_count,
                SUM(CASE WHEN th.status='invalidated' AND th.level='investment' THEN 1 ELSE 0 END) AS inv_count
            FROM themes t
            LEFT JOIN thesis_themes tt ON t.theme_id = tt.theme_id
            LEFT JOIN theses th ON tt.thesis_id = th.thesis_id
            GROUP BY t.theme_id
            ORDER BY active_count DESC, t.name
        """).fetchall()]

        theme_theses: dict = defaultdict(list)
        for r in conn.execute("""
            SELECT tt.theme_id, th.title, th.conviction, th.status
            FROM thesis_themes tt
            JOIN theses th ON tt.thesis_id = th.thesis_id
            WHERE th.level = 'investment'
              AND th.title NOT LIKE 'system:%'
            ORDER BY th.conviction DESC, th.title
        """).fetchall():
            theme_theses[r["theme_id"]].append(dict(r))

        accounts = [dict(r) for r in conn.execute(
            "SELECT account_id, name FROM accounts WHERE is_active=1 ORDER BY name"
        ).fetchall()]

        # Holdings (ETF) first, then benchmarks, alpha within each group
        securities = [dict(r) for r in conn.execute("""
            SELECT s.ticker, s.name, s.security_type, ac.name AS sleeve, ac.asset_class_id
            FROM securities s
            JOIN asset_classes ac ON s.asset_class_id = ac.asset_class_id
            ORDER BY CASE WHEN s.security_type='ETF' THEN 0 ELSE 1 END, s.ticker
        """).fetchall()]

        # asset_class_id → position theses for that sleeve
        ac_to_pos: dict = defaultdict(list)
        for pt in pos_theses:
            try:
                for sleeve_name in json.loads(pt["target_sleeves"] or "[]"):
                    row = conn.execute(
                        "SELECT asset_class_id FROM asset_classes WHERE name=?",
                        (sleeve_name,),
                    ).fetchone()
                    if row:
                        ac_to_pos[row["asset_class_id"]].append(pt)
            except Exception:
                pass

        system_theses = [dict(r) for r in conn.execute(
            "SELECT thesis_id, title FROM theses WHERE title LIKE 'system:%' ORDER BY title"
        ).fetchall()]

        counts = dict(conn.execute("""
            SELECT
                (SELECT COUNT(*) FROM trades WHERE COALESCE(lot_source,'initial') != 'drip') AS n_disc_trades,
                (SELECT COUNT(*) FROM trades WHERE lot_source = 'drip')                      AS n_drip,
                (SELECT COUNT(*) FROM theses WHERE level='investment' AND status='active')    AS n_inv,
                (SELECT COUNT(*) FROM theses WHERE level='position'   AND status='active')    AS n_pos,
                (SELECT COUNT(*) FROM themes)                                                 AS n_themes
        """).fetchone())

    return dict(
        trades=trades,
        inv_theses=inv_theses,
        pos_theses=pos_theses,
        themes=themes,
        theme_theses=theme_theses,
        accounts=accounts,
        securities=securities,
        ac_to_pos=ac_to_pos,
        system_theses=system_theses,
        counts=counts,
    )


data = load_all()
c    = data["counts"]

# Initialise toggle state before the header renders so the banner is correct
# on the first load (before the toggle widget inside the tab fires a rerun).
if "tl_show_drip" not in st.session_state:
    st.session_state["tl_show_drip"] = False

if IS_DEMO:
    st.info(get_demo_banner_text())

# ── Header ────────────────────────────────────────────────────────────────────
_, col, _ = st.columns([1, 8, 1])
with col:
    st.title("Trade Log & Theses")
    st.caption(
        "Every trade documents a position thesis, which rolls up to "
        "an investment view, which carries theme tags."
    )
    st.caption(as_of_banner())

    _show_drip = st.session_state.get("tl_show_drip", False)
    _drip_part = (
        f"{c['n_drip']} DRIP reinvestments"
        if _show_drip
        else f"{c['n_drip']} DRIP reinvestments hidden"
    )
    st.caption(
        f"{c['n_disc_trades']} discretionary trades  ·  {_drip_part}  ·  "
        f"{c['n_inv']} active investment theses  ·  "
        f"{c['n_pos']} active position theses  ·  "
        f"{c['n_themes']} themes"
    )

    with st.expander("How to read this page", expanded=False):
        st.markdown(
            "- **Hierarchy** — every trade is linked to a Position Thesis (the vehicle-level "
            "rationale: why this ETF, not just this exposure), which links to an Investment "
            "Thesis (the sleeve-level view: why hold this asset class at this weight), which "
            "carries Theme tags (the strategic category the view belongs to). The summary line "
            "counts all three levels independently.\n"
            "- **Conviction stars (1–5)** — 1 = exploratory, 3 = standard position, "
            "4 = high-weight or cross-cycle view, 5 = highest conviction. The scale is used "
            "conservatively: a 5-star thesis requires an airtight structural case. Cash/SPAXX "
            "is the only 5-star — not because it's the best return idea, but because zero-cash "
            "is never the right answer in a taxable rebalancing portfolio.\n"
            "- **Active / Closed / Invalidated** — these are distinct outcomes. Closed = thesis "
            "completed as expected and position was unwound. Invalidated = the original "
            "analytical case was wrong. Exit conditions are expected unwind triggers; "
            "invalidation conditions are the scenarios that prove the original case analytically "
            "wrong. A position can reach its exit without being invalidated, and vice versa. "
            "Most thesis documentation systems collapse these; keeping them separate is intentional.\n"
            "- **Themes** — allocator categories, not factor exposures. A position can express "
            "multiple thematic rationales simultaneously (e.g., REITs are both Drawdown "
            "protection and Regime change)."
        )

    st.divider()


# ── Tabs ──────────────────────────────────────────────────────────────────────
tab_trades, tab_theses, tab_themes = st.tabs(["Trades", "Theses", "Themes"])


# ────────────────────────────────────────────────────────────────────────────
# Trade entry form
# Ticker and thesis selects live OUTSIDE st.form so changing them triggers a
# re-run that filters the thesis dropdown before the form is submitted.
# ────────────────────────────────────────────────────────────────────────────

def render_trade_form():
    if not is_write_enabled():
        st.info(
            "Public demo: trade entries are illustrative; "
            "submissions are not written to the store."
        )

    securities    = data["securities"]
    accounts      = data["accounts"]
    system_theses = data["system_theses"]
    ac_to_pos     = data["ac_to_pos"]

    ticker_labels  = [s["ticker"] for s in securities]
    ticker_to_sec  = {s["ticker"]: s for s in securities}

    # Always build investment thesis lookup (needed in "create new" submit path)
    inv_opts     = [t for t in data["inv_theses"] if not t["title"].startswith("system:")]
    inv_labels   = [t["title"] for t in inv_opts]
    inv_by_label = {t["title"]: t for t in inv_opts}

    # Show success banner from the previous submit
    if msg := st.session_state.pop("trade_success", None):
        st.success(msg)

    # — Dynamic selects (outside st.form) ————————————————————————————————
    dc1, dc2 = st.columns(2)
    with dc1:
        selected_ticker = st.selectbox(
            "Ticker",
            ticker_labels,
            key="tl_ticker",
            help="Holdings listed first, then benchmarks",
        )

    sec   = ticker_to_sec.get(selected_ticker, {})
    ac_id = sec.get("asset_class_id")

    sleeve_pos    = ac_to_pos.get(ac_id, [])
    thesis_opts   = [{"label": pt["title"], "thesis_id": pt["thesis_id"]} for pt in sleeve_pos]
    for sys in system_theses:
        thesis_opts.append({"label": sys["title"], "thesis_id": sys["thesis_id"]})
    thesis_opts.append({"label": "-- Create new position thesis --", "thesis_id": None})

    thesis_labels   = [t["label"] for t in thesis_opts]
    thesis_by_label = {t["label"]: t for t in thesis_opts}

    with dc2:
        selected_thesis_label = st.selectbox(
            "Position Thesis",
            thesis_labels,
            key="tl_thesis",
        )

    create_new = selected_thesis_label == "-- Create new position thesis --"

    if create_new:
        st.caption("New position thesis")
        nc1, nc2 = st.columns(2)
        with nc1:
            st.text_input(
                "Title",
                key="tl_new_title",
                placeholder=f"{sec.get('sleeve', '')} — {selected_ticker}",
            )
            st.text_input("Vehicle rationale (one line)", key="tl_new_vehicle")
        with nc2:
            st.slider("Conviction", 1, 5, 3, key="tl_new_conviction")
            st.selectbox("Parent investment thesis", inv_labels, key="tl_new_parent")

    # — Static form fields ————————————————————————————————————————————————
    with st.form("trade_entry_form"):
        r1, r2, r3 = st.columns(3)
        with r1:
            trade_date = st.date_input("Date", value=dt_date.today())
            action     = st.radio("Action", ["Buy", "Sell"], horizontal=True)
        with r2:
            account_name = st.selectbox("Account", [a["name"] for a in accounts])
            shares       = st.number_input("Shares", min_value=0.0, step=0.001, format="%.3f")
        with r3:
            price = st.number_input("Price ($)", min_value=0.0, step=0.01, format="%.2f")
            fees  = st.number_input("Fees ($)", min_value=0.0, value=0.0, step=0.01, format="%.2f")
        notes     = st.text_area("Notes (optional)", height=68)
        submitted = st.form_submit_button("Log trade", width='stretch')

        if submitted:
            ticker   = st.session_state.get("tl_ticker", "")
            th_label = st.session_state.get("tl_thesis", "")
            is_new   = th_label == "-- Create new position thesis --"

            errors = []
            if not ticker:
                errors.append("Ticker is required.")
            if shares <= 0:
                errors.append("Shares must be greater than 0.")
            if price <= 0:
                errors.append("Price must be greater than 0.")
            if trade_date > dt_date.today():
                errors.append("Date cannot be in the future.")
            if is_new and not st.session_state.get("tl_new_title", "").strip():
                errors.append("New thesis title is required.")

            if errors:
                for e in errors:
                    st.error(e)
            elif not is_write_enabled():
                write_guard_toast()
            else:
                with get_connection() as conn:
                    acct = next(
                        (a for a in accounts if a["name"] == account_name),
                        accounts[0],
                    )

                    if is_new:
                        new_title   = st.session_state.get("tl_new_title", "").strip()
                        new_vehicle = st.session_state.get("tl_new_vehicle", "").strip()
                        new_conv    = st.session_state.get("tl_new_conviction", 3)
                        new_parent  = st.session_state.get("tl_new_parent", "")
                        parent_id   = inv_by_label.get(new_parent, {}).get("thesis_id")
                        target_sl   = json.dumps([sec.get("sleeve", "")])
                        row = conn.execute(
                            """INSERT INTO theses
                               (title, macro_view, view_summary, conviction, level, status,
                                parent_thesis_id, vehicle_rationale, target_sleeves)
                               VALUES (?, ?, ?, ?, 'position', 'active', ?, ?, ?)""",
                            (new_title, new_vehicle, new_vehicle,
                             new_conv, parent_id, new_vehicle, target_sl),
                        )
                        thesis_id = row.lastrowid
                    else:
                        thesis_id = (thesis_by_label.get(th_label) or {}).get("thesis_id")

                    conn.execute(
                        """INSERT INTO trades
                           (account_id, ticker, thesis_id, trade_date, action,
                            shares, price, fees, notes)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            acct["account_id"], ticker, thesis_id,
                            trade_date.isoformat(), action,
                            shares, price, fees,
                            notes.strip() or None,
                        ),
                    )

                st.session_state["trade_success"] = (
                    f"Logged: {action} {shares:.3f} {ticker} @ \\${price:.2f}"
                )
                st.rerun()


# ────────────────────────────────────────────────────────────────────────────
# Fidelity transaction-CSV import
# Parses a Fidelity account-history export, logs each fill as its own tax lot,
# classifies/excludes non-trade rows, pre-filters unknown tickers (FK-safe), and
# dedups via a composite-key multiset so re-importing a cumulative export does
# not double-log. Writes through the existing guarded write_trades_batch.
# ────────────────────────────────────────────────────────────────────────────

def render_fidelity_import():
    from datetime import date as _imp_date

    from src.ingestion.fidelity import parse_fidelity_transactions
    from src.ingestion.trade_import import dedupe_against_existing, split_known_tickers
    from src.trade_writer import build_thesis_lookup, write_trades_batch

    if not is_write_enabled():
        st.info(
            "Public demo: CSV import is illustrative; nothing is written to the store."
        )

    up = st.file_uploader(
        "Fidelity account-history CSV",
        type="csv",
        key="fidelity_txn_csv",
        help="Export your Fidelity account Activity (Accounts & Trade → History → "
             "Download) as CSV and upload it here.",
    )
    if up is None:
        st.caption(
            "Each fill is logged as a separate tax lot. Dividends and reinvestments are "
            "classified and skipped; unknown tickers (not in the securities table) are "
            "skipped; re-uploading a cumulative export will not double-log."
        )
        return

    try:
        parsed = parse_fidelity_transactions(up)
    except Exception as exc:
        st.error(f"Could not parse the CSV: {exc}")
        return

    # Fidelity's account-history export does NOT name the account (it is a
    # single-account export — the columns are Run Date / Action / Symbol / … with
    # no Account field), so the account cannot be read per row. It must be chosen
    # explicitly here: every lot in this file is filed under the selected account,
    # and there is no silent default — importing to the wrong account cannot be
    # undone (a lot carries no other account marker afterward).
    _ACCT_PLACEHOLDER = "— Select the account this file was exported from —"
    _acct_choice = st.selectbox(
        "Account this history belongs to",
        [_ACCT_PLACEHOLDER] + [a["name"] for a in data["accounts"]],
        key="fidelity_import_account",
        help="Fidelity's account-history CSV does not include the account, so name "
             "it here. Every lot in this file is logged to this account. Export one "
             "account's history at a time.",
    )
    _sel_acct = next((a for a in data["accounts"] if a["name"] == _acct_choice), None)
    if _sel_acct is None:
        st.info(
            "Choose the account this file was exported from to continue — importing "
            "to the wrong account cannot be undone."
        )
        return
    account_id = int(_sel_acct["account_id"])

    # Stamp every parsed lot with the chosen account BEFORE dedup, so the dedup key
    # is account-scoped: an identical lot in a different account (a cross-account
    # rebalance) is not mistaken for a duplicate of one already logged elsewhere.
    for _lot in parsed["trades"]:
        _lot["account_id"] = account_id

    known_set = {s["ticker"] for s in data["securities"]}
    known_lots, unknown_lots = split_known_tickers(parsed["trades"], known_set)
    to_import, already = dedupe_against_existing(known_lots, data["trades"])

    sk      = parsed["skipped"]
    n_div   = sk.get("DIVIDEND RECEIVED", 0)
    n_reinv = sk.get("REINVESTMENT", 0)
    n_other = sum(v for k, v in sk.items() if k not in ("DIVIDEND RECEIVED", "REINVESTMENT"))
    unk_syms = sorted({l["ticker"] for l in unknown_lots})

    summary = (
        f"**{len(parsed['trades'])}** trade lots parsed · "
        f"**{len(already)}** already logged (skipped) · "
        f"non-trade rows skipped: {n_div} dividends, {n_reinv} reinvestments"
    )
    if n_other:
        summary += f", {n_other} other"
    if unknown_lots:
        summary += f" · **{len(unknown_lots)}** unknown-ticker rows skipped ({', '.join(unk_syms)})"
    if parsed["errors"]:
        summary += f" · {len(parsed['errors'])} parse errors"
    st.caption(summary)

    if not to_import:
        st.success("Nothing new to import — all trade lots in this file are already logged.")
        return

    preview = pd.DataFrame([{
        "Date":   l["trade_date"],
        "Ticker": l["ticker"],
        "Action": l["action"],
        "Shares": l["shares"],
        "Price":  l["price"],
        "Total":  l["total_value"],
    } for l in to_import])
    st.dataframe(
        preview,
        width="stretch",
        hide_index=True,
        column_config={
            "Price":  st.column_config.NumberColumn(format="$%.2f"),
            "Total":  st.column_config.NumberColumn(format="$%.2f"),
            "Shares": st.column_config.NumberColumn(format="%.4f"),
        },
    )

    if st.button(
        f"Confirm import — {len(to_import)} lot{'s' if len(to_import) != 1 else ''} "
        f"→ {_sel_acct['name']}",
        type="primary",
        disabled=not is_write_enabled(),
        key="fidelity_confirm",
    ):
        if not is_write_enabled():
            write_guard_toast()
            return
        thesis_lookup = build_thesis_lookup(data["pos_theses"])
        today = _imp_date.today().isoformat()
        trades_list = [{
            "ticker":      l["ticker"],
            "trade_date":  l["trade_date"],
            "shares":      l["shares"],
            "price":       l["price"],
            "total_value": l["total_value"],
            "action":      l["action"],
            "thesis_id":   thesis_lookup.get(l["ticker"]),
            "lot_source":  "Fidelity CSV",
            "notes":       f"Imported from Fidelity CSV {today} | fp: {l['fingerprint']}",
        } for l in to_import]
        expected = round(sum(l["total_value"] for l in to_import), 2)
        if write_trades_batch(trades_list, expected, account_id):
            st.session_state["trade_success"] = (
                f"Imported {len(trades_list)} trade lots from Fidelity CSV "
                f"into {_sel_acct['name']}."
            )
            st.rerun()
        else:
            st.error("Import failed — no trades were written (check the write guard).")


# ────────────────────────────────────────────────────────────────────────────
# Tab 1: Trades
# ────────────────────────────────────────────────────────────────────────────

_DRIP_TOGGLE_HELP = (
    "DRIP reinvestments are mechanical reinvestments of distributions, not "
    "discretionary trades. They inherit the position thesis of the parent holding."
)

with tab_trades:
    _, col, _ = st.columns([1, 8, 1])
    with col:
        show_drip = st.toggle(
            "Show DRIP reinvestments",
            value=False,
            key="tl_show_drip",
            help=_DRIP_TOGGLE_HELP,
        )

        trades_all = data["trades"]
        trades = (
            trades_all
            if show_drip
            else [t for t in trades_all if t.get("lot_source") != "drip"]
        )

        # Build ticker → (pos_thesis_title, inv_thesis_title) from active
        # position theses so DRIP rows can inherit the same thesis display
        # as their discretionary counterpart for the same ticker.
        _ticker_thesis: dict[str, tuple[str, str]] = {}
        for _pt in data["pos_theses"]:
            if _pt.get("status") != "active":
                continue
            _parts = (_pt.get("title") or "").rsplit(" — ", 1)
            if len(_parts) == 2:
                _sym = _parts[-1].strip()
                _ticker_thesis[_sym] = (
                    _pt["title"],
                    _pt.get("parent_title") or "—",
                )

        if not trades:
            if is_write_enabled():
                st.info(
                    "No trades logged yet. The portfolio is currently 100% cash. "
                    "When you make your first trade, log it here with the thesis it expresses."
                )
                st.markdown("#### Log a trade")
                render_trade_form()
            else:
                st.caption("No discretionary trades have been logged yet.")
        else:
            rows = []
            for t in trades:
                pos_t = t["position_thesis"]
                inv_t = t["investment_thesis"]
                if t.get("lot_source") == "drip" and not pos_t:
                    _inherited = _ticker_thesis.get(t["ticker"])
                    if _inherited:
                        pos_t, inv_t = _inherited
                rows.append({
                    "Date":              t["trade_date"],
                    "Action":            t["action"].title(),
                    "Ticker":            t["ticker"],
                    "Shares":            t["shares"],
                    "Price":             t["price"],
                    "Total Value":       round(t["shares"] * t["price"], 2),
                    "Position Thesis":   pos_t or "—",
                    "Investment Thesis": inv_t or "—",
                })
            df = pd.DataFrame(rows)
            st.dataframe(
                df,
                width='stretch',
                hide_index=True,
                column_config={
                    "Price":       st.column_config.NumberColumn(format="$%.2f"),
                    "Total Value": st.column_config.NumberColumn(format="$%.2f"),
                    "Shares":      st.column_config.NumberColumn(format="%.3f"),
                },
            )
            if is_write_enabled():
                with st.expander("Log a new trade"):
                    render_trade_form()

        with st.expander("Import from Fidelity CSV"):
            render_fidelity_import()


# ────────────────────────────────────────────────────────────────────────────
# Tab 2: Theses
# ────────────────────────────────────────────────────────────────────────────

with tab_theses:
    _, col, _ = st.columns([1, 8, 1])
    with col:
        # ── Investment theses ──────────────────────────────────────────────
        st.subheader("Investment Theses")
        st.caption("★ rating reflects conviction in the thesis (1 = exploratory, 5 = high conviction).")
        show_system = st.checkbox(
            "Show operational theses", value=False, key="theses_show_system"
        )

        inv_theses  = data["inv_theses"]
        active_inv  = [
            t for t in inv_theses
            if t["status"] == "active"
            and (show_system or not t["title"].startswith("system:"))
        ]
        closed_inv  = [t for t in inv_theses if t["status"] == "closed"]
        invalid_inv = [t for t in inv_theses if t["status"] == "invalidated"]

        pos_by_parent: dict = defaultdict(list)
        for pt in data["pos_theses"]:
            key = pt.get("parent_title") or ""
            pos_by_parent[key].append(pt)

        for thesis in active_inv:
            label = f"{thesis['title']}  {_stars(thesis['conviction'])}"
            with st.expander(label):
                hc1, hc2 = st.columns([4, 1])
                with hc1:
                    pills = _pills(thesis.get("theme_names") or "")
                    if pills:
                        st.markdown(pills, unsafe_allow_html=True)
                with hc2:
                    st.markdown(_badge(thesis["status"]), unsafe_allow_html=True)

                _safe_md(thesis.get("view_summary") or "")

                if thesis.get("created_at"):
                    try:
                        _open_date = dt_date.fromisoformat(str(thesis["created_at"])[:10])
                        _days_held = (dt_date.today() - _open_date).days
                        _safe_cap(f"**Days held:** {_days_held}")
                    except Exception:
                        pass
                if thesis.get("horizon_months"):
                    _safe_cap(f"**Horizon:** {thesis['horizon_months']} months")
                if thesis.get("exit_conditions"):
                    _safe_cap(f"**Exit conditions:** {thesis['exit_conditions']}")
                if thesis.get("invalidation_conditions"):
                    _safe_cap(f"**Invalidation conditions:** {thesis['invalidation_conditions']}")
                if thesis.get("expected_return_scenario"):
                    _safe_cap(f"**Expected return:** {thesis['expected_return_scenario']}")
                if thesis.get("target_sleeves"):
                    try:
                        sl = json.loads(thesis["target_sleeves"])
                        st.caption(f"**Target sleeves:** {', '.join(sl)}")
                    except Exception:
                        pass

                linked = pos_by_parent.get(thesis["title"], [])
                if linked:
                    tickers = "  ·  ".join(
                        pt["title"].split(" — ")[-1] for pt in linked
                    )
                    st.caption(f"**Positions:** {tickers}")

        if closed_inv:
            with st.expander(f"Closed theses ({len(closed_inv)})"):
                for t in closed_inv:
                    st.markdown(f"**{t['title']}**  {_stars(t['conviction'])}")
                    summary = t.get("view_summary") or ""
                    if summary:
                        truncated = (summary[:120] + "…") if len(summary) > 120 else summary
                        st.caption(truncated.replace("$", r"\$"))

        if invalid_inv:
            with st.expander(f"Invalidated theses ({len(invalid_inv)})"):
                for t in invalid_inv:
                    st.markdown(f"**{t['title']}**  {_stars(t['conviction'])}")
                    summary = t.get("view_summary") or ""
                    if summary:
                        truncated = (summary[:120] + "…") if len(summary) > 120 else summary
                        st.caption(truncated.replace("$", r"\$"))

        st.divider()

        # ── Position theses ────────────────────────────────────────────────
        st.subheader("Position Theses")

        if data["pos_theses"]:
            pos_rows = []
            for pt in data["pos_theses"]:
                parts       = pt["title"].split(" — ")
                ticker_part = parts[-1] if parts else pt["title"]
                sleeve_part = " — ".join(parts[:-1]) if len(parts) > 1 else ""
                rat         = pt.get("vehicle_rationale") or ""
                pos_rows.append({
                    "Sleeve":            sleeve_part,
                    "Ticker":            ticker_part,
                    "Vehicle Rationale": (rat[:120] + "…") if len(rat) > 120 else rat,
                    "Conviction":        _stars(pt["conviction"]),
                    "Investment Thesis": pt.get("parent_title") or "—",
                })
            st.dataframe(
                pd.DataFrame(pos_rows),
                width='stretch',
                hide_index=True,
            )

        with st.expander("Methodology", expanded=False):
            st.markdown(
                "**Thesis taxonomy** — three levels stored separately. Position theses "
                "document why a specific vehicle (e.g., VGIT) is the right implementation "
                "of an exposure. Investment theses document why a sleeve (e.g., Core Fixed "
                "Income) is held at a given weight. Themes group investment theses by "
                "strategic function.\n\n"
                "**Days held** — computed from the position creation date. All current "
                "theses dated 2025-05-01 inception.\n\n"
                "**Horizon** — stated holding horizon in months, set at thesis creation. "
                "Standard portfolio positions use 60 months reflecting the strategic "
                "allocation horizon; tactical positions would use shorter horizons.\n\n"
                "**Theme assignments** — a single thesis can carry multiple theme tags "
                "when a position serves multiple strategic functions. The distribution of "
                "tags across the portfolio (active counts per theme on the Themes tab) is "
                "a diagnostic of how the SAA earns its risk budget."
            )


# ────────────────────────────────────────────────────────────────────────────
# Tab 3: Themes
# ────────────────────────────────────────────────────────────────────────────

with tab_themes:
    _, col, _ = st.columns([1, 8, 1])
    with col:
        themes       = data["themes"]
        theme_theses = data["theme_theses"]

        if not themes:
            st.info("No themes defined yet.")
        else:
            st.markdown(
                "Themes are allocator categories, not return drivers. Each theme represents "
                "a class of strategy rationale that a portfolio might want explicit exposure "
                "to or explicit absence of — the same framework institutional investors use "
                "to classify manager mandates. A single position can express multiple "
                "thematic rationales simultaneously: REITs provide both inflation-correlated "
                "returns (Regime change) and equity-like diversification drag in a portfolio "
                "with a dominant 78% equity allocation (Drawdown protection)."
            )
            for theme in themes:
                _t_active = theme.get("active_count") or 0
                _t_badge  = (
                    f'<span style="background:#2d6a4f20;color:#2d6a4f;'
                    f'padding:2px 9px;border-radius:12px;font-size:0.8rem;'
                    f'font-weight:500">{_t_active} active</span>'
                )
                st.markdown(
                    f"### {theme['name']} &nbsp; {_t_badge}",
                    unsafe_allow_html=True,
                )
                if theme.get("description"):
                    st.caption(theme["description"])

                linked = theme_theses.get(theme["theme_id"], [])
                if linked:
                    for th in linked:
                        st.markdown(
                            f"· {th['title']}&ensp;"
                            f"{_stars(th['conviction'])}&ensp;"
                            f"{_badge(th['status'])}",
                            unsafe_allow_html=True,
                        )
                else:
                    st.caption("No linked theses.")

                active_n = theme.get("active_count") or 0
                closed_n = theme.get("closed_count") or 0
                inv_n    = theme.get("inv_count") or 0
                st.caption(
                    f"{active_n} active theses  ·  "
                    f"{closed_n} closed  ·  "
                    f"{inv_n} invalidated"
                )
                st.divider()
render_footer()
