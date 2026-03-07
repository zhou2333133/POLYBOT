from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional

from rich.console import Console, Group
from rich.live import Live
from rich.table import Table

from polybot.core.clob import (
    are_orders_scoring,
    cancel_order,
    create_client,
    ensure_api_creds,
    get_open_orders,
    get_usdc_balance,
    place_limit_order,
)
from polybot.core.config import RootConfig
from polybot.core.http import HttpClient
from polybot.core.loader import resolve_account_secrets
from polybot.core.markets import (
    MarketFetcher,
    filter_markets_with_reasons,
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
    open_orders: int
    open_exposure_usdc: float
    balance_usdc: Optional[float]
    est_daily_reward: float
    fills_detected: int


@dataclass
class MarketPlan:
    account: str
    token_id: str
    price: float
    size_shares: float
    size_usdc: float
    level: int
    daily_reward: float
    question: str
    end_date: str
    score: float
    book: Optional[dict]


def build_summary_table(stats: List[AccountStats]) -> Table:
    table = Table(title="POLYBOT 账户概览", header_style="bold")
    table.add_column("账户")
    table.add_column("余额(USDC)", justify="right")
    table.add_column("持仓占用(USDC)", justify="right")
    table.add_column("开放挂单", justify="right")
    table.add_column("计划挂单", justify="right")
    table.add_column("计分挂单", justify="right")
    table.add_column("疑似成交", justify="right")
    table.add_column("预估奖励/日", justify="right")
    table.add_column("奖励/可用/总", justify="right")

    for row in stats:
        balance = "-" if row.balance_usdc is None else f"{row.balance_usdc:.2f}"
        reward_text = f"{row.est_daily_reward:.3f}"
        counts = f"{row.markets_reward}/{row.markets_eligible}/{row.markets_total}"
        table.add_row(
            row.name,
            balance,
            f"{row.open_exposure_usdc:.2f}",
            str(row.open_orders),
            str(row.orders_planned),
            str(row.orders_scoring),
            str(row.fills_detected),
            reward_text,
            counts,
        )

    return table


def _short_question(question: str, limit: int = 36) -> str:
    if len(question) <= limit:
        return question
    return question[: limit - 1] + "…"


def build_plan_table(plans: List[MarketPlan], max_rows: int) -> Table:
    table = Table(title="POLYBOT 计划挂单（Top）", header_style="bold")
    table.add_column("账户")
    table.add_column("市场")
    table.add_column("到期", justify="right")
    table.add_column("价格", justify="right")
    table.add_column("挡位", justify="right")
    table.add_column("金额(USDC)", justify="right")
    table.add_column("预估奖励/日", justify="right")
    table.add_column("得分", justify="right")

    for plan in plans[: max_rows]:
        table.add_row(
            plan.account,
            _short_question(plan.question),
            plan.end_date,
            f"{plan.price:.4f}",
            f"L{plan.level}",
            f"{plan.size_usdc:.2f}",
            f"{plan.daily_reward:.3f}",
            f"{plan.score:.4f}",
        )
    return table


def build_filter_table(
    title: str,
    reasons: Counter,
    max_rows: int,
) -> Table:
    labels = {
        "not_accepting": "不接单",
        "closed": "已关闭",
        "archived": "已归档",
        "inactive": "未激活",
        "not_reward": "无奖励",
        "missing_daily_rate": "无奖励日池",
        "missing_end_date": "缺少到期",
        "invalid_end_date": "到期异常",
        "expiry_too_soon": "到期太近",
        "min_incentive_over_cap": "最低金额过高",
        "min_incentive_parse_error": "最低金额异常",
        "no_token_id": "无Token",
        "no_midpoint": "无中间价",
        "no_order_book": "无盘口",
        "spread_too_wide": "点差超限",
        "daily_rate_missing": "奖励日池缺失",
        "reward_efficiency_low": "奖励效率低",
        "no_candidate": "无合格挡位",
        "risk_too_high": "风险过高",
        "scan_limit": "超出扫描上限",
        "max_competition": "竞争过大",
    }
    table = Table(title=title, header_style="bold")
    table.add_column("原因")
    table.add_column("数量", justify="right")
    rows = reasons.most_common(max_rows)
    if not rows:
        table.add_row("无", "0")
        return table
    for key, count in rows:
        table.add_row(labels.get(key, key), str(count))
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
) -> List[tuple[float, int]]:
    if not book:
        return []
    levels = book.get("bids") if side.lower() == "buy" else book.get("asks")
    if not levels:
        return []
    start = max(0, min_level)
    end = start + max(1, depth)
    prices: List[tuple[float, int]] = []
    for idx in range(start, min(end, len(levels))):
        price = _extract_level_price(levels[idx])
        if price is not None:
            prices.append((price, idx + 1))
    return prices


