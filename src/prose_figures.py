"""Figures in seeded prose, filled from the data when the prose renders (2026-09-25
audit, #406 items 5 and 7).

Seeded prose is written into the book once, so a figure typed into it stays whatever it
was the day it was typed. The sleeve rationales quoted weights from an earlier target set
(US Large Core's "16%" against a 17.3% target), and SPAXX's rationale gave cash a "3%
weight" when cash has no target at all. They now carry tokens that render from the SAA
targets and the book:

  {{w:Sleeve}}           the sleeve's SAA target weight
  {{sum:A+B}}            the sum of those sleeves' targets
  {{share:A+B|C+D}}      the first sum as a share of the second
  {{scaled:A|B|C+D}}     A's target times B's share of the C+D group: the size a sleeve
                         would have if a structure were mirrored into A's region
  {{cash}}               the operational cash share of the portfolio, with its close
  {{er:TICKER}}          the fund's expense ratio, as the securities table holds it
  {{dur:TICKER}}         the fund's effective duration, as src.positioning.ETF_DURATION
                         holds it for the duration metric

Weights and shares print to one decimal, as the SAA page does; an expense ratio prints to
two, as the Research page does. A name that is not a sleeve of the book, or a ticker with
no expense ratio on file, raises: prose naming what the book lacks is an error to see, not
a figure to guess. Text without tokens (the personal book's prose) renders unchanged.
"""
from __future__ import annotations

import re
from datetime import date

TOKEN = re.compile(r"\{\{(\w+)(?::([^}]*))?\}\}")


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _targets() -> "dict[str, float]":
    """Every asset class's target weight by name. A sleeve and its parent may share a
    name (Real Assets); the sleeve's row wins, and on the books the two agree."""
    from src.db import get_connection
    with get_connection() as conn:
        rows = conn.execute("SELECT name, target_weight, parent_id IS NULL FROM asset_classes"
                            ).fetchall()
    out: "dict[str, float]" = {}
    for name, weight, is_parent in sorted(rows, key=lambda r: -r[2]):  # parents first
        out[name] = float(weight or 0.0)
    return out


def _total(targets: "dict[str, float]", names: str) -> float:
    total = 0.0
    for name in (n.strip() for n in names.split("+")):
        if name not in targets:
            raise KeyError(f"prose names '{name}', which is not an asset class of this book")
        total += targets[name]
    return total


def _expense_ratio(ticker: str) -> str:
    """A fund's expense ratio from the securities table, printed as Research prints it
    (theses read the same number the fee exhibit shows, #421)."""
    from src.db import get_connection
    with get_connection() as conn:
        row = conn.execute("SELECT expense_ratio FROM securities WHERE ticker = ?",
                           (ticker.strip(),)).fetchone()
    if row is None or row[0] is None:
        raise KeyError(f"prose names the expense ratio of '{ticker}', which this book "
                       "does not hold")
    return f"{float(row[0]) * 100:.2f}%"


def _duration(ticker: str) -> str:
    """A fund's effective duration from the table the FI duration metric reads, so a
    thesis states the figure the Positioning section computes with."""
    from src.positioning import ETF_DURATION
    years = ETF_DURATION.get(ticker.strip())
    if years is None:
        raise KeyError(f"prose names the duration of '{ticker}', which ETF_DURATION does "
                       "not hold")
    return f"{years:.1f} years"


def cash_phrase() -> str:
    """The operational cash share of the portfolio, as the SAA page's own caption
    measures it (sleeve_weights_with_coverage's cash_weight_of_total), with the close it
    is measured at."""
    from src.holdings import committed_price_frontier, sleeve_weights_with_coverage
    frame, _coverage = sleeve_weights_with_coverage(date.today().isoformat())
    share = frame.attrs.get("cash_weight_of_total") if not frame.empty else None
    if share is None:
        return "a share this page could not measure"
    close = date.fromisoformat(committed_price_frontier())
    return f"{_pct(share)} of the portfolio at the {close:%B} {close.day}, {close.year} close"


def render(text: "str | None", *, targets: "dict[str, float] | None" = None) -> "str | None":
    """``text`` with every token filled. Reads the targets (and, for {{cash}}, the book)
    only when the text has a token."""
    if not text or "{{" not in text:
        return text
    targets = targets if targets is not None else _targets()

    def fill(m: "re.Match") -> str:
        kind, arg = m.group(1), m.group(2) or ""
        if kind == "w":
            return _pct(_total(targets, arg))
        if kind == "sum":
            return _pct(_total(targets, arg))
        if kind == "share":
            part, whole = arg.split("|")
            return _pct(_total(targets, part) / _total(targets, whole))
        if kind == "scaled":
            region, part, whole = arg.split("|")
            return _pct(_total(targets, region) * _total(targets, part)
                        / _total(targets, whole))
        if kind == "cash":
            return cash_phrase()
        if kind == "er":
            return _expense_ratio(arg)
        if kind == "dur":
            return _duration(arg)
        raise KeyError(f"unknown prose token {m.group(0)}")

    return TOKEN.sub(fill, text)
