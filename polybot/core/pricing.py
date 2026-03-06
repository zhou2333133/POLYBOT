from __future__ import annotations

from typing import Optional

from polybot.core.http import HttpClient


class PricingClient:
    def __init__(self, http: HttpClient):
        self.http = http

    def get_midpoint(self, token_id: str) -> Optional[float]:
        url = "https://data-api.polymarket.com/midpoint"
        data = self.http.get(url, params={"token_id": token_id})
        mid = data.get("midpoint") or data.get("mid")
        try:
            return float(mid)
        except (TypeError, ValueError):
            return None

    def get_spread(self, token_id: str) -> Optional[float]:
        url = "https://clob.polymarket.com/spread"
        data = self.http.get(url, params={"token_id": token_id})
        spread = data.get("spread")
        try:
            return float(spread)
        except (TypeError, ValueError):
            return None

    def get_order_book(self, token_id: str) -> Optional[dict]:
        url = "https://clob.polymarket.com/book"
        try:
            return self.http.get(url, params={"token_id": token_id})
        except Exception:
            return None
