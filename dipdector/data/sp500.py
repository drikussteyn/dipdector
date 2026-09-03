"""
S&P 500 constituents and their GICS classification.

DEVLOG s.4 requires constituents to be "dynamically determined from the
selected universe and classification data rather than hard-coded". The curated
seed table in `universe.py` never satisfied that — it was a six-industry
prototype stand-in, and it means the detector can only ever find a shock in an
industry somebody chose in advance. This module replaces it with the whole
index.

Source is the Wikipedia list of S&P 500 companies, which carries the GICS
Sector and GICS Sub-Industry for every constituent. That is reference data
(company -> industry), not market data, so caching it in the repository is
fine — and necessary, since a scan must not depend on Wikipedia being up.
Refresh it deliberately:

    python -m dipdector.data.sp500 --refresh

TWO LIMITS, both real:

  - This is CURRENT membership applied to every historical date. Same
    survivorship bias the seed table had; the backtest runner still prints the
    warning. Point-in-time membership is the fix and needs a paid feed.
  - Wikipedia publishes Sector and Sub-Industry but not the two middle GICS
    tiers. Those fields are left empty rather than guessed at, because
    inventing a classification is exactly the kind of quiet fabrication this
    codebase forbids. Nothing outside `universe.py` reads them; grouping is
    done on sub_industry.
"""

from __future__ import annotations

import argparse
import io
import os
from typing import List

import pandas as pd

WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
CACHE = os.path.join(os.path.dirname(__file__), "sp500_constituents.csv")


def fetch_constituents() -> pd.DataFrame:
    """Pull the live list. Raises rather than returning a partial table."""
    import requests

    r = requests.get(WIKI_URL, timeout=60,
                     headers={"User-Agent": "DipDector/1.0 (research tool)"})
    r.raise_for_status()
    df = pd.read_html(io.StringIO(r.text))[0]

    required = {"Symbol", "Security", "GICS Sector", "GICS Sub-Industry"}
    missing = required - set(df.columns)
    if missing:
        raise RuntimeError(
            f"The S&P 500 table is missing {sorted(missing)}. Wikipedia's "
            f"column layout has changed and this parser needs updating — "
            f"refusing to write a partial constituent list.")

    df = df[["Symbol", "Security", "GICS Sector", "GICS Sub-Industry"]].copy()
    df.columns = ["ticker", "name", "sector", "sub_industry"]

    # Class shares are written BRK.B on Wikipedia and BRK-B by Yahoo. Getting
    # this wrong silently drops the affected names from every scan.
    df["ticker"] = df["ticker"].str.strip().str.replace(".", "-", regex=False)
    df["name"] = df["name"].str.strip()

    if len(df) < 400:
        raise RuntimeError(
            f"Only {len(df)} constituents parsed, expected around 500. "
            f"Refusing to overwrite the cache with a truncated list.")
    return df.sort_values("ticker").reset_index(drop=True)


def refresh(path: str = CACHE) -> pd.DataFrame:
    df = fetch_constituents()
    df.to_csv(path, index=False)
    return df


def load(path: str = CACHE) -> pd.DataFrame:
    """Read the cached list. The cache is committed, so this works offline."""
    if not os.path.exists(path):
        raise RuntimeError(
            f"{path} is missing. Run `python -m dipdector.data.sp500 "
            f"--refresh` to fetch it.")
    return pd.read_csv(path)


def main() -> int:
    p = argparse.ArgumentParser(description="S&P 500 constituent list")
    p.add_argument("--refresh", action="store_true",
                   help="re-fetch from Wikipedia and rewrite the cache")
    a = p.parse_args()

    if a.refresh:
        before = len(load()) if os.path.exists(CACHE) else 0
        df = refresh()
        print(f"Wrote {len(df)} constituents to {CACHE} "
              f"(was {before}).")
    else:
        df = load()
        print(f"{len(df)} constituents cached at {CACHE}")

    vc = df["sub_industry"].value_counts()
    print(f"{df['sector'].nunique()} sectors, {len(vc)} sub-industries")
    print(f"{(vc >= 5).sum()} sub-industries have 5+ members "
          f"({vc[vc >= 5].sum()} companies); the rest are too small to show "
          f"breadth and will not be scored.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
