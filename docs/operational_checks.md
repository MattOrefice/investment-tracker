# Operational Checks

Periodic maintenance tasks to keep the portfolio tracker running correctly.

---

## Daily / on-demand

### Refresh prices cache

Run when the bound test soft-warns about cache staleness, or before generating a quarterly report:

```bash
python scripts/rebuild_prices_cache.py
```

Or for a specific date window:

```bash
python scripts/rebuild_prices_cache.py --since 2025-01-01
```

SPAXX and DJP are excluded automatically (SPAXX = $1.00 constant; DJP = delisted 2020).

### Run fast tests

```bash
python -m pytest                        # fast tests only (slow excluded by default)
python -m pytest -m slow                # only the slow/network-bound tests
python -m pytest -m "not slow"          # explicit fast filter (same as default)
python -m pytest -m bound               # only Layer 2 reasonability bounds
```

---

## Quarterly

### Update duration caption in pages/6_Positioning.py

The fixed-income sleeve duration caption cites ETF fact-sheet values (VGIT and SCHP duration).
These change as the ETFs' underlying bonds roll. Check:
- VGIT: https://investor.vanguard.com/investment-products/etfs/profile/vgit (Characteristics tab)
- SCHP: https://www.schwabassetmanagement.com/products/schp (Fund Facts)

Update the caption at `pages/6_Positioning.py` line ~82.

### Verify ETF expense ratios

ERs are static in `pages/2_Research.py`. Review once per year at renewal:
- Any ETF issuer ER reductions since last review
- PDBC (0.59%) — most likely to change; watch for Invesco repricing

---

## Semi-annual / as-needed

### Review CAPE data freshness

The Shiller CAPE dataset at Yale is updated periodically but lags by 1–2 months. If the
Macro page shows data-as-of more than 3 months ago, force-refresh via the "Force Refresh"
button on the Macro page.

### Review `_SAA_US` and `_FI_WEIGHTS` constants

If SAA weights are ever revised, two constants in `src/factors.py` must be updated:
- `_SAA_US` (line ~101): US equity sleeve weights in percent
- `_FI_WEIGHTS` (line ~92): VGIT/SCHP proportional weights

The tests `test_prose_saa_us_constants_match_db` and `test_prose_fi_weights_constant_matches_db`
will fail immediately if these drift from the DB, catching the mismatch on the next `pytest` run.

### Update `INCEPTION` if restarting the portfolio

`pages/4_Performance.py` line 25 has `INCEPTION = "2025-05-01"`. This is the date the first
paper trades were entered. If the portfolio is ever fully reset (e.g., transitioning from demo
to real mode with new first trade date), update this constant and rerun attribution tests.

---

## One-time setup

### Configure real portfolio database

1. Copy `data/demo.db` to `data/tracker.db` (or let `src/db.py` auto-create it)
2. Seed the SAA: `python src/seed_saa.py`
3. Seed securities: `python src/seed_securities.py`
4. Enter real trades via the Trade Log page or directly via sqlite3

### Streamlit Cloud deployment

1. Push `data/demo.db` to main (it is committed; `tracker.db` is gitignored)
2. Set `TRACKER_MODE=demo` in Streamlit Cloud secrets
3. Set `FRED_API_KEY` in Streamlit Cloud secrets
4. Set `password` in `.streamlit/secrets.toml` on the deployment host

See `.streamlit/secrets.toml.example` for the template.
