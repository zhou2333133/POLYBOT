from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, List, Tuple

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


def _select_non_top_price(
    book: dict | None,
    side: str,
    tick_size: float | None,
    skip_levels: int,
) -> float | None:
    if not book:
        return None
    levels = book.get("bids") if side.lower() == "buy" else book.get("asks")
    if not levels:
        return None
    index = max(0, skip_levels)
    if len(levels) > index:
        return _extract_level_price(levels[index])
    best = _extract_level_price(levels[0])
    if best is None or not tick_size:
        return None
    return best - tick_size if side.lower() == "buy" else best + tick_size


def run_loop(cfg: RootConfig) -> None:
    console = Console()
    last_order_at: Dict[Tuple[str, str], float] = {}
    client_cache: Dict[str, object] = {}

    with Live(console=console, refresh_per_second=4) as live:
        while True:
            stats: List[AccountStats] = []

            for account in cfg.accounts:
                resolved = resolve_account_secrets(account.model_dump())
                http = HttpClient(http_proxy=account.http_proxy)
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
                eligible = eligible[: cfg.app.max_markets_per_account]

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

                for market in eligible:
                    token_id = select_token_id(market)
                    if not token_id:
                        continue
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

                    book = None
                    if cfg.strategy.avoid_top_of_book or cfg.strategy.max_competition_size is not None:
                        book = pricing.get_order_book(token_id)

                    if cfg.strategy.require_spread_within_reward and max_spread_val:
                        current_spread = pricing.get_spread(token_id)
                        if current_spread is None or current_spread > max_spread_val:
                            continue

                    price = compute_order_price(midpoint, cfg.app, cfg.strategy)
                    if cfg.strategy.respect_max_incentive_spread and max_spread_val:
                        max_delta = max_spread_val / 2.0
                        if cfg.strategy.side.lower() == "buy":
                            price = max(price, midpoint - max_delta)
                        else:
                            price = min(price, midpoint + max_delta)
                    price = apply_tick_size(price, tick_size_val)

                    if cfg.strategy.avoid_top_of_book:
                        candidate = _select_non_top_price(
                            book,
                            cfg.strategy.side,
                            tick_size_val,
                            cfg.strategy.avoid_top_levels,
                        )
                        if candidate is None:
                            continue
                        if cfg.strategy.respect_max_incentive_spread and max_spread_val:
                            max_delta = max_spread_val / 2.0
                            if cfg.strategy.side.lower() == "buy":
                                if candidate < (midpoint - max_delta):
                                    continue
                            else:
                                if candidate > (midpoint + max_delta):
                                    continue
                        price = max(cfg.app.min_price, min(cfg.app.max_price, candidate))

                    min_incentive = get_reward_field(market, cfg.strategy.min_incentive_size_key)
                    try:
                        min_incentive_val = float(min_incentive) if min_incentive is not None else None
                    except (TypeError, ValueError):
                        min_incentive_val = None

                    size_usdc = min_incentive_val or cfg.app.max_order_usdc
                    size_usdc = min(size_usdc, cfg.app.max_order_usdc)
                    if price <= 0:
                        continue
                    size_shares = size_usdc / price

                    orders_planned += 1

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
                        book = book or pricing.get_order_book(token_id) or {}
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
