from __future__ import annotations

from typing import Any


def fetch_sample_news() -> list[dict[str, Any]]:
    return [
        {
            "id": "sample-1",
            "source": "sample",
            "headline": "NVIDIA jumps after analysts raise targets on AI demand",
            "summary": "Analysts cite stronger GPU demand and supply chain improvements.",
            "url": "",
            "datetime": "2026-03-28T12:00:00Z",
        },
        {
            "id": "sample-2",
            "source": "sample",
            "headline": "Tesla falls as regulators open probe after new incident",
            "summary": "The probe adds pressure on sentiment and delivery outlook.",
            "url": "",
            "datetime": "2026-03-28T12:01:00Z",
        },
    ]
