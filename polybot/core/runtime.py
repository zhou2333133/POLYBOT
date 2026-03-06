from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional

from rich.console import Console
from rich.live import Live
from rich.table import Table

from polybot.core.clob import (
    are_orders_scoring,
    cancel_order,
    create_client,
    ensure_api_creds,
    get_open_orders,
    place_limit_order,
)
from polybot.core.config import RootConfig
from polybot.core.http import HttpClient
from polybot.core.loader import resolve_account_secrets
from polybot.core.markets import (
    MarketFetcher,
    filter_markets,
    get_reward_field,
    get_rewards_daily_rate,
    is_reward_market,
    select_token_id,
)
from polybot.core.pricing import PricingClient
from polybot.core.strategy import apply_tick_size, compute_order_price, within_replace_threshold


@dataclass
class AccountStats:
    name: str
    markets_total: int
    markets_reward: int
    markets_eligible: int
    orders_planned: int
    orders_placed: int
    orders_scoring: int


@dataclass
class MarketPlan:
    token_id: str
    price: float
    size_shares: float
    score: float
    book: Optional[dict]


def build_table(stats: List[AccountStats]) -> Table:
    table = Table(title="POLYBOT 实时面板", header_style="bold")
    table.add_column("账户")
    table.add_column("市场总数", justify="right")
    table.add_column("奖励市场", justify="right")
    table.add_column("可用市场", justify="right")
    table.add_column("计划挂单", justify="right")
    table.add_column("已下挂单", justify="right")
    table.add_column("计分挂单", justify="right")

    for row in stats:
        table.add_row(
            row.name,
            str(row.markets_total),
            str(row.markets_reward),
            str(row.markets_eligible),
            str(row.orders_planned),
            str(row.orders_placed),
            str(row.orders_scoring),
        )

    return table


def _extract_price(order: dict) -> float:
    for key in ("price", "price_per_share"):
        if key in order:
            try:
                return float(order[key])
            except (TypeError, ValueError):
                continue
    return 0.0


def _extract_size(order: dict) -> float:
    for key in ("size", "original_size", "remaining_size"):
        if key in order:
            try:
                return float(order[key])
            except (TypeError, ValueError):
                continue
    return 0.0


def _extract_token_id(order: dict) -> str:
    for key in ("token_id", "tokenId", "asset_id", "assetId"):
        if key in order and order[key]:
            return str(order[key])
    return ""


def _extract_level_price(level: object) -> float | None:
    if isinstance(level, dict):
        value = level.get("price")
    elif isinstance(level, list) and len(level) >= 2:
        value = level[0]
    else:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_level_size(level: object) -> float | None:
    if isinstance(level, dict):
        value = level.get("size")
    elif isinstance(level, list) and len(level) >= 2:
        value = level[1]
    else:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _spread_from_book(book: dict | None) -> Optional[float]:
    if not book:
        return None
    best_bid = _extract_level_price((book.get("bids") or [None])[0])
    best_ask = _extract_level_price((book.get("asks") or [None])[0])
    if best_bid is None or best_ask is None:
        return None
    return best_ask - best_bid


def _eligible_liquidity_usdc(
    book: dict | None,
    side: str,
    midpoint: float,
    max_delta: float,
) -> float:
    if not book:
        return 0.0
    levels = book.get("bids") if side.lower() == "buy" else book.get("asks")
    total = 0.0
    if not levels:
        return total
    for level in levels:
        price = _extract_level_price(level)
        size = _extract_level_size(level)
        if price is None or size is None:
            continue
        if abs(price - midpoint) <= max_delta:
            total += price * size
    return total


def _candidate_prices(
    book: dict | None,
    side: str,
    min_level: int,
    depth: int,
) -> List[float]:
    if not book:
        return []
    levels = book.get("bids") if side.lower() == "buy" else book.get("asks")
    if not levels:
        return []
    start = max(0, min_level)
    end = start + max(1, depth)
    prices: List[float] = []
    for idx in range(start, min(end, len(levels))):
        price = _extract_level_price(levels[idx])
        if price is not None:
            prices.append(price)
    return prices


