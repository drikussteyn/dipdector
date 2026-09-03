"""
S&P 500 constituents and their industry classification.

TWO SOURCES, BOTH PRIMARY. NO CROWD-EDITED DATA ANYWHERE.

  Constituents come from the SPDR S&P 500 ETF's own daily holdings file,
  published by State Street, who run the fund. This is not a description of
  the index — it is the list of what the fund actually holds, stamped with the
  date it was struck. If a company is in it, real money is invested in it on
  the strength of index membership.

  Classification comes from the same price provider the rest of the app
  already depends on. Its industry taxonomy is at the level that matters here
  — "Semiconductors", "Banks - Regional", "Airlines", "Railroads" — which is
  companies that genuinely compete, not a sector bucket that lumps chip fabs
  in with payroll software.

An earlier version of this module read both from Wikipedia. It does not any
more, deliberately: a crowd-edited page is not an acceptable source for the
universe a research tool measures, however convenient its GICS column was.

Refresh deliberately, since it costs a few minutes of requests:

    python -m dipdector.data.sp500 --refresh

TWO LIMITS, both real:

  - This is CURRENT membership applied to every historical date. A company
    that fell and never recovered left the index and is absent from the very
    history used to judge whether falls recover, so every recovery statistic
    is measured on survivors and is optimistic. Removing this needs a feed
    carrying historical constituents AND prices for delisted securities. The
    free feed has neither: delisted tickers return nothing, or worse, are
    recycled — SBNY returns bars from 2024 although Signature Bank failed in
    March 2023, and CC returns a decade although Circuit City died in 2009.
    Those belong to different companies. This module therefore does not
    pretend to be point-in-time.

  - Classification is a vendor taxonomy, not licensed GICS. Its groupings are
    reasonable and it is honest about being what it is.
"""

from __future__ import annotations

import argparse
import io
import os
import time
from typing import Dict, List, Optional

import pandas as pd

SPDR_HOLDINGS = ("https://www.ssga.com/us/en/intermediary/library-content/"
                 "products/fund-data/etfs/us/holdings-daily-us-en-spy.xlsx")
UA = {"User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                     "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 "
                     "Safari/537.36")}
CACHE = os.path.join(os.path.dirname(__file__), "sp500_constituents.csv")


def fetch_holdings() -> pd.DataFrame:
    """Constituents from the fund's own holdings file. Raises on anything odd."""
    import requests

    r = requests.get(SPDR_HOLDINGS, headers=UA, timeout=90)
    r.raise_for_status()
    if r.content[:2] != b"PK":
        raise RuntimeError(
            "The SPDR holdings endpoint returned something that is not a "
            "spreadsheet. Refusing to parse it rather than guess at a "
            "universe.")

    raw = pd.read_excel(io.BytesIO(r.content), header=None)
    hdr = next((i for i in range(len(raw))
                if str(raw.iloc[i, 0]).strip().lower() == "name"), None)
    if hdr is None:
        raise RuntimeError("No header row in the SPDR holdings file; the "
                           "layout has changed and this parser needs updating.")

    df = pd.read_excel(io.BytesIO(r.content), header=hdr)
    if not {"Ticker", "Name"} <= set(df.columns):
        raise RuntimeError(f"SPDR holdings lack Ticker/Name: {list(df.columns)}")

    df = df[["Ticker", "Name"]].dropna()
    df["ticker"] = (df["Ticker"].astype(str).str.strip().str.upper()
                    .str.replace(".", "-", regex=False))
    df["name"] = df["Name"].astype(str).str.strip()
    # Drop cash lines, futures and other non-equity rows.
    df = df[df["ticker"].str.fullmatch(r"[A-Z][A-Z0-9-]{0,6}")]
    df = df[~df["name"].str.contains("CASH|FUTURE|RECEIVABLE|PAYABLE",
                                     case=False, na=False)]
    df = df[["ticker", "name"]].drop_duplicates("ticker")

    if len(df) < 400:
        raise RuntimeError(
            f"Only {len(df)} holdings parsed, expected around 500. Refusing "
            f"to overwrite the cache with a truncated universe.")
    return df.sort_values("ticker").reset_index(drop=True)


def classify(tickers: List[str], polite: float = 0.05) -> pd.DataFrame:
    """Sector and industry per ticker, from the price provider."""
    import yfinance as yf

    rows, failed = [], []
    for i, t in enumerate(tickers, 1):
        try:
            info = yf.Ticker(t).info or {}
            sector, industry = info.get("sector"), info.get("industry")
        except Exception:                    # noqa: BLE001 — recorded below
            sector = industry = None
        if sector and industry:
            rows.append({"ticker": t, "sector": sector,
                         "sub_industry": industry})
        else:
            failed.append(t)
        if i % 50 == 0:
            print(f"    classified {i}/{len(tickers)}", flush=True)
        time.sleep(polite)

    if failed:
        # Named, not silently dropped: an unclassified company is invisible to
        # the detector, and a shrinking universe must never be quiet.
        print(f"  {len(failed)} could not be classified and are excluded: "
              f"{', '.join(failed[:12])}{' ...' if len(failed) > 12 else ''}")
    return pd.DataFrame(rows)


def refresh(path: str = CACHE) -> pd.DataFrame:
    holdings = fetch_holdings()
    print(f"  {len(holdings)} holdings from the SPDR fund file")
    cls = classify(holdings["ticker"].tolist())
    df = holdings.merge(cls, on="ticker", how="inner")
    if len(df) < 400:
        raise RuntimeError(
            f"Only {len(df)} constituents survived classification, expected "
            f"around 500. Refusing to write a truncated universe.")
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
                   help="re-fetch holdings and classification, rewrite cache")
    a = p.parse_args()

    if a.refresh:
        before = len(load()) if os.path.exists(CACHE) else 0
        df = refresh()
        print(f"Wrote {len(df)} constituents to {CACHE} (was {before}).")
    else:
        df = load()
        print(f"{len(df)} constituents cached at {CACHE}")

    vc = df["sub_industry"].value_counts()
    print(f"{df['sector'].nunique()} sectors, {len(vc)} industries")
    print(f"{(vc >= 3).sum()} industries have 3+ members "
          f"({vc[vc >= 3].sum()} companies); smaller groups cannot show "
          f"breadth and are not scored.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
