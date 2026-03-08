"""Daily news analysis: fetch, store, and summarize news for the dashboard."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


async def fetch_last_24h_news() -> list[dict]:
    """Fetch news using in-engine scraper (runs sync code in executor)."""
    from app.news_scraper import fetch_last_24h_news as _fetch_sync
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _fetch_sync)


def _hash_news_item(title: str, source: str) -> str:
    return hashlib.sha256(f"{title}|{source}".encode()).hexdigest()


async def get_news_from_mongo(store, hours: int = 24) -> tuple[list[dict], list[str]]:
    """Retrieve news from MongoDB (news_raw) from the last N hours.
    Returns (news_items for OpenAI, list of _id strings)."""
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    cursor = store.db.news_raw.find({"fetched_at": {"$gte": cutoff}}).sort("fetched_at", -1).limit(100)
    docs = await cursor.to_list(length=100)
    items = []
    ids = []
    for d in docs:
        items.append({
            "title": d.get("title", ""),
            "snippet": d.get("snippet", ""),
            "source": d.get("source", ""),
            "url": d.get("url", ""),
            "published_at": str(d.get("published_at", "")),
        })
        ids.append(str(d["_id"]))
    return items, ids


async def store_raw_news(store, news_items: list[dict]) -> tuple[list[str], list[str]]:
    """Store raw news in news_raw. Returns (inserted_ids, all_input_ids)."""
    inserted_ids = []
    input_ids = []
    coll = store.db.news_raw
    for item in news_items:
        h = _hash_news_item(item.get("title", ""), item.get("source", ""))
        existing = await coll.find_one({"hash": h}, projection={"_id": 1})
        if existing:
            input_ids.append(str(existing["_id"]))
            continue
        doc = {
            "title": item.get("title", ""),
            "snippet": item.get("snippet", ""),
            "source": item.get("source", ""),
            "url": item.get("url", ""),
            "published_at": item.get("published_at", ""),
            "fetched_at": datetime.now(timezone.utc),
            "tags": [],
            "hash": h,
        }
        r = await coll.insert_one(doc)
        oid_str = str(r.inserted_id)
        inserted_ids.append(oid_str)
        input_ids.append(oid_str)
    return inserted_ids, input_ids


async def summarize_with_openai(news_items: list[dict], api_key: str) -> dict | None:
    """Call OpenAI to summarize news. Returns structured JSON or None on failure."""
    if not api_key:
        logger.warning("OPENAI_API_KEY not set, skipping news summarization")
        return None
    if not news_items:
        return {
            "summary": "No news collected in the last 24 hours.",
            "sticky_notes": [],
            "asset_bias": {"BTC": "neutral", "ETH": "neutral", "XAU": "neutral"},
        }

    import httpx

    # Build input for the model
    input_text = "\n\n".join(
        f"Title: {i.get('title', '')}\nSnippet: {i.get('snippet', '')}\nSource: {i.get('source', '')}"
        for i in news_items[:80]  # Limit tokens
    )
    prompt = """You are a financial news analyst. Summarize the following news for traders.
Use ONLY the provided titles and snippets. Do NOT invent facts.
Produce short trader-readable notes. Identify tone for BTC, ETH, and XAU (Gold).

Respond with valid JSON only, no markdown:
{
  "summary": "one short paragraph summary",
  "sticky_notes": [
    {"title": "Macro tone", "text": "..."},
    {"title": "BTC", "text": "..."},
    {"title": "ETH", "text": "..."},
    {"title": "Gold (XAU)", "text": "..."}
  ],
  "asset_bias": {
    "BTC": "bullish|bearish|neutral",
    "ETH": "bullish|bearish|neutral",
    "XAU": "bullish|bearish|neutral"
  }
}

News:
"""
    prompt += input_text[:12000]  # Limit input size

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "gpt-3.5-turbo",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                },
            )
            if resp.status_code >= 400:
                logger.error("OpenAI API error: %s %s", resp.status_code, resp.text[:500])
                return None
            data = resp.json()
            content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
            if not content:
                return None
            content = content.strip()
            if content.startswith("```"):
                lines = content.split("\n")
                content = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
            return json.loads(content)
    except json.JSONDecodeError as e:
        logger.error("Failed to parse OpenAI JSON: %s", e)
        return None
    except Exception as e:
        logger.exception("OpenAI summarization failed: %s", e)
        return None


async def run_daily_news_analysis(
    store,
    timezone_str: str,
    openai_api_key: str,
    force: bool = False,
    scrape: bool = True,
) -> bool:
    """
    Run pipeline: optionally scrape, then retrieve news from MongoDB, summarize with OpenAI, store summary.
    If scrape=True, fetch from RSS and store in news_raw first.
    Summary is always generated from news retrieved from MongoDB.
    If force=True, overwrite today's summary.
    Returns True if summary was created/updated, False if skipped or failed.
    """
    from zoneinfo import ZoneInfo
    try:
        tz = ZoneInfo(timezone_str)
    except Exception:
        tz = ZoneInfo("America/New_York")
    today_local = datetime.now(tz).date()
    date_str = today_local.isoformat()

    coll_summary = store.db.news_daily_summary
    if not force:
        existing = await coll_summary.find_one({"date": date_str})
        if existing:
            logger.info("Daily news summary for %s already exists, skipping", date_str)
            return False

    if scrape:
        try:
            news_items = await fetch_last_24h_news()
            logger.info("Fetched %d news items from RSS", len(news_items))
            await store_raw_news(store, news_items)
            logger.info("Stored raw news in MongoDB")
        except Exception as e:
            logger.exception("Failed to fetch news: %s", e)
            return False

    # Retrieve news from MongoDB and generate summary with OpenAI
    news_from_db, input_news_ids = await get_news_from_mongo(store, hours=24)
    logger.info("Retrieved %d news from MongoDB for summary", len(news_from_db))
    if not news_from_db:
        # Fallback: use any recent news from DB
        cursor = store.db.news_raw.find().sort("fetched_at", -1).limit(100)
        docs = await cursor.to_list(length=100)
        news_from_db = [{"title": d.get("title", ""), "snippet": d.get("snippet", ""), "source": d.get("source", "")} for d in docs]
        input_news_ids = [str(d["_id"]) for d in docs]

    summary_data = await summarize_with_openai(news_from_db, openai_api_key)
    if not summary_data:
        summary_data = {
            "summary": "Summarization unavailable.",
            "sticky_notes": [],
            "asset_bias": {"BTC": "neutral", "ETH": "neutral", "XAU": "neutral"},
        }

    doc = {
        "date": date_str,
        "timezone": timezone_str,
        "created_at": datetime.now(timezone.utc),
        "summary": summary_data.get("summary", ""),
        "sticky_notes": summary_data.get("sticky_notes", []),
        "asset_bias": summary_data.get("asset_bias", {}),
        "input_news_ids": input_news_ids,
    }
    if force:
        await coll_summary.replace_one({"date": date_str}, doc, upsert=True)
    else:
        await coll_summary.insert_one(doc)
    logger.info("Created daily news summary for %s", date_str)
    return True
