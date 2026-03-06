"""RSS news scraper - runs inside the trading engine, no external process."""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone
from html import unescape

import feedparser
import requests

# RSS feeds: (source_name, feed_url, category)
RSS_FEEDS = [
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/", "crypto"),
    ("Cointelegraph", "https://cointelegraph.com/rss", "crypto"),
    ("Decrypt", "https://decrypt.co/feed", "crypto"),
    ("CryptoPotato", "https://cryptopotato.com/feed/", "crypto"),
    ("CryptoSlate", "https://cryptoslate.com/feed/", "crypto"),
    ("Bitcoinist", "https://bitcoinist.com/feed/", "crypto"),
    ("NewsBTC", "https://www.newsbtc.com/feed/", "crypto"),
    ("CryptoBriefing", "https://cryptobriefing.com/feed/", "crypto"),
    ("The Defiant", "https://thedefiant.io/feed/", "crypto"),
    ("BeInCrypto", "https://beincrypto.com/feed/", "crypto"),
    ("Reuters", "https://www.reuters.com/markets/feed/", "stock"),
    ("Bloomberg", "https://feeds.bloomberg.com/markets/news.rss", "stock"),
    ("Yahoo Finance", "https://finance.yahoo.com/news/rssindex", "stock"),
    ("CNBC", "https://www.cnbc.com/id/100003114/device/rss/rss.html", "stock"),
    ("MarketWatch", "https://feeds.marketwatch.com/marketwatch/topstories/", "stock"),
    ("Benzinga", "https://www.benzinga.com/feed/", "stock"),
]

REQUEST_DELAY_SECONDS = 1.5
USER_AGENT = "DemoBot-Engine/1.0 (Trading News Scraper)"


def _fetch_feed(url: str) -> feedparser.FeedParserDict | None:
    headers = {"User-Agent": USER_AGENT}
    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        return feedparser.parse(response.content)
    except Exception:
        return None


def _parse_published(entry) -> str | None:
    for key in ("published_parsed", "updated_parsed"):
        parsed = getattr(entry, key, None)
        if parsed:
            try:
                dt = datetime(*parsed[:6])
                return dt.isoformat()
            except (TypeError, ValueError):
                pass
    return None


def _get_content(entry) -> str:
    candidates = []
    if hasattr(entry, "content") and entry.content:
        for c in entry.content:
            val = c.get("value", "")
            if val:
                candidates.append(val)
    for key in ("summary", "description", "summary_detail"):
        val = getattr(entry, key, None)
        if isinstance(val, str) and val:
            candidates.append(val)
        elif hasattr(val, "value"):
            candidates.append(val.value)
    if not candidates:
        return ""

    def _text(s: str) -> str:
        s = unescape(s)
        s = re.sub(r"<[^>]+>", " ", s)
        return " ".join(s.split()).strip()

    best = max(candidates, key=lambda x: len(_text(x)))
    return _text(best)


def fetch_last_24h_news() -> list[dict]:
    """
    Fetch news from all RSS feeds from the last 24 hours.
    Returns: list of {title, snippet, source, url, published_at}
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    all_items: list[dict] = []
    seen_urls: set[str] = set()

    for source_name, feed_url, category in RSS_FEEDS:
        feed = _fetch_feed(feed_url)
        if not feed or not feed.entries:
            continue
        for entry in feed.entries:
            link = entry.get("link") or entry.get("id")
            if not link or link in seen_urls:
                continue
            title = (entry.get("title") or "").strip()
            if not title:
                continue
            published_at = _parse_published(entry) or ""
            if published_at:
                try:
                    dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if dt < cutoff:
                        continue
                except (TypeError, ValueError):
                    pass
            content = _get_content(entry)
            snippet = (content[:500].strip() if content else title[:200]) or "No snippet"
            all_items.append({
                "title": title,
                "snippet": snippet,
                "source": source_name,
                "url": link,
                "published_at": published_at or datetime.now(timezone.utc).isoformat(),
            })
            seen_urls.add(link)
        time.sleep(REQUEST_DELAY_SECONDS)

    all_items.sort(key=lambda x: x.get("published_at", ""), reverse=True)
    return all_items