def run_loop(cfg: RootConfig) -> None:
    console = Console()
    last_order_at: Dict[Tuple[str, str], float] = {}
    client_cache: Dict[str, object] = {}
    market_cache: Dict[str, List[dict]] = {}
    market_cache_time: Dict[str, float] = {}
    order_cache: Dict[str, set[str]] = {}
    cancel_cache: Dict[str, Dict[str, float]] = {}

    with Live(console=console, refresh_per_second=4) as live:
        while True:
            stats: List[AccountStats] = []
            all_plans: List[MarketPlan] = []
            filter_reasons_total: Counter = Counter()
            plan_reasons_total: Counter = Counter()

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

                cache_key = account.http_proxy or "default"
                now = time.time()
                cached_markets = market_cache.get(cache_key)
                cached_at = market_cache_time.get(cache_key, 0.0)
                if cached_markets and (now - cached_at) < cfg.app.market_refresh_seconds:
                    markets = cached_markets
                else:
                    try:
                        max_needed = cfg.app.max_markets_to_scan or None
                        markets = fetcher.fetch_markets(max_needed=max_needed)
                        if markets:
                            market_cache[cache_key] = markets
                            market_cache_time[cache_key] = now
                    except Exception:
                        markets = cached_markets or []
                reward_markets = [m for m in markets if is_reward_market(m)]
                eligible, filter_reasons = filter_markets_with_reasons(
                    markets, cfg.app, cfg.strategy
                )
                filter_reasons_total.update(filter_reasons)
                eligible_count = len(eligible)
                if cfg.app.max_markets_to_scan > 0 and len(eligible) > cfg.app.max_markets_to_scan:
                    scored = []
                    for market in eligible:
                        daily_rate = get_rewards_daily_rate(market) or 0.0
                        scored.append((daily_rate, market))
                    scored.sort(key=lambda item: item[0], reverse=True)
                    eligible = [item[1] for item in scored[: cfg.app.max_markets_to_scan]]

                prefiltered: List[tuple[float, dict]] = []
                for market in eligible:
                    token_id = select_token_id(market)
                    if not token_id:
                        plan_reasons_total["no_token_id"] += 1
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
                            plan_reasons_total["spread_too_wide"] += 1
                            continue
                    spread_hint = market.get("spread")
                    if spread_hint is not None and max_spread_val:
                        try:
                            if float(spread_hint) > max_spread_val:
                                plan_reasons_total["spread_too_wide"] += 1
                                continue
                        except (TypeError, ValueError):
                            pass
                    min_incentive = get_reward_field(market, cfg.strategy.min_incentive_size_key)
                    try:
                        min_incentive_val = float(min_incentive) if min_incentive is not None else None
                    except (TypeError, ValueError):
                        min_incentive_val = None
                    size_usdc = min_incentive_val or cfg.app.max_order_usdc
                    size_usdc = min(size_usdc, cfg.app.max_order_usdc)
                    if size_usdc <= 0:
                        plan_reasons_total["reward_efficiency_low"] += 1
                        continue
                    daily_rate = get_rewards_daily_rate(market)
                    if cfg.app.require_rewards_daily_rate and (daily_rate is None or daily_rate <= 0):
                        plan_reasons_total["daily_rate_missing"] += 1
                        continue
                    if not daily_rate:
                        plan_reasons_total["daily_rate_missing"] += 1
                        continue
                    liquidity_hint = market.get("liquidityClob") or market.get("liquidityClobNum")
                    if liquidity_hint is None:
                        liquidity_hint = market.get("liquidity") or market.get("liquidityNum")
                    try:
                        liquidity_val = float(liquidity_hint) if liquidity_hint is not None else 0.0
                    except (TypeError, ValueError):
                        liquidity_val = 0.0
                    approx_score = daily_rate / (liquidity_val + size_usdc)
                    prefiltered.append((approx_score, market))

                prefiltered.sort(key=lambda item: item[0], reverse=True)
                if cfg.app.max_orderbook_requests > 0:
                    eligible = [item[1] for item in prefiltered[: cfg.app.max_orderbook_requests]]
                    skipped = max(0, len(prefiltered) - len(eligible))
                    if skipped:
                        plan_reasons_total["scan_limit"] += skipped
                else:
                    eligible = [item[1] for item in prefiltered]

                open_orders = []
                open_orders_ok = True
                try:
                    open_orders = get_open_orders(client)
                except Exception:
                    open_orders = []
                    open_orders_ok = False

                orders_by_token: Dict[str, List[dict]] = {}
                exposure_usdc = 0.0
                for order in open_orders or []:
                    token_id = _extract_token_id(order)
                    if not token_id:
                        continue
                    orders_by_token.setdefault(token_id, []).append(order)
                    exposure_usdc += _extract_price(order) * _extract_size(order)

                prev_open = order_cache.get(account.name, set())
                current_open = {o.get("id") for o in open_orders if o.get("id")}
                current_open = {o for o in current_open if o}
                fills_detected = 0
                if open_orders_ok and prev_open:
                    cancels = cancel_cache.get(account.name, {})
                    cutoff = time.time() - cfg.app.cancel_cache_seconds
                    cancels = {oid: ts for oid, ts in cancels.items() if ts >= cutoff}
                    cancel_cache[account.name] = cancels
                    disappeared = prev_open - current_open - set(cancels.keys())
                    fills_detected = len(disappeared)
                order_cache[account.name] = current_open

                balance_usdc = get_usdc_balance(client)
                est_daily_reward = 0.0

                if exposure_usdc >= cfg.app.max_open_exposure_usdc:
                    stats.append(
                        AccountStats(
                            name=account.name,
                            markets_total=len(markets),
                            markets_reward=len(reward_markets),
                            markets_eligible=eligible_count,
                            orders_planned=0,
                            orders_placed=0,
                            orders_scoring=0,
                            open_orders=len(open_orders),
                            open_exposure_usdc=exposure_usdc,
                            balance_usdc=balance_usdc,
                            est_daily_reward=est_daily_reward,
                            fills_detected=fills_detected,
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
                        plan_reasons_total["no_token_id"] += 1
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
                        plan_reasons_total["no_midpoint"] += 1
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
                            plan_reasons_total["spread_too_wide"] += 1
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
                        plan_reasons_total["no_order_book"] += 1
                        continue

                    if cfg.strategy.require_spread_within_reward:
                        current_spread = _spread_from_book(book)
                        if current_spread is None or current_spread > max_spread_val:
                            plan_reasons_total["spread_too_wide"] += 1
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
                        plan_reasons_total["daily_rate_missing"] += 1
                        continue
                    if not daily_rate:
                        plan_reasons_total["daily_rate_missing"] += 1
                        continue

                    max_delta = max_spread_val / 2.0 if max_spread_val else None
                    if max_delta is None or max_delta <= 0:
                        plan_reasons_total["spread_too_wide"] += 1
                        continue
                    eligible_liquidity = _eligible_liquidity_usdc(
                        book, cfg.strategy.side, midpoint, max_delta
                    )
                    reward_efficiency = daily_rate / (eligible_liquidity + size_usdc)
                    if reward_efficiency <= 0:
                        plan_reasons_total["reward_efficiency_low"] += 1
                        continue

                    if cfg.strategy.auto_level_selection:
                        candidate_prices = _candidate_prices(
                            book,
                            cfg.strategy.side,
                            cfg.strategy.auto_level_min,
                            cfg.strategy.auto_level_depth,
                        )
                    else:
                        fallback = compute_order_price(midpoint, cfg.app, cfg.strategy)
                        candidate_prices = [(fallback, 0)]

                    best_price = None
                    best_score = 0.0
                    best_level = 0
                    best_daily_reward = 0.0
                    risk_weight = max(0.0, min(cfg.strategy.fill_risk_weight, 1.0))
                    max_dev = midpoint * (cfg.strategy.max_midpoint_deviation_bps / 10000.0)
                    risk_blocked = False
                    for candidate, level in candidate_prices:
                        price = apply_tick_size(candidate, tick_size_val) if tick_size_val else candidate
                        if price <= 0:
                            continue
                        if price < cfg.app.min_price or price > cfg.app.max_price:
                            continue
                        if max_dev > 0 and abs(price - midpoint) > max_dev:
                            risk_blocked = True
                            continue
                        if abs(price - midpoint) > max_delta:
                            continue
                        if cfg.strategy.respect_max_incentive_spread:
                            if cfg.strategy.side.lower() == "buy":
                                if price < (midpoint - max_delta):
                                    continue
                            else:
                                if price > (midpoint + max_delta):
                                    continue
                        distance = abs(price - midpoint)
                        risk = 1.0 - min(distance / max_delta, 1.0)
                        score = reward_efficiency * (1.0 - risk_weight * risk)
                        if score > best_score:
                            best_score = score
                            best_price = price
                            best_level = level
                            best_daily_reward = reward_efficiency * size_usdc

                    if not best_price or best_score <= 0:
                        if risk_blocked:
                            plan_reasons_total["risk_too_high"] += 1
                        else:
                            plan_reasons_total["no_candidate"] += 1
                        continue
                    size_shares = size_usdc / best_price
                    plans.append(
                        MarketPlan(
                            account=account.name,
                            token_id=token_id,
                            price=best_price,
                            size_shares=size_shares,
                            size_usdc=size_usdc,
                            level=best_level,
                            daily_reward=best_daily_reward,
                            question=str(market.get("question") or market.get("title") or "未知市场"),
                            end_date=str(
                                (market.get("endDateIso") or market.get("endDate") or "")[:10]
                            ),
                            score=best_score,
                            book=book,
                        )
                    )
                plans.sort(key=lambda item: item.score, reverse=True)
                plans = plans[: cfg.app.max_markets_per_account]
                orders_planned = len(plans)
                est_daily_reward = sum(plan.daily_reward for plan in plans)
                all_plans.extend(plans)

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
                            cancel_cache.setdefault(account.name, {})[order_id] = time.time()

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
                        markets_eligible=eligible_count,
                        orders_planned=orders_planned,
                        orders_placed=orders_placed,
                        orders_scoring=orders_scoring,
                        open_orders=len(open_orders),
                        open_exposure_usdc=exposure_usdc,
                        balance_usdc=balance_usdc,
                        est_daily_reward=est_daily_reward,
                        fills_detected=fills_detected,
                    )
                )

            all_plans.sort(key=lambda item: item.score, reverse=True)
            summary = build_summary_table(stats)
            plan_table = build_plan_table(all_plans, cfg.app.max_plan_rows)
            filter_table = build_filter_table(
                "过滤统计（本轮）", filter_reasons_total, cfg.app.max_filter_rows
            )
            plan_filter_table = build_filter_table(
                "挂单筛选统计（本轮）", plan_reasons_total, cfg.app.max_filter_rows
            )
            live.update(Group(summary, plan_table, filter_table, plan_filter_table))
            time.sleep(cfg.app.refresh_seconds)
