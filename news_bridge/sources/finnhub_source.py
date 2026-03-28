from __future__ import annotations

from typing import Any

import requests


def fetch_finnhub_news(api_key: str) -> list[dict[str, Any]]:
    url = "https://finnhub.io/api/v1/news"
    resp = requests.get(url, params={"category": "general", "token": api_key}, timeout=20)
    resp.raise_for_status()
    payload = resp.json()
    if not isinstance(payload, list):
        return []
    return payload[:30]
