from __future__ import annotations

import json
from typing import List, Dict, Any

from polybot.core.http import HttpClient
from polybot.core.config import AppConfig, StrategyConfig


class MarketFetcher:
    def __init__(self, http: HttpClient):
        self.http = http

    def fetch_markets(self, max_pages: int = 8, page_size: int = 500) -> List[Dict[str, Any]]:
        # Use Gamma API for active markets metadata.
        url = "https://gamma-api.polymarket.com/markets"
        markets: List[Dict[str, Any]] = []
        offset = 0
        for _ in range(max_pages):
            params = {
                "active": "true",
                "closed": "false",
                "archived": "false",
                "limit": page_size,
                "offset": offset,
            }
            data = self.http.get(url, params=params)
            if isinstance(data, dict):
                items = data.get("value") or data.get("data") or data.get("markets") or []
                if isinstance(items, list):
                    markets.extend(items)
                if not items:
                    break
                offset += len(items)
                if len(items) < page_size:
                    break
            elif isinstance(data, list):
                markets.extend(data)
                break
        return markets


def get_reward_field(market: Dict[str, Any], key: str) -> Any:
    if key in market:
        return market.get(key)
    if key == "min_incentive_size":
        for candidate in ("rewardsMinSize", "rewards_min_size"):
            if candidate in market:
                return market.get(candidate)
    if key == "max_incentive_spread":
        for candidate in ("rewardsMaxSpread", "rewards_max_spread"):
            if candidate in market:
                return market.get(candidate)
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
    clob_ids = market.get("clobTokenIds") or market.get("clob_token_ids")
    if isinstance(clob_ids, str):
        try:
            clob_ids = json.loads(clob_ids)
        except json.JSONDecodeError:
            clob_ids = None
    if isinstance(clob_ids, list) and clob_ids:
        return str(clob_ids[0])
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
        if market.get("acceptingOrders") is False:
            continue
        if market.get("accepting_orders") is False:
            continue
        if market.get("closed") is True:
            continue
        if market.get("archived") is True:
            continue
        if market.get("active") is False:
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
