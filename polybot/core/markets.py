from __future__ import annotations

from typing import List, Dict, Any

from polybot.core.http import HttpClient
from polybot.core.config import AppConfig, StrategyConfig


class MarketFetcher:
    def __init__(self, http: HttpClient):
        self.http = http

    def fetch_markets(self) -> List[Dict[str, Any]]:
        # Primary: CLOB markets endpoint
        url = "https://clob.polymarket.com/markets"
        data = self.http.get(url)
        if isinstance(data, dict) and "markets" in data:
            return data["markets"]
        if isinstance(data, list):
            return data
        return []


def filter_markets(
    markets: List[Dict[str, Any]],
    app: AppConfig,
    strategy: StrategyConfig,
) -> List[Dict[str, Any]]:
    filtered: List[Dict[str, Any]] = []
    min_key = strategy.min_incentive_size_key

    for market in markets:
        min_incentive = market.get(min_key)
        if app.enforce_incentive_cap and min_incentive is not None:
            try:
                if float(min_incentive) > app.max_order_usdc:
                    continue
            except (TypeError, ValueError):
                continue

        filtered.append(market)

    return filtered
