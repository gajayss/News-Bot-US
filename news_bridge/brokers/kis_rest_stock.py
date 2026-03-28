from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import requests


@dataclass(slots=True)
class KISConfig:
    base_url: str
    app_key: str
    app_secret: str
    cano: str
    acnt_prdt_cd: str
    exchange_code: str
    buy_tr_id: str
    sell_tr_id: str
    order_price: str = "0"
    order_type: str = "00"
    simulate: bool = False


class KISRestStockBroker:
    def __init__(self, cfg: KISConfig) -> None:
        self.cfg = cfg
        self._access_token: str | None = None
        self._token_expire_at: float = 0.0
        self.session = requests.Session()

    def _token_valid(self) -> bool:
        return self._access_token is not None and time.time() < self._token_expire_at - 30

    def authenticate(self, max_retries: int = 3) -> str:
        if self._token_valid():
            return self._access_token or ""
        url = f"{self.cfg.base_url}/oauth2/tokenP"
        payload = {
            "grant_type": "client_credentials",
            "appkey": self.cfg.app_key,
            "appsecret": self.cfg.app_secret,
        }
        last_exc: Exception | None = None
        for attempt in range(max_retries):
            try:
                resp = self.session.post(url, json=payload, timeout=20)
                resp.raise_for_status()
                data = resp.json()
                token = data.get("access_token")
                if not token:
                    raise RuntimeError(f"KIS token not returned: {data}")
                self._access_token = str(token)
                expires_in = int(data.get("expires_in", 3600))
                self._token_expire_at = time.time() + expires_in
                return self._access_token
            except Exception as exc:
                last_exc = exc
                wait = 2 ** attempt
                time.sleep(wait)
        raise RuntimeError(f"KIS auth failed after {max_retries} retries: {last_exc}")

    def _hashkey(self, body: dict[str, Any]) -> str:
        url = f"{self.cfg.base_url}/uapi/hashkey"
        headers = {
            "content-type": "application/json; charset=utf-8",
            "appKey": self.cfg.app_key,
            "appSecret": self.cfg.app_secret,
        }
        resp = self.session.post(url, headers=headers, data=json.dumps(body), timeout=20)
        resp.raise_for_status()
        data = resp.json()
        hashkey = data.get("HASH") or data.get("hash")
        if not hashkey:
            raise RuntimeError(f"KIS hashkey not returned: {data}")
        return str(hashkey)

    def place_order(self, symbol: str, side: str, qty: int, reason: str, signal_id: str) -> dict[str, Any]:
        if self.cfg.simulate:
            return {
                "broker": "KIS",
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "reason": reason,
                "signal_id": signal_id,
                "status": "SIMULATED",
            }

        token = self.authenticate()
        order_body = {
            "CANO": self.cfg.cano,
            "ACNT_PRDT_CD": self.cfg.acnt_prdt_cd,
            "OVRS_EXCG_CD": self.cfg.exchange_code,
            "PDNO": symbol,
            "ORD_QTY": str(qty),
            "OVRS_ORD_UNPR": str(self.cfg.order_price),
            "ORD_SVR_DVSN_CD": "0",
            "ORD_DVSN": self.cfg.order_type,
        }
        tr_id = self.cfg.buy_tr_id if side.upper() == "BUY" else self.cfg.sell_tr_id
        headers = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "appKey": self.cfg.app_key,
            "appSecret": self.cfg.app_secret,
            "tr_id": tr_id,
            "custtype": "P",
            "hashkey": self._hashkey(order_body),
        }
        url = f"{self.cfg.base_url}/uapi/overseas-stock/v1/trading/order"
        resp = self.session.post(url, headers=headers, json=order_body, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        status = "SENT"
        rt_cd = str(data.get("rt_cd", ""))
        if rt_cd not in {"0", ""}:
            status = "REJECTED"
        return {
            "broker": "KIS",
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "reason": reason,
            "signal_id": signal_id,
            "status": status,
            "response": data,
        }
