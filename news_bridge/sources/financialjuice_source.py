"""FinancialJuice RSS 피드 — 가장 빠른 실시간 매크로 뉴스 소스.

financialjuice.com은 Bloomberg/Reuters급 속도로 경제 헤드라인을 제공.
RSS 피드로 requests 파싱 가능 (브라우저 불필요).

사용법:
    news_items = fetch_financialjuice_rss()
    for item in news_items:
        event = classify_news(item, watchlist)
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any
from xml.etree import ElementTree

import requests

logger = logging.getLogger("financialjuice_source")

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}

_RSS_URL = "https://www.financialjuice.com/feed.ashx?xy=rss"


def fetch_financialjuice_rss(
    max_items: int = 50,
) -> list[dict[str, Any]]:
    """FinancialJuice RSS 피드에서 최신 뉴스 가져오기.

    Returns: classify_news()용 news dict 리스트
    """
    try:
        resp = requests.get(_RSS_URL, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        logger.warning("FinancialJuice RSS fetch failed: %s", e)
        return []

    items: list[dict[str, Any]] = []

    try:
        root = ElementTree.fromstring(resp.content)
    except ElementTree.ParseError as e:
        logger.warning("FinancialJuice RSS parse error: %s", e)
        return []

    # RSS 2.0: <rss><channel><item>...</item></channel></rss>
    channel = root.find("channel")
    if channel is None:
        # Atom 형식 시도
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        entries = root.findall("atom:entry", ns)
        if not entries:
            logger.warning("FinancialJuice: no items found in RSS")
            return []
        for entry in entries[:max_items]:
            title = (entry.findtext("atom:title", "", ns) or "").strip()
            summary = (entry.findtext("atom:summary", "", ns) or "").strip()
            link = ""
            link_el = entry.find("atom:link", ns)
            if link_el is not None:
                link = link_el.get("href", "")
            pub_date = (entry.findtext("atom:updated", "", ns) or "").strip()
            entry_id = (entry.findtext("atom:id", "", ns) or link or title)

            if not title:
                continue

            items.append({
                "id": f"fj_{_clean_id(entry_id)}",
                "source": "FinancialJuice",
                "headline": title,
                "summary": summary or title,
                "url": link,
                "datetime": pub_date,
            })
        return items

    # RSS 2.0 처리
    for item_el in channel.findall("item")[:max_items]:
        title = (item_el.findtext("title") or "").strip()
        description = (item_el.findtext("description") or "").strip()
        link = (item_el.findtext("link") or "").strip()
        pub_date = (item_el.findtext("pubDate") or "").strip()
        guid = (item_el.findtext("guid") or link or title)
        category = (item_el.findtext("category") or "").strip()

        if not title:
            continue

        # HTML 태그 제거
        description = re.sub(r"<[^>]+>", "", description).strip()

        items.append({
            "id": f"fj_{_clean_id(guid)}",
            "source": "FinancialJuice",
            "headline": title,
            "summary": description or title,
            "url": link,
            "datetime": pub_date,
            "_meta": {
                "category": category,
                "source_site": "financialjuice.com",
            },
        })

    logger.info("FinancialJuice RSS: %d items fetched", len(items))
    return items


def _clean_id(text: str) -> str:
    """ID용 문자열 정리."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", text)[:80]
