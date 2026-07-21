"""DB-derived sleeve → benchmark maps and the sleeve-taxonomy helpers.

Replaces the hardcoded per-sleeve benchmark maps that used to live in
benchmarks.py, asset_evaluation.py, reports.py and factors.py. Those maps froze
a 12-sleeve taxonomy into module constants, so an armed guard comparing map to DB
was conformance-checking "are there exactly these 12 sleeves" rather than
coherence-checking "does every sleeve in THIS book have a benchmark". Deriving
from asset_classes (the seeded source of truth) means the map describes whatever
book it is pointed at — 9 sleeves for the personal tracker, 12 for the demo —
and the coherence check becomes: every strategic sleeve has a parseable
benchmark. A sleeve with no benchmark fails loud here rather than silently
resolving to weight 0.0 downstream (the 120bps silent-drift bug).

Mode/DB is read at CALL TIME via src.db.get_connection(), so the same code
yields the 9-sleeve map in personal mode and the 12-sleeve map in demo mode.
"""
import re

from src.db import get_connection

_CASH_SLEEVE = "Cash / SPAXX"

# A benchmark spec component is either a bare ticker ("EFA") or a weighted
# blend leg ("VNQ (60%)"). Tickers are uppercase alphanumerics plus dot.
_TICKER = r"[A-Z][A-Z0-9.]*"
_BLEND_LEG = re.compile(rf"^\s*({_TICKER})\s*\((\d+(?:\.\d+)?)%\)\s*$")
_BARE = re.compile(rf"^\s*({_TICKER})\s*$")


def parse_benchmark_spec(spec: str) -> list[tuple[str, float]]:
    """Parse an asset_classes.benchmark_ticker value into [(ticker, weight), …].

    Accepted forms:
        "EFA"                     -> [("EFA", 1.0)]
        "VNQ (60%) + DBC (40%)"   -> [("VNQ", 0.6), ("DBC", 0.4)]

    FAILS LOUD (ValueError) on an empty/None spec, an unparseable component, or
    weights that do not sum to 1.0. A benchmark that silently vanishes — a stale
    key resolving to weight 0.0, its leg dropped, the survivors renormalised —
    is precisely the silent-drift defect this layer exists to prevent, so every
    failure mode raises rather than returning an empty or partial list.
    """
    if spec is None or not str(spec).strip():
        raise ValueError(f"empty/None benchmark spec: {spec!r}")

    legs: list[tuple[str, float]] = []
    for raw in str(spec).split("+"):
        part = raw.strip()
        m = _BLEND_LEG.match(part)
        if m:
            legs.append((m.group(1), float(m.group(2)) / 100.0))
            continue
        m = _BARE.match(part)
        if m:
            legs.append((m.group(1), 1.0))
            continue
        raise ValueError(
            f"unparseable benchmark component {part!r} in spec {spec!r} "
            f"(expected 'TICKER' or 'TICKER (NN%)')"
        )

    total = sum(w for _, w in legs)
    if abs(total - 1.0) > 1e-9:
        raise ValueError(
            f"benchmark weights sum to {total} (not 1.0) in spec {spec!r}: {legs}. "
            f"A partial blend would silently rescale the surviving legs to $1 — refuse it."
        )
    return legs


def _strategic_rows(include_cash: bool):
    """asset_classes sub-class rows (parent_id NOT NULL), source of truth for the maps.

    include_cash keeps the Cash / SPAXX row (target 0.0, benchmark BIL) — the
    benchmark subsystem wants it (BIL is a real reference leg), the mean-variance
    subsystem excludes it (near-zero variance distorts the optimisation).
    """
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT name, target_weight, benchmark_ticker, sort_order "
            "FROM asset_classes WHERE parent_id IS NOT NULL "
            "ORDER BY sort_order"
        ).fetchall()
    if include_cash:
        return rows
    return [r for r in rows if r["name"] != _CASH_SLEEVE]


def sleeve_benchmarks(include_cash: bool = True) -> dict[str, list[tuple[str, float]]]:
    """{sleeve name -> [(benchmark ticker, weight), …]} for the current book.

    Raises (via parse_benchmark_spec) if any sleeve carries no valid benchmark —
    the coherence check that used to be _assert_benchmark_map_matches_db.
    """
    return {
        r["name"]: parse_benchmark_spec(r["benchmark_ticker"])
        for r in _strategic_rows(include_cash)
    }


def strategic_sleeve_weights(include_cash: bool = False) -> dict[str, float]:
    """{sleeve name -> target_weight} for sleeves carrying a strategic target (>0)."""
    return {
        r["name"]: float(r["target_weight"])
        for r in _strategic_rows(include_cash)
        if float(r["target_weight"]) > 0
    }


def sleeve_holdings() -> dict[str, list[str]]:
    """{sleeve -> [held ticker, …]} — the securities held for each sleeve.

    A sleeve's holdings are the securities pointing at it whose ticker is NOT one
    of that sleeve's own benchmark legs. This is unambiguous for every tilt sleeve
    (International Quality -> IDHQ, benchmark IQLT), which is what the SAA thesis
    prose enumerates. The one impure case is Real Assets, where VNQ is both a held
    REIT and a leg of the "VNQ (60%) + DBC (40%)" benchmark blend, so it resolves
    to the commodity holding alone (PDBC) — callers displaying the full Real
    Assets holding should not rely on this for that sleeve.
    """
    out: dict[str, list[str]] = {}
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT ac.name AS sleeve, s.ticker AS ticker
                 FROM securities s
                 JOIN asset_classes ac ON s.asset_class_id = ac.asset_class_id
                WHERE ac.parent_id IS NOT NULL
                ORDER BY ac.sort_order, s.rowid"""
        ).fetchall()
    bench = sleeve_benchmarks(include_cash=True)
    for r in rows:
        sleeve, ticker = r["sleeve"], r["ticker"]
        bench_legs = {t for t, _ in bench.get(sleeve, [])}
        if ticker not in bench_legs:
            out.setdefault(sleeve, []).append(ticker)
    return out


def international_sleeves() -> tuple[str, ...]:
    """The developed-international sleeve names present in the current book.

    Personal (9-sleeve): ('International Developed',).
    Demo (12-sleeve):    the four Core/Quality/Large Value/Small Value sleeves.
    Ordered by sort_order so callers that render them get a stable sequence.
    """
    return tuple(
        r["name"]
        for r in _strategic_rows(include_cash=False)
        if r["name"].startswith("International")
    )
