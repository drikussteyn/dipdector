"""
Price data adapters.

DEVLOG s.30 / s.39: market data is deterministic, must carry provenance, and the
app must know when data is stale or incomplete.

Three implementations:
  YFinanceProvider  - free, no key, for the first pass
  EODHDProvider     - paid, point-in-time capable, the intended production feed
  FixtureProvider   - loads a local CSV of SYNTHETIC prices, for testing the
                      pipeline offline

DEVLOG s.44.1 forbids inventing market data. FixtureProvider therefore refuses
to run unless the file is explicitly marked synthetic, and every downstream
object it touches is stamped `synthetic=True` so no output can quietly pass
itself off as a real market observation.
"""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Protocol

import pandas as pd


@dataclass(frozen=True)
class PriceFrame:
    """Adjusted daily bars for a set of tickers, plus provenance."""

    close: pd.DataFrame      # index=date, columns=ticker
    volume: pd.DataFrame     # index=date, columns=ticker
    source: str
    retrieved_at: dt.datetime
    synthetic: bool = False

    def as_of(self, date: dt.date) -> "PriceFrame":
        """DEVLOG s.29 / s.44.5 — truncate so no future bar is ever visible."""
        ts = pd.Timestamp(date)
        return PriceFrame(
            close=self.close.loc[self.close.index <= ts],
            volume=self.volume.loc[self.volume.index <= ts],
            source=self.source,
            retrieved_at=self.retrieved_at,
            synthetic=self.synthetic,
        )

    def coverage_report(self) -> Dict[str, float]:
        """Fraction of non-null closes per ticker. Feeds the staleness warning."""
        return (self.close.notna().sum() / max(len(self.close), 1)).to_dict()


class PriceProvider(Protocol):
    name: str

    def fetch(self, tickers: List[str], start: dt.date, end: dt.date) -> PriceFrame:
        ...


class YFinanceProvider:
    """Free tier. Adjusted closes. No point-in-time index membership."""

    name = "yfinance"

    def fetch(self, tickers: List[str], start: dt.date, end: dt.date) -> PriceFrame:
        import yfinance as yf  # imported lazily so the module loads without it

        raw = yf.download(
            tickers, start=start, end=end + dt.timedelta(days=1),
            auto_adjust=True, progress=False, group_by="column",
        )
        if raw.empty:
            raise RuntimeError(
                f"yfinance returned no rows for {len(tickers)} tickers "
                f"between {start} and {end}. Check tickers and network access."
            )
        close = raw["Close"] if "Close" in raw else raw
        volume = raw["Volume"] if "Volume" in raw else pd.DataFrame(index=close.index)
        return PriceFrame(
            close=close.sort_index(),
            volume=volume.sort_index(),
            source="yfinance",
            retrieved_at=dt.datetime.now(dt.timezone.utc),
            synthetic=False,
        )


class EODHDProvider:
    """
    Intended production feed. Requires EODHD_API_KEY.

    Chosen because a single subscription covers adjusted EOD prices, volume,
    fundamentals, news, and — the part that matters most for devlog s.29 —
    S&P 500 historical constituents with join and leave dates.
    """

    name = "eodhd"
    BASE = "https://eodhd.com/api"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("EODHD_API_KEY")
        if not self.api_key:
            raise RuntimeError(
                "EODHD_API_KEY is not set. Export it, or run with "
                "--provider yfinance to use the free feed."
            )

    def fetch(self, tickers: List[str], start: dt.date, end: dt.date) -> PriceFrame:
        import requests

        closes, volumes = {}, {}
        for t in tickers:
            symbol = t if "." in t else f"{t}.US"
            r = requests.get(
                f"{self.BASE}/eod/{symbol}",
                params={"api_token": self.api_key, "fmt": "json",
                        "from": start.isoformat(), "to": end.isoformat(),
                        "period": "d"},
                timeout=30,
            )
            r.raise_for_status()
            df = pd.DataFrame(r.json())
            if df.empty:
                continue
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date").sort_index()
            closes[t] = df["adjusted_close"]
            volumes[t] = df["volume"]

        if not closes:
            raise RuntimeError("EODHD returned no usable rows for any ticker.")

        return PriceFrame(
            close=pd.DataFrame(closes),
            volume=pd.DataFrame(volumes),
            source="eodhd",
            retrieved_at=dt.datetime.now(dt.timezone.utc),
            synthetic=False,
        )


class FixtureProvider:
    """
    Loads SYNTHETIC bars from disk so the pipeline can be exercised without
    network access. Everything it produces is flagged synthetic.
    """

    name = "fixture-synthetic"

    def __init__(self, path: str):
        self.path = path

    def fetch(self, tickers: List[str], start: dt.date, end: dt.date) -> PriceFrame:
        df = pd.read_csv(self.path, parse_dates=["date"])
        if "synthetic" not in df.columns or not df["synthetic"].all():
            raise RuntimeError(
                f"{self.path} is not marked synthetic. FixtureProvider refuses "
                "to load data that might be mistaken for real market data."
            )
        close = df.pivot(index="date", columns="ticker", values="close")
        volume = df.pivot(index="date", columns="ticker", values="volume")
        mask = (close.index >= pd.Timestamp(start)) & (close.index <= pd.Timestamp(end))
        return PriceFrame(
            close=close.loc[mask],
            volume=volume.loc[mask],
            source=f"fixture:{os.path.basename(self.path)}",
            retrieved_at=dt.datetime.now(dt.timezone.utc),
            synthetic=True,
        )


def get_provider(kind: str, **kwargs) -> PriceProvider:
    if kind == "yfinance":
        return YFinanceProvider()
    if kind == "eodhd":
        return EODHDProvider(**kwargs)
    if kind == "fixture":
        return FixtureProvider(kwargs["path"])
    raise ValueError(f"Unknown provider: {kind}")
