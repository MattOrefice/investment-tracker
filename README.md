# Investment Analytics Tracker

A Python-based personal investment analytics system mirroring an institutional allocator workflow: strategic asset allocation, candidate research, thesis-driven execution, performance attribution, and quarterly reporting.

## Workflow stages
1. **SAA** — target weights with rationale and tolerance bands
2. **Research** — candidate ETF/security comparison per asset class
3. **Trade log** — every trade documents a thesis, conviction, and exit conditions
4. **Performance** — time-weighted returns, S&P 500 benchmarking, Brinson-Hood-Beebower attribution
5. **Macro** — CAPE, 2/10 spread, US vs international relative performance

## Stack
Python 3.11+, Streamlit, SQLite, pandas, yfinance, fredapi, plotly, pytest

## Run
```
.\.venv\Scripts\Activate.ps1
streamlit run app.py
```
