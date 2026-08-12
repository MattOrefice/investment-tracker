"""Refresh the committed market-data inputs — the ONLY writer of these files.

Fetches and rewrites the five tracked market-data files:

    data/cache/ff_factors_us.csv             Ken French daily FF5, US
    data/cache/ff_factors_developed_exus.csv Ken French daily FF5, Developed ex-US
    data/cache/ff_umd_us.csv                 Ken French daily momentum (UMD)
    data/shiller_cape.csv                    Shiller CAPE (multpl, Yale fallback)
    data/trailing_pe.csv                     S&P 500 trailing P/E (multpl)

The runtime loaders never fetch or write (see data/cache/README.md): a refresh
is a deliberate step whose diff is reviewed and COMMITTED, giving the repo a
legible refresh history instead of silent drift. Cadence: monthly-ish — the
staleness surfaces (asof.MARKET_DATA_STALE_DAYS_*) fire when a cycle is missed.

Fetch and write are SEPARATE failure states, deliberately: the old in-loader
refresh put ``to_csv`` in the same except as the download, so a blocked write
reported "refresh failed … using cached data" and silently discarded a
successfully fetched frame. Here a write failure reports exactly what was
fetched and lost, and exits nonzero.

Usage:
    python tools/refresh_market_data.py                # all five
    python tools/refresh_market_data.py --files ff_us cape
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import factors, shiller, trailing_pe  # noqa: E402


def _write_csv_factory(path: Path, **to_csv_kwargs):
    def _write(df) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(path, **to_csv_kwargs)
    return _write


# key -> (target path, fetch() -> frame/series, write(frame), frontier() -> date|None)
_TARGETS: dict[str, tuple] = {
    "ff_us": (
        factors._FACTOR_CONFIG["us"]["cache"],
        lambda: factors._fetch_factors(factors._FACTOR_CONFIG["us"]["url"]),
        _write_csv_factory(factors._FACTOR_CONFIG["us"]["cache"]),
        lambda: factors.factor_frontier("us"),
    ),
    "ff_developed_exus": (
        factors._FACTOR_CONFIG["developed_exus"]["cache"],
        lambda: factors._fetch_factors(factors._FACTOR_CONFIG["developed_exus"]["url"]),
        _write_csv_factory(factors._FACTOR_CONFIG["developed_exus"]["cache"]),
        lambda: factors.factor_frontier("developed_exus"),
    ),
    "ff_umd": (
        factors._UMD_CACHE,
        factors._fetch_umd,
        _write_csv_factory(factors._UMD_CACHE),
        factors.umd_frontier,
    ),
    "cape": (
        shiller._CACHE_CSV,
        shiller.fetch_cape_dataframe,
        _write_csv_factory(shiller._CACHE_CSV, index=False),
        shiller.cape_frontier,
    ),
    "pe": (
        trailing_pe._CACHE_CSV,
        trailing_pe.fetch_trailing_pe_dataframe,
        _write_csv_factory(trailing_pe._CACHE_CSV, index=False),
        trailing_pe.trailing_pe_frontier,
    ),
}


def _frontier_of(frame) -> str:
    """Last data date of a fetched frame/series, for reporting."""
    try:
        if hasattr(frame, "columns") and "date" in getattr(frame, "columns", []):
            return str(frame["date"].iloc[-1])[:10]
        return str(frame.index[-1])[:10]
    except Exception:
        return "?"


def refresh(keys: list[str]) -> int:
    """Refresh each target; returns the number of FAILED targets.

    Three distinct per-file outcomes, never conflated:
      REFRESHED    fetch ok, write ok — old frontier -> new frontier
      FETCH FAILED network/parse failed — file untouched
      WRITE FAILED fetch SUCCEEDED (frontier reported) but the write was
                   blocked — file untouched, fetched frame discarded. This is
                   a local problem (permissions/locks), not a network one.
    """
    failures = 0
    for key in keys:
        path, fetch, write, frontier = _TARGETS[key]
        old = frontier()
        try:
            frame = fetch()
        except Exception as exc:
            failures += 1
            print(f"  {key:<18} FETCH FAILED — {type(exc).__name__}: {exc}  (file untouched)")
            continue
        fetched_through = _frontier_of(frame)
        try:
            write(frame)
        except Exception as exc:
            failures += 1
            print(
                f"  {key:<18} WRITE FAILED — fetched {len(frame)} rows through "
                f"{fetched_through} successfully, but could not write {path}: "
                f"{type(exc).__name__}: {exc}  (file untouched, fetched data discarded)"
            )
            continue
        print(f"  {key:<18} REFRESHED  {old} -> {frontier()}  ({len(frame)} rows)")
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh committed market-data inputs")
    parser.add_argument(
        "--files", nargs="+", choices=sorted(_TARGETS), metavar="KEY",
        help=f"Subset to refresh (default: all). Keys: {', '.join(sorted(_TARGETS))}",
    )
    args = parser.parse_args()
    keys = args.files or sorted(_TARGETS)

    print(f"Refreshing {len(keys)} market-data file(s):")
    failures = refresh(keys)

    diff = subprocess.run(
        ["git", "diff", "--stat", "--", "data"],
        cwd=str(ROOT), capture_output=True, text=True,
    ).stdout.strip()
    if diff:
        print("\nWorking-tree changes (review, then COMMIT — a refresh is a commit):")
        print(diff)
    else:
        print("\nNo working-tree changes (already current, or every refresh failed).")

    if failures:
        print(f"\n{failures} target(s) FAILED — see lines above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