def run_loop(cfg: RootConfig) -> None:
    console = Console()
    last_order_at: Dict[Tuple[str, str], float] = {}
    client_cache: Dict[str, object] = {}

    with Live(console=console, refresh_per_second=4) as live:
        while True:
            stats: List[AccountStats] = []

            for account in cfg.accounts:
                resolved = resolve_account_secrets(account.model_dump())
                http = HttpClient(
                    http_proxy=account.http_proxy,
                    timeout=cfg.app.http_timeout_seconds,
                )
                fetcher = MarketFetcher(http)
                pricing = PricingClient(http)

                client = client_cache.get(account.name)
                if client is None:
                    client = create_client(account, resolved["private_key"])
                    ensure_api_creds(client)
                    client_cache[account.name] = client

                markets = fetcher.fetch_markets()
                reward_markets = [m for m in markets if is_reward_market(m)]
                eligible = filter_markets(markets, cfg.app, cfg.strategy)
                if cfg.app.max_markets_to_scan > 0 and len(eligible) > cfg.app.max_markets_to_scan:
                    scored = []
                    for market in eligible:
                        daily_rate = get_rewards_daily_rate(market) or 0.0
                        scored.append((daily_rate, market))
                    scored.sort(key=lambda item: item[0], reverse=True)
                    eligible = [item[1] for item in scored[: cfg.app.max_markets_to_scan]]

                open_orders = []
                try:
                    open_orders = get_open_orders(client)
                except Exception:
                    open_orders = []

                orders_by_token: Dict[str, List[dict]] = {}
                exposure_usdc = 0.0
                for order in open_orders or []:
                    token_id = _extract_token_id(order)
                    if not token_id:
                        continue
                    orders_by_token.setdefault(token_id, []).append(order)
                    exposure_usdc += _extract_price(order) * _extract_size(order)

                if exposure_usdc >= cfg.app.max_open_exposure_usdc:
                    stats.append(
                        AccountStats(
                            name=account.name,
                            markets_total=len(markets),
                            markets_reward=len(reward_markets),
                            markets_eligible=len(eligible),
                            orders_planned=0,
                            orders_placed=0,
                            orders_scoring=0,
                        )
                    )
                    continue

                orders_planned = 0
                orders_placed = 0
                orders_scoring = 0

                scoring_map: Dict[str, bool] = {}
                if cfg.strategy.check_scoring:
                    order_ids = [o.get("id") for o in open_orders if o.get("id")]
                    try:
                        scoring_map = are_orders_scoring(client, order_ids)
                    except Exception:
                        scoring_map = {}

                plans: List[MarketPlan] = []
                for market in eligible:
                    token_id = select_token_id(market)
                    if not token_id:
                        continue
                    midpoint = None
                    best_bid = market.get("bestBid") or market.get("best_bid")
                    best_ask = market.get("bestAsk") or market.get("best_ask")
                    try:
                        if best_bid is not None and best_ask is not None:
                            midpoint = (float(best_bid) + float(best_ask)) / 2.0
                    except (TypeError, ValueError):
                        midpoint = None
                    if midpoint is None:
                        midpoint = pricing.get_midpoint(token_id)
                    if midpoint is None:
                        continue

                    max_spread = get_reward_field(market, cfg.strategy.max_incentive_spread_key)
                    try:
                        max_spread_val = float(max_spread) if max_spread is not None else None
                    except (TypeError, ValueError):
                        max_spread_val = None
                    if max_spread_val is not None and max_spread_val > 1.0:
                        max_spread_val = max_spread_val / 100.0
                    if cfg.strategy.require_spread_within_reward:
                        if not max_spread_val or max_spread_val <= 0:
                            continue

                    tick_size = (
                        market.get("orderPriceMinTickSize")
                        or market.get("minimum_tick_size")
                        or market.get("tick_size")
                        or market.get("tickSize")
                    )
                    if not tick_size:
                        tick_size = pricing.get_tick_size(token_id)
                    try:
                        tick_size_val = float(tick_size) if tick_size else None
                    except (TypeError, ValueError):
                        tick_size_val = None

                    book = pricing.get_order_book(token_id)
                    if not book:
                        continue

                    if cfg.strategy.require_spread_within_reward:
                        current_spread = _spread_from_book(book)
                        if current_spread is None or current_spread > max_spread_val:
                            continue

                    min_incentive = get_reward_field(market, cfg.strategy.min_incentive_size_key)
                    try:
                        min_incentive_val = float(min_incentive) if min_incentive is not None else None
                    except (TypeError, ValueError):
                        min_incentive_val = None

                    size_usdc = min_incentive_val or cfg.app.max_order_usdc
                    size_usdc = min(size_usdc, cfg.app.max_order_usdc)
                    if size_usdc <= 0:
                        continue

                    daily_rate = get_rewards_daily_rate(market)
                    if cfg.app.require_rewards_daily_rate and (daily_rate is None or daily_rate <= 0):
                        continue
                    if not daily_rate:
                        continue

                    max_delta = max_spread_val / 2.0 if max_spread_val else None
                    if max_delta is None or max_delta <= 0:
                        continue
                    eligible_liquidity = _eligible_liquidity_usdc(
                        book, cfg.strategy.side, midpoint, max_delta
                    )
                    reward_efficiency = daily_rate / (eligible_liquidity + size_usdc)
                    if reward_efficiency <= 0:
                        continue

                    if cfg.strategy.auto_level_selection:
                        candidate_prices = _candidate_prices(
                            book,
                            cfg.strategy.side,
                            cfg.strategy.auto_level_min,
                            cfg.strategy.auto_level_depth,
                        )
                    else:
                        candidate_prices = [compute_order_price(midpoint, cfg.app, cfg.strategy)]

                    best_price = None
                    best_score = 0.0
                    risk_weight = max(0.0, min(cfg.strategy.fill_risk_weight, 1.0))
                    for candidate in candidate_prices:
                        if tick_size_val:
                            candidate = apply_tick_size(candidate, tick_size_val)
                        if candidate <= 0:
                            continue
                        if candidate < cfg.app.min_price or candidate > cfg.app.max_price:
                            continue
                        if abs(candidate - midpoint) > max_delta:
                            continue
                        if cfg.strategy.respect_max_incentive_spread:
                            if cfg.strategy.side.lower() == "buy":
                                if candidate < (midpoint - max_delta):
                                    continue
                            else:
                                if candidate > (midpoint + max_delta):
                                    continue
                        distance = abs(candidate - midpoint)
                        risk = 1.0 - min(distance / max_delta, 1.0)
                        score = reward_efficiency * (1.0 - risk_weight * risk)
                        if score > best_score:
                            best_score = score
                            best_price = candidate

                    if not best_price or best_score <= 0:
                        continue
                    size_shares = size_usdc / best_price
                    plans.append(
                        MarketPlan(
                            token_id=token_id,
                            price=best_price,
                            size_shares=size_shares,
                            score=best_score,
                            book=book,
                        )
                    )

                plans.sort(key=lambda item: item.score, reverse=True)
                plans = plans[: cfg.app.max_markets_per_account]
                orders_planned = len(plans)

                for plan in plans:
                    token_id = plan.token_id
                    price = plan.price
                    size_shares = plan.size_shares

                    existing = orders_by_token.get(token_id, [])
                    best_match = existing[0] if existing else None
                    if best_match:
                        current_price = _extract_price(best_match)
                        order_id = best_match.get("id")
                        is_scoring = scoring_map.get(order_id, True)
                        if within_replace_threshold(
                            current_price, price, cfg.strategy.cancel_replace_threshold_bps
                        ) and is_scoring:
                            orders_scoring += 1 if is_scoring else 0
                            continue

                        last_time = last_order_at.get((account.name, str(token_id)), 0.0)
                        if time.time() - last_time < cfg.app.order_refresh_seconds:
                            continue
                        if not cfg.app.dry_run and order_id:
                            cancel_order(client, order_id)

                    if cfg.strategy.max_competition_size is not None:
                        book = plan.book or pricing.get_order_book(token_id) or {}
                        side_key = "bids" if cfg.strategy.side.lower() == "buy" else "asks"
                        top = (book.get(side_key) or [None])[0]
                        top_size = 0.0
                        if isinstance(top, dict):
                            top_size = float(top.get("size") or 0)
                        elif isinstance(top, list) and len(top) >= 2:
                            top_size = float(top[1])
                        if top_size >= cfg.strategy.max_competition_size:
                            continue

                    key = (account.name, token_id)
                    last_time = last_order_at.get(key, 0.0)
                    if time.time() - last_time < cfg.app.order_refresh_seconds:
                        continue

                    if not cfg.app.dry_run:
                        place_limit_order(
                            client,
                            cfg.strategy,
                            token_id,
                            price,
                            size_shares,
                        )
                    orders_placed += 1
                    last_order_at[key] = time.time()

                stats.append(
                    AccountStats(
                        name=account.name,
                        markets_total=len(markets),
                        markets_reward=len(reward_markets),
                        markets_eligible=len(eligible),
                        orders_planned=orders_planned,
                        orders_placed=orders_placed,
                        orders_scoring=orders_scoring,
                    )
                )

            live.update(build_table(stats))
            time.sleep(cfg.app.refresh_seconds)
