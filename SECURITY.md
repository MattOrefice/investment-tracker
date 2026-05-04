# Secret Handling

## API keys in use

| Key | Purpose | How to obtain |
|-----|---------|---------------|
| `FRED_API_KEY` | FRED macroeconomic data (yield curve, Fed Funds, HY spreads) | Free at fredaccount.stlouisfed.org/apikeys |

## Where keys live

- **Local development:** `.env` file at repo root — gitignored, never committed. Copy `.env.example` to `.env` and fill in your key.
- **Streamlit Cloud:** `.streamlit/secrets.toml` on the deployment host — not in this repository. Configure under app settings → Secrets.

## Committed secrets audit (as of Phase 7)

No API keys or secrets are committed to this repository:

- `data/demo.db` — SQLite containing paper-trade data only, no credentials
- `src/`, `pages/`, `templates/` — no hardcoded API keys or passwords
- `.env` — gitignored
- `.streamlit/secrets.toml` — gitignored

If you discover a committed secret, rotate the key immediately before taking any other action.

## FRED API key rotation

1. Revoke the current key at fredaccount.stlouisfed.org/apikeys
2. Generate a new key on the same page
3. Update your local `.env`: `FRED_API_KEY=<new-key>`
4. Update the Streamlit Cloud secret: app settings → Secrets → update `FRED_API_KEY`
5. Streamlit Cloud redeploys automatically (typically within 1–2 minutes)
6. Verify: open the Macro page and confirm all four indicators populate (CAPE, yield curve, Fed Funds, HY spread)

## General guidance

- Do not commit `.env` or `.streamlit/secrets.toml` under any circumstances
- The `python-dotenv` library loads `.env` automatically in local dev; on Streamlit Cloud, `st.secrets` is used instead (see `src/config.py`)
- If a pull request accidentally includes a secret, close it immediately, rotate the key, and use `git filter-repo` or BFG Repo Cleaner to scrub the history before force-pushing
