from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import requests


@dataclass(slots=True)
class KiwoomRestConfig:
    base_url: str
    app_key: str
    app_secret: str
    account_no: str
    option_order_tr_code: str


class KiwoomRestOptionBrokerTemplate:
    """
    키움 REST/OAuth형 구조를 위한 템플릿.

    실전에서 바로 가장 잘 먹히는 건 형님 기존 옵션 엔진을 command/webhook으로 부르는 방식이라,
    이 클래스는 REST 직결 확장용 템플릿으로 제공합니다.
    """

    def __init__(self, cfg: KiwoomRestConfig) -> None:
        self.cfg = cfg
        self.session = requests.Session()
        self._access_token: str | None = None
        self._expires_at = 0.0

    def authenticate(self) -> str:
        if self._access_token and time.time() < self._expires_at - 30:
            return self._access_token
        url = f"{self.cfg.base_url}/oauth2/token"
        payload = {
            "grant_type": "client_credentials",
            "appkey": self.cfg.app_key,
            "appsecret": self.cfg.app_secret,
        }
        resp = self.session.post(url, json=payload, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        token = data.get("access_token") or data.get("token")
        if not token:
            raise RuntimeError(f"Kiwoom token not returned: {data}")
        self._access_token = str(token)
        self._expires_at = time.time() + int(data.get("expires_in", 3600))
        return self._access_token

    def place_order(self, order_body: dict[str, Any]) -> dict[str, Any]:
        token = self.authenticate()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "appkey": self.cfg.app_key,
            "appsecret": self.cfg.app_secret,
            "tr_cd": self.cfg.option_order_tr_code,
        }
        url = f"{self.cfg.base_url}/api/dostk/overseas-option/order"
        resp = self.session.post(url, headers=headers, json=order_body, timeout=20)
        resp.raise_for_status()
        return resp.json()
