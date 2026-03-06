from __future__ import annotations

from typing import List, Dict, Any

from polybot.core.http import HttpClient
from polybot.core.config import AppConfig, StrategyConfig


class MarketFetcher:
    def __init__(self, http: HttpClient):
        self.http = http

    def fetch_markets(self, max_pages: int = 3) -> List[Dict[str, Any]]:
        # Use simplified markets endpoint with cursor pagination.
        url = "https://clob.polymarket.com/simplified-markets"
        markets: List[Dict[str, Any]] = []
        next_cursor = None
        for _ in range(max_pages):
            params = {}
            if next_cursor:
                params["next_cursor"] = next_cursor
            data = self.http.get(url, params=params)
            if isinstance(data, dict):
                items = data.get("data") or data.get("markets") or []
                if isinstance(items, list):
                    markets.extend(items)
                next_cursor = data.get("next_cursor")
            elif isinstance(data, list):
                markets.extend(data)
                break
            if not next_cursor:
                break
        return markets


def get_reward_field(market: Dict[str, Any], key: str) -> Any:
    if key in market:
        return market.get(key)
    rewards = market.get("rewards") or {}
    if key == "min_incentive_size":
        return rewards.get("min_size")
    if key == "max_incentive_spread":
        return rewards.get("max_spread")
    return rewards.get(key)


def select_token_id(market: Dict[str, Any], preferred_outcome: str | None = None) -> str:
    token_id = market.get("token_id") or market.get("tokenId")
    if token_id:
        return str(token_id)
    tokens = market.get("tokens") or []
    if isinstance(tokens, list):
        if preferred_outcome:
            for token in tokens:
                if str(token.get("outcome", "")).upper() == preferred_outcome.upper():
                    if token.get("token_id"):
                        return str(token["token_id"])
        for token in tokens:
            if token.get("token_id"):
                return str(token["token_id"])
    return ""


def filter_markets(
    markets: List[Dict[str, Any]],
    app: AppConfig,
    strategy: StrategyConfig,
) -> List[Dict[str, Any]]:
    filtered: List[Dict[str, Any]] = []
    min_key = strategy.min_incentive_size_key

    for market in markets:
        if market.get("accepting_orders") is False:
            continue
        if market.get("closed") is True:
            continue
        if market.get("archived") is True:
            continue
        min_incentive = get_reward_field(market, min_key)
        if app.enforce_incentive_cap and min_incentive is not None:
            try:
                if float(min_incentive) > app.max_order_usdc:
                    continue
            except (TypeError, ValueError):
                continue

        filtered.append(market)

    return filtered
