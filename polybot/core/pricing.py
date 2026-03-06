from __future__ import annotations

from typing import Optional

from polybot.core.http import HttpClient


class PricingClient:
    def __init__(self, http: HttpClient):
        self.http = http

    def get_midpoint(self, token_id: str) -> Optional[float]:
        url = "https://data-api.polymarket.com/midpoint"
        try:
            data = self.http.get(url, params={"token_id": token_id})
            mid = data.get("midpoint") or data.get("mid")
            return float(mid)
        except Exception:
            pass

        book = self.get_order_book(token_id)
        if not book:
            return None
        best_bid = self._best_price(book.get("bids"))
        best_ask = self._best_price(book.get("asks"))
        if best_bid is None or best_ask is None:
            return None
        return (best_bid + best_ask) / 2.0

    @staticmethod
    def _best_price(levels: Optional[list]) -> Optional[float]:
        if not levels:
            return None
        top = levels[0]
        if isinstance(top, dict):
            value = top.get("price")
        elif isinstance(top, list) and len(top) >= 2:
            value = top[0]
        else:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def get_spread(self, token_id: str) -> Optional[float]:
        url = "https://clob.polymarket.com/spread"
        try:
            data = self.http.get(url, params={"token_id": token_id})
            spread = data.get("spread")
            return float(spread)
        except Exception:
            pass
        book = self.get_order_book(token_id)
        if not book:
            return None
        best_bid = self._best_price(book.get("bids"))
        best_ask = self._best_price(book.get("asks"))
        if best_bid is None or best_ask is None:
            return None
        return best_ask - best_bid

    def get_order_book(self, token_id: str) -> Optional[dict]:
        url = "https://clob.polymarket.com/book"
        try:
            return self.http.get(url, params={"token_id": token_id})
        except Exception:
            return None

    def get_tick_size(self, token_id: str) -> Optional[float]:
        url = "https://clob.polymarket.com/tick-size"
        try:
            data = self.http.get(url, params={"token_id": token_id})
        except Exception:
            return None
        tick = data.get("tick_size") or data.get("tickSize")
        try:
            return float(tick)
        except (TypeError, ValueError):
            return None
