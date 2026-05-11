"""News headline lookup for the daily Discord candidate summary.

Tries yfinance first (no auth required), falls back to Finnhub
(requires `news.finnhub_api_key` in config or FINNHUB_API_KEY env var).

Both sources are wrapped with hard 5-second timeouts and narrow
exception handlers. A failure for a single ticker is non-fatal and
returns None: per CLAUDE.md the partial-failure-in-batch carve-out
applies here, since one missing headline must not abort the daily
Discord summary for the rest of the candidates.

This module is read-only and never touches Alpaca, IBKR, or the
trading DB. Safe to import from any job context.
"""

from __future__ import annotations

import concurrent.futures
import logging
from datetime import date, timedelta
from typing import Optional, TypedDict

import requests

logger = logging.getLogger(__name__)

PER_SOURCE_TIMEOUT_SEC = 5.0
MAX_TITLE_LEN = 180


class Headline(TypedDict):
    title: str
    url: str


def _fetch_yfinance(ticker: str) -> Optional[Headline]:
    """Most recent article from yfinance.Ticker.news, or None.

    yfinance's news property does a synchronous HTTP fetch that we
    cannot pass a timeout to directly. We wrap it in a thread with a
    hard wall-clock cap so a hanging Yahoo endpoint cannot stall the
    Discord summary job.
    """
    import yfinance as yf

    def _read_news() -> list:
        return yf.Ticker(ticker).news or []

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(_read_news)
            news = future.result(timeout=PER_SOURCE_TIMEOUT_SEC)
    except concurrent.futures.TimeoutError:
        logger.warning("yfinance news timeout for %s", ticker)
        return None
    except (requests.RequestException, ValueError, KeyError, AttributeError) as e:
        logger.warning("yfinance news error for %s: %s", ticker, e)
        return None

    if not news:
        return None

    # yfinance shapes have shifted across versions; defensively probe known
    # locations for title + url.
    article = news[0] if news else {}
    if not isinstance(article, dict):
        return None

    content = article.get("content")
    if isinstance(content, dict):
        title = content.get("title") or ""
        url = (
            (content.get("clickThroughUrl") or {}).get("url")
            or (content.get("canonicalUrl") or {}).get("url")
            or ""
        )
    else:
        title = article.get("title", "")
        url = article.get("link", "")

    if not title:
        return None
    return {"title": title[:MAX_TITLE_LEN], "url": url or ""}


def _fetch_finnhub(ticker: str, api_key: str, since: date) -> Optional[Headline]:
    """Most recent Finnhub company-news article since `since`, or None."""
    today = date.today()
    try:
        resp = requests.get(
            "https://finnhub.io/api/v1/company-news",
            params={
                "symbol": ticker,
                "from": since.isoformat(),
                "to": today.isoformat(),
                "token": api_key,
            },
            timeout=PER_SOURCE_TIMEOUT_SEC,
        )
        resp.raise_for_status()
        articles = resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning("Finnhub news error for %s: %s", ticker, e)
        return None

    if not isinstance(articles, list) or not articles:
        return None

    # Finnhub returns most-recent first as of writing, but sort defensively.
    # `datetime` is a unix timestamp (int).
    sorted_articles = sorted(
        (a for a in articles if isinstance(a, dict)),
        key=lambda a: a.get("datetime", 0) or 0,
        reverse=True,
    )
    if not sorted_articles:
        return None

    article = sorted_articles[0]
    title = article.get("headline", "") or ""
    url = article.get("url", "") or ""
    if not title:
        return None
    return {"title": title[:MAX_TITLE_LEN], "url": url}


def fetch_headline(
    ticker: str,
    finnhub_key: Optional[str] = None,
    since_days: int = 3,
) -> Optional[Headline]:
    """Return the most recent news headline for `ticker`, or None.

    Tries yfinance first; if that returns nothing and a Finnhub API
    key is provided, falls back to Finnhub. Both calls have hard
    5-second timeouts and never raise — partial-failure-in-batch.
    """
    headline = _fetch_yfinance(ticker)
    if headline is not None:
        return headline

    if finnhub_key:
        since = date.today() - timedelta(days=since_days)
        headline = _fetch_finnhub(ticker, finnhub_key, since)
        if headline is not None:
            return headline

    return None
