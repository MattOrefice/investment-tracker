# LinkedIn Project Entry Drafts

Two versions for review. Both mention CFA credential, specific analytical concepts,
and include demo and GitHub links. Choose one or blend elements.

---

## Version A — Tagline + Bullets (~100 words)

**Investment Analytics Tracker | Personal Portfolio System**

Built a full-stack portfolio analytics platform to apply CFA-level investment analysis
to a live personal portfolio:

- GIPS-compliant daily-linked TWR with Brinson-Fachler attribution — allocation and
  selection effects reconcile to active return within 1 basis point
- Ten-sleeve SAA with two-threshold drift monitoring; custom-blended benchmark
  constructed from SAA target weights against per-sleeve proxies
- Macro overlay: CAPE (Shiller/Yale), yield curve, HY credit spreads via FRED API
- Automated quarterly PDF reporting (WeasyPrint, kaleido static chart rendering)
- Dual-mode architecture: local personal portfolio and public demo on Streamlit Cloud

CFA charterholder (April 2026) | Previously: portfolio analytics at MissionSquare Retirement

Live demo: https://mattorefice-investment.streamlit.app/
GitHub: https://github.com/MattOrefice/investment-tracker

---

## Version B — Short Paragraph Format (~120 words)

**Investment Analytics Tracker**

Built an end-to-end portfolio analytics system designed to mirror institutional
allocator workflows. The system implements GIPS-compliant time-weighted returns,
Brinson-Fachler attribution decomposing active return into allocation and selection
effects, and a custom-blended benchmark constructed from SAA target weights against
per-sleeve benchmark proxies — the same framing a formal attribution report would use.

Every trade is linked to a documented investment thesis with a macro view, conviction
rating, and exit conditions. Performance attribution traces back through that thesis
structure. Macro context integrates CAPE, yield curve slope, and credit spreads via
FRED, with historical percentiles for each indicator.

The primary public artifact is an automated quarterly PDF report, generated directly
from the live demo. CFA charterholder (April 2026); previously in portfolio analytics
at MissionSquare Retirement.

Live demo: https://mattorefice-investment.streamlit.app/
GitHub: https://github.com/MattOrefice/investment-tracker

---

## Notes on usage

- Version A is more scannable for a quick profile view; better if the audience is
  likely to skim rather than read.
- Version B reads more naturally as prose; better if the audience will read the full
  entry (more common for direct outreach or cover letter context).
- Either version can be shortened by removing one bullet (A) or one sentence (B)
  if the platform's character limit is tight.
- The GitHub URL should link to the public repo once it is flipped to public.
