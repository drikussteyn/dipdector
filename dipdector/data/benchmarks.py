"""
Benchmark resolution.

Two comparators, and they do different jobs.

THE S&P 500 IS ALWAYS THE PRIMARY. It never changes with the industry. It is
the reference for "is this an industry problem or is everything down", and it
is also the quality gate: the universe is drawn from it because those companies
are large, established, and mostly able to survive a bad year. A rebound thesis
needs the company to still exist when the event resolves.

  One caveat on that reasoning, because it matters when real money is involved:
  S&P 500 membership lowers bankruptcy risk, it does not remove it. Lehman
  Brothers, Washington Mutual, General Motors, Circuit City and Hertz were all
  index members shortly before filing. Membership screens for size, not for
  balance-sheet safety, and the index contains plenty of heavily indebted
  companies. It is a good filter and a bad guarantee. The structural-risk score
  exists precisely because the index cannot do that job on its own.

THE SECOND COMPARATOR IS THE INDUSTRY'S OWN ETF, resolved per industry — never
a fixed index. Comparing airlines to the Nasdaq-100 tells you nothing about
airlines. Resolution walks sub-industry, then industry, then the sector SPDR.

  A caveat here too: an industry ETF usually holds the same companies we are
  measuring, so "the ETF confirms the decline" is partly circular. Its real
  value is coverage of names OUTSIDE the S&P 500 universe — mid-caps, foreign
  listings, pure-plays the index excludes. If the ETF falls harder than our
  large-cap basket, the smaller operators are being hit worse, which is
  evidence about severity. Because of the overlap this is weighted lightly in
  the shock score (5%) and is treated as confirmation, not as an independent
  signal.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional


@dataclass(frozen=True)
class Benchmark:
    ticker: str
    name: str
    overlap: str          # how much it duplicates our own universe
    note: str = ""


# Most specific first. Tickers are real, widely traded US-listed ETFs.
SUB_INDUSTRY_BENCHMARKS: Dict[str, Benchmark] = {
    "Semiconductors": Benchmark(
        "SOXX", "iShares Semiconductor ETF", "high",
        "Holds most of the same large-cap names, plus equipment makers."),
    "Semiconductor Materials & Equipment": Benchmark(
        "SOXX", "iShares Semiconductor ETF", "high",
        "No clean equipment-only ETF; SOXX blends chips and equipment."),
    "Passenger Airlines": Benchmark(
        "JETS", "US Global Jets ETF", "medium",
        "Includes regional carriers and foreign airlines the S&P 500 excludes."),
    "Hotels, Resorts & Cruise Lines": Benchmark(
        "PEJ", "Invesco Leisure and Entertainment ETF", "low",
        "Broader leisure exposure; only partly overlaps this group."),
    "Regional Banks": Benchmark(
        "KRE", "SPDR S&P Regional Banking ETF", "medium",
        "Equal-weighted and skewed smaller — often the first to show stress."),
    "Oil & Gas Equipment & Services": Benchmark(
        "OIH", "VanEck Oil Services ETF", "high",
        "Very concentrated in the same handful of large service names."),
    "Oil & Gas Exploration & Production": Benchmark(
        "XOP", "SPDR S&P Oil & Gas Exploration ETF", "medium", ""),
    "Biotechnology": Benchmark(
        "XBI", "SPDR S&P Biotech ETF", "low",
        "Equal-weighted and small-cap heavy; a very different risk profile."),
    "Homebuilding": Benchmark(
        "ITB", "iShares U.S. Home Construction ETF", "medium", ""),
}

INDUSTRY_BENCHMARKS: Dict[str, Benchmark] = {
    "Semiconductors": Benchmark("SOXX", "iShares Semiconductor ETF", "high", ""),
    "Banks": Benchmark("KBE", "SPDR S&P Bank ETF", "medium", ""),
    "Passenger Airlines": Benchmark("JETS", "US Global Jets ETF", "medium", ""),
    "Energy Equipment & Services": Benchmark(
        "OIH", "VanEck Oil Services ETF", "high", ""),
    "Hotels, Restaurants & Leisure": Benchmark(
        "PEJ", "Invesco Leisure and Entertainment ETF", "low", ""),
    "Software": Benchmark("IGV", "iShares Expanded Tech-Software ETF", "high", ""),
    "Metals & Mining": Benchmark("XME", "SPDR S&P Metals & Mining ETF", "medium", ""),
}

# Last resort. Sector SPDRs are S&P 500 subsets, so overlap is high by
# construction and they add little independent information.
SECTOR_BENCHMARKS: Dict[str, Benchmark] = {
    "Information Technology": Benchmark("XLK", "Technology Select Sector SPDR", "high",
                                        "An S&P 500 subset — largely the same companies."),
    "Financials": Benchmark("XLF", "Financial Select Sector SPDR", "high", ""),
    "Energy": Benchmark("XLE", "Energy Select Sector SPDR", "high", ""),
    "Industrials": Benchmark("XLI", "Industrial Select Sector SPDR", "high", ""),
    "Health Care": Benchmark("XLV", "Health Care Select Sector SPDR", "high", ""),
    "Consumer Discretionary": Benchmark("XLY", "Consumer Discretionary Select Sector SPDR", "high", ""),
    "Consumer Staples": Benchmark("XLP", "Consumer Staples Select Sector SPDR", "high", ""),
    "Utilities": Benchmark("XLU", "Utilities Select Sector SPDR", "high", ""),
    "Materials": Benchmark("XLB", "Materials Select Sector SPDR", "high", ""),
    "Real Estate": Benchmark("XLRE", "Real Estate Select Sector SPDR", "high", ""),
    "Communication Services": Benchmark("XLC", "Communication Services Select Sector SPDR", "high", ""),
}


def resolve(sub_industry: str, industry: str = "",
            sector: str = "") -> Optional[Benchmark]:
    """Most specific match wins. Returns None rather than guessing."""
    for table, key in ((SUB_INDUSTRY_BENCHMARKS, sub_industry),
                       (INDUSTRY_BENCHMARKS, industry),
                       (INDUSTRY_BENCHMARKS, sub_industry),
                       (SECTOR_BENCHMARKS, sector)):
        if key and key in table:
            return table[key]
    return None


def all_tickers() -> list:
    """Every benchmark ticker, for the price fetch."""
    seen = {}
    for table in (SUB_INDUSTRY_BENCHMARKS, INDUSTRY_BENCHMARKS, SECTOR_BENCHMARKS):
        for b in table.values():
            seen[b.ticker] = b
    return sorted(seen)
