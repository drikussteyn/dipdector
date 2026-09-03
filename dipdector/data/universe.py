"""
Universe and industry classification.

DEVLOG s.3 / s.4 require GICS sector -> industry group -> industry ->
sub-industry, and s.4 requires constituents to be "dynamically determined from
the selected universe and classification data rather than hard-coded".

REALITY CHECK: real GICS Direct is a licensed S&P Global / MSCI product sold at
enterprise pricing. It is not something you subscribe to online. So this module
defines a *classification adapter* interface. Today it is backed by a curated
seed table; later it can be backed by a licensed GICS feed or a vendor's own
taxonomy without any change to the detection engine.

The curated table below is reference data (company -> industry), not market
data. It is transcribed from public company descriptions and index membership,
not invented, and it is deliberately small: the prototype targets one industry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Protocol
import datetime as dt

import pandas as pd


@dataclass(frozen=True)
class Company:
    ticker: str
    name: str
    exchange: str
    sector: str
    industry_group: str
    industry: str
    sub_industry: str
    index_membership: tuple = ()
    market_cap: Optional[float] = None

    @property
    def classification_path(self) -> str:
        return f"{self.sector} > {self.industry_group} > {self.industry} > {self.sub_industry}"


class ClassificationProvider(Protocol):
    """Swap target: seed table now, licensed GICS feed later."""

    name: str

    def companies_as_of(self, as_of: dt.date) -> List[Company]:
        ...

    def industry_members(self, industry: str, as_of: dt.date) -> List[Company]:
        ...


# --- Curated seed table -----------------------------------------------------
# Scope: the semiconductor complex, which is the prototype's replay target.
# Sub-industry split matters here: "Semiconductors" and "Semiconductor
# Materials & Equipment" are separate GICS sub-industries and behave
# differently under export-control shocks, which is exactly the distinction
# devlog s.4 says sector-level grouping would destroy.

_SEED: List[Company] = [
    # Semiconductors
    Company("NVDA", "NVIDIA", "NASDAQ", "Information Technology",
            "Semiconductors & Semiconductor Equipment", "Semiconductors",
            "Semiconductors", ("SP500", "NDX")),
    Company("AMD", "Advanced Micro Devices", "NASDAQ", "Information Technology",
            "Semiconductors & Semiconductor Equipment", "Semiconductors",
            "Semiconductors", ("SP500", "NDX")),
    Company("INTC", "Intel", "NASDAQ", "Information Technology",
            "Semiconductors & Semiconductor Equipment", "Semiconductors",
            "Semiconductors", ("SP500", "NDX")),
    Company("MU", "Micron Technology", "NASDAQ", "Information Technology",
            "Semiconductors & Semiconductor Equipment", "Semiconductors",
            "Semiconductors", ("SP500", "NDX")),
    Company("AVGO", "Broadcom", "NASDAQ", "Information Technology",
            "Semiconductors & Semiconductor Equipment", "Semiconductors",
            "Semiconductors", ("SP500", "NDX")),
    Company("QCOM", "Qualcomm", "NASDAQ", "Information Technology",
            "Semiconductors & Semiconductor Equipment", "Semiconductors",
            "Semiconductors", ("SP500", "NDX")),
    Company("TXN", "Texas Instruments", "NASDAQ", "Information Technology",
            "Semiconductors & Semiconductor Equipment", "Semiconductors",
            "Semiconductors", ("SP500", "NDX")),
    Company("ADI", "Analog Devices", "NASDAQ", "Information Technology",
            "Semiconductors & Semiconductor Equipment", "Semiconductors",
            "Semiconductors", ("SP500", "NDX")),
    Company("NXPI", "NXP Semiconductors", "NASDAQ", "Information Technology",
            "Semiconductors & Semiconductor Equipment", "Semiconductors",
            "Semiconductors", ("SP500", "NDX")),
    Company("ON", "ON Semiconductor", "NASDAQ", "Information Technology",
            "Semiconductors & Semiconductor Equipment", "Semiconductors",
            "Semiconductors", ("SP500",)),
    # Semiconductor Materials & Equipment
    Company("AMAT", "Applied Materials", "NASDAQ", "Information Technology",
            "Semiconductors & Semiconductor Equipment", "Semiconductors",
            "Semiconductor Materials & Equipment", ("SP500", "NDX")),
    Company("LRCX", "Lam Research", "NASDAQ", "Information Technology",
            "Semiconductors & Semiconductor Equipment", "Semiconductors",
            "Semiconductor Materials & Equipment", ("SP500", "NDX")),
    Company("KLAC", "KLA Corporation", "NASDAQ", "Information Technology",
            "Semiconductors & Semiconductor Equipment", "Semiconductors",
            "Semiconductor Materials & Equipment", ("SP500", "NDX")),
    Company("TER", "Teradyne", "NASDAQ", "Information Technology",
            "Semiconductors & Semiconductor Equipment", "Semiconductors",
            "Semiconductor Materials & Equipment", ("SP500", "NDX")),
    # Passenger Airlines
    Company("DAL", "Delta Air Lines", "NYSE", "Industrials",
            "Transportation", "Passenger Airlines", "Passenger Airlines", ("SP500",)),
    Company("UAL", "United Airlines", "NASDAQ", "Industrials",
            "Transportation", "Passenger Airlines", "Passenger Airlines", ("SP500",)),
    Company("AAL", "American Airlines", "NASDAQ", "Industrials",
            "Transportation", "Passenger Airlines", "Passenger Airlines", ("SP500",)),
    Company("LUV", "Southwest Airlines", "NYSE", "Industrials",
            "Transportation", "Passenger Airlines", "Passenger Airlines", ("SP500",)),
    Company("ALK", "Alaska Air Group", "NYSE", "Industrials",
            "Transportation", "Passenger Airlines", "Passenger Airlines", ("SP500",)),
    # Hotels, Resorts & Cruise Lines
    Company("MAR", "Marriott International", "NASDAQ", "Consumer Discretionary",
            "Consumer Services", "Hotels, Restaurants & Leisure",
            "Hotels, Resorts & Cruise Lines", ("SP500", "NDX")),
    Company("HLT", "Hilton Worldwide", "NYSE", "Consumer Discretionary",
            "Consumer Services", "Hotels, Restaurants & Leisure",
            "Hotels, Resorts & Cruise Lines", ("SP500",)),
    Company("RCL", "Royal Caribbean", "NYSE", "Consumer Discretionary",
            "Consumer Services", "Hotels, Restaurants & Leisure",
            "Hotels, Resorts & Cruise Lines", ("SP500",)),
    Company("CCL", "Carnival", "NYSE", "Consumer Discretionary",
            "Consumer Services", "Hotels, Restaurants & Leisure",
            "Hotels, Resorts & Cruise Lines", ("SP500",)),
    Company("WYNN", "Wynn Resorts", "NASDAQ", "Consumer Discretionary",
            "Consumer Services", "Hotels, Restaurants & Leisure",
            "Hotels, Resorts & Cruise Lines", ("SP500",)),
    # Regional Banks
    Company("USB", "U.S. Bancorp", "NYSE", "Financials", "Banks", "Banks",
            "Regional Banks", ("SP500",)),
    Company("PNC", "PNC Financial Services", "NYSE", "Financials", "Banks",
            "Banks", "Regional Banks", ("SP500",)),
    Company("TFC", "Truist Financial", "NYSE", "Financials", "Banks", "Banks",
            "Regional Banks", ("SP500",)),
    Company("FITB", "Fifth Third Bancorp", "NASDAQ", "Financials", "Banks",
            "Banks", "Regional Banks", ("SP500",)),
    Company("RF", "Regions Financial", "NYSE", "Financials", "Banks", "Banks",
            "Regional Banks", ("SP500",)),
    Company("KEY", "KeyCorp", "NYSE", "Financials", "Banks", "Banks",
            "Regional Banks", ("SP500",)),
    # Oil & Gas Equipment & Services — included deliberately, because this is
    # the industry that did NOT come back after 2014.
    Company("SLB", "SLB", "NYSE", "Energy", "Energy",
            "Energy Equipment & Services", "Oil & Gas Equipment & Services", ("SP500",)),
    Company("HAL", "Halliburton", "NYSE", "Energy", "Energy",
            "Energy Equipment & Services", "Oil & Gas Equipment & Services", ("SP500",)),
    Company("BKR", "Baker Hughes", "NASDAQ", "Energy", "Energy",
            "Energy Equipment & Services", "Oil & Gas Equipment & Services", ("SP500",)),
    Company("FTI", "TechnipFMC", "NYSE", "Energy", "Energy",
            "Energy Equipment & Services", "Oil & Gas Equipment & Services", ()),
    Company("NOV", "NOV Inc.", "NYSE", "Energy", "Energy",
            "Energy Equipment & Services", "Oil & Gas Equipment & Services", ("SP500",)),
]


class SeedClassificationProvider:
    """
    Static seed table. No point-in-time membership.

    LIMITATION (devlog s.29): this uses *current* index membership for all
    dates, which is survivorship bias. Any backtest run against this provider
    must be treated as indicative only. Replace with a point-in-time membership
    feed (EODHD's S&P 500 historical constituents endpoint carries join/leave
    dates) before any result is used to judge the strategy.
    """

    name = "seed-static"
    point_in_time = False

    def __init__(self, companies: Optional[List[Company]] = None):
        self._companies = companies or list(_SEED)

    def companies_as_of(self, as_of: dt.date) -> List[Company]:
        return list(self._companies)

    def industry_members(self, industry: str, as_of: dt.date) -> List[Company]:
        return [c for c in self.companies_as_of(as_of)
                if industry in (c.industry, c.sub_industry)]

    def industries(self, as_of: dt.date, level: str = "sub_industry") -> Dict[str, List[Company]]:
        out: Dict[str, List[Company]] = {}
        for c in self.companies_as_of(as_of):
            key = getattr(c, level)
            out.setdefault(key, []).append(c)
        return out


class SP500ClassificationProvider(SeedClassificationProvider):
    """
    The whole S&P 500, grouped by industry.

    This is what devlog s.4 actually asked for: constituents determined from
    the universe and its classification data, not chosen in advance. With the
    seed table the detector could only find a shock in one of six industries
    somebody had picked; a crash anywhere else in the index was invisible to
    it, and looked exactly like a quiet day.

    Industry is the grouping level because it is the one that corresponds to
    companies that actually compete with each other — "Semiconductors",
    "Banks - Regional", "Airlines", "Railroads". Sector is too coarse:
    "Technology" spans both chip fabs and payroll software, which do not fall
    together for the same reasons.

    Constituents come from the SPDR fund's own holdings file and the
    classification from the price provider; see data/sp500.py. Neither is
    crowd-edited.

    Groups smaller than `min_industry_members` are still scored out by
    engine/metrics.py, which is the correct behaviour rather than a gap: three
    companies falling together is a coincidence, not industry-wide breadth.
    """

    name = "sp500-current"
    point_in_time = False           # current membership; see the module note

    def __init__(self, path: Optional[str] = None):
        from .sp500 import load

        df = load(path) if path else load()
        super().__init__([
            Company(
                ticker=r.ticker, name=r.name, exchange="",
                sector=r.sector,
                # The vendor taxonomy has two levels, not GICS's four. The
                # middle tiers are left empty rather than invented.
                industry_group="", industry="",
                sub_industry=r.sub_industry,
                index_membership=("S&P 500",),
                market_cap=(float(r.market_cap)
                            if getattr(r, "market_cap", None) and
                            pd.notna(r.market_cap) else None),
            )
            for r in df.itertuples(index=False)
        ])


def default_provider() -> ClassificationProvider:
    """
    The full index when its constituent list is available, the seed table
    otherwise. The fallback keeps the tests and the synthetic fixtures — which
    are built around the six-industry table — working unchanged.
    """
    try:
        return SP500ClassificationProvider()
    except Exception:                        # noqa: BLE001 — cache absent
        return SeedClassificationProvider()
