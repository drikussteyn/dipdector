"""
Free news retrieval — no API key, no subscription.

Two sources, both free:
  yfinance per-ticker news  — Yahoo's own feed, decent coverage of large caps
  Yahoo Finance RSS         — same source, reachable without the library

Both are unofficial. Yahoo can change or throttle either at any time, and the
volume is thinner than a paid feed. That is the trade for spending nothing. If
articles stop arriving, the pipeline degrades to "decline detected, cause not
determined" rather than breaking — which is the correct failure mode, and the
report says so explicitly rather than leaving a blank.

DEVLOG s.36 / s.44.5 — the as-of cutoff is enforced here, in fetch, so a
backtest can never see an article published after the date it is replaying.
"""

from __future__ import annotations

import datetime as dt
import time
from typing import List, Optional
from urllib.parse import quote

from .engine import Article

# Rough source-quality tiers, per devlog s.12. Anything unlisted falls to 5.
TIER_HINTS = {
    "reuters": 2, "bloomberg": 2, "associated press": 2, "ap": 2,
    "wall street journal": 2, "wsj": 2, "financial times": 2, "ft": 2,
    "cnbc": 3, "barron's": 3, "barrons": 3, "marketwatch": 3, "forbes": 3,
    "business insider": 4, "benzinga": 4, "zacks": 4, "investing.com": 4,
    "seeking alpha": 4, "motley fool": 5, "insider monkey": 5,
}


def _tier(source: str) -> int:
    s = (source or "").lower()
    for key, tier in TIER_HINTS.items():
        if key in s:
            return tier
    return 5


class FreeNewsProvider:
    """yfinance first, Yahoo RSS as the fallback. Deduplicated by headline."""

    name = "yahoo-free"

    def __init__(self, per_ticker_limit: int = 8, polite_delay: float = 0.3):
        self.per_ticker_limit = per_ticker_limit
        self.polite_delay = polite_delay

    def fetch(self, keywords: List[str], as_of: dt.datetime,
              lookback_days: int = 10) -> List[Article]:
        since = as_of - dt.timedelta(days=lookback_days)
        out, seen = [], set()

        for kw in keywords:
            for art in self._yfinance_news(kw) or self._rss(kw):
                if art is None:
                    continue
                # Hard cutoff. Never relax this — it is what keeps backtests honest.
                if not (since <= art.published_at <= as_of):
                    continue
                key = art.headline.strip().lower()[:120]
                if key in seen or not key:
                    continue
                seen.add(key)
                out.append(art)
            time.sleep(self.polite_delay)

        return sorted(out, key=lambda a: a.published_at)

    def _yfinance_news(self, ticker: str) -> Optional[List[Article]]:
        """Only meaningful for things that look like tickers, not phrases."""
        if " " in ticker or len(ticker) > 6:
            return None
        try:
            import yfinance as yf
            items = yf.Ticker(ticker).news or []
        except Exception:
            return None

        arts = []
        for it in items[:self.per_ticker_limit]:
            content = it.get("content", it)
            title = content.get("title") or it.get("title", "")
            if not title:
                continue
            ts = it.get("providerPublishTime")
            if ts:
                published = dt.datetime.fromtimestamp(ts, dt.timezone.utc)
            else:
                raw = content.get("pubDate") or ""
                try:
                    published = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
                except ValueError:
                    continue
            provider = (content.get("provider") or {}).get("displayName") \
                if isinstance(content.get("provider"), dict) else it.get("publisher", "")
            url = (content.get("canonicalUrl") or {}).get("url", "") \
                if isinstance(content.get("canonicalUrl"), dict) else it.get("link", "")
            arts.append(Article(
                headline=title, source=provider or "Yahoo Finance",
                published_at=published, url=url,
                summary=(content.get("summary") or "")[:300],
                source_tier=_tier(provider),
            ))
        return arts or None

    def _rss(self, query: str) -> List[Article]:
        try:
            import xml.etree.ElementTree as ET
            import requests
        except ImportError:
            return []
        url = ("https://feeds.finance.yahoo.com/rss/2.0/headline"
               f"?s={quote(query)}&region=US&lang=en-US")
        try:
            r = requests.get(url, timeout=15,
                             headers={"User-Agent": "Mozilla/5.0 (DipDector personal)"})
            r.raise_for_status()
            root = ET.fromstring(r.content)
        except Exception:
            return []

        arts = []
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            pub = item.findtext("pubDate")
            if not title or not pub:
                continue
            try:
                from email.utils import parsedate_to_datetime
                published = parsedate_to_datetime(pub)
                if published.tzinfo is None:
                    published = published.replace(tzinfo=dt.timezone.utc)
            except Exception:
                continue
            src = item.findtext("source") or "Yahoo Finance"
            arts.append(Article(
                headline=title, source=src, published_at=published,
                url=item.findtext("link") or "",
                summary=(item.findtext("description") or "")[:300],
                source_tier=_tier(src),
            ))
        return arts
