from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, List, Tuple

from rich.console import Console
from rich.live import Live
from rich.table import Table

from polybot.core.clob import create_client, ensure_api_creds, place_limit_order
from polybot.core.config import RootConfig
from polybot.core.http import HttpClient
from polybot.core.loader import resolve_account_secrets
from polybot.core.markets import MarketFetcher, filter_markets
from polybot.core.pricing import PricingClient
from polybot.core.strategy import apply_tick_size, compute_order_price


@dataclass
class AccountStats:
    name: str
    markets_total: int
    markets_eligible: int
    orders_planned: int
    orders_placed: int


def build_table(stats: List[AccountStats]) -> Table:
    table = Table(title="POLYBOT Live", header_style="bold")
    table.add_column("Account")
    table.add_column("Markets Total", justify="right")
    table.add_column("Eligible", justify="right")
    table.add_column("Orders Planned", justify="right")
    table.add_column("Orders Placed", justify="right")

    for row in stats:
        table.add_row(
            row.name,
            str(row.markets_total),
            str(row.markets_eligible),
            str(row.orders_planned),
            str(row.orders_placed),
        )

    return table


def run_loop(cfg: RootConfig) -> None:
    console = Console()
    last_order_at: Dict[Tuple[str, str], float] = {}

    with Live(console=console, refresh_per_second=4) as live:
        while True:
            stats: List[AccountStats] = []

            for account in cfg.accounts:
                resolved = resolve_account_secrets(account.model_dump())
                http = HttpClient(http_proxy=account.http_proxy)
                fetcher = MarketFetcher(http)
                pricing = PricingClient(http)

                markets = fetcher.fetch_markets()
                eligible = filter_markets(markets, cfg.app, cfg.strategy)
                eligible = eligible[: cfg.app.max_markets_per_account]

                orders_planned = 0
                orders_placed = 0
                client = create_client(account, resolved["private_key"])
                ensure_api_creds(client)

                for market in eligible:
                    token_id = market.get("token_id") or market.get("tokenId")
                    if not token_id:
                        continue
                    midpoint = pricing.get_midpoint(str(token_id))
                    if midpoint is None:
                        continue

                    tick_size = market.get("tick_size") or market.get("tickSize")
                    price = compute_order_price(midpoint, cfg.app, cfg.strategy)
                    try:
                        price = apply_tick_size(price, float(tick_size) if tick_size else None)
                    except (TypeError, ValueError):
                        pass

                    min_incentive = market.get(cfg.strategy.min_incentive_size_key)
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

                    key = (account.name, str(token_id))
                    last_time = last_order_at.get(key, 0.0)
                    if time.time() - last_time < cfg.app.order_refresh_seconds:
                        continue

                    if not cfg.app.dry_run:
                        place_limit_order(
                            client,
                            cfg.strategy,
                            str(token_id),
                            price,
                            size_shares,
                        )
                    orders_placed += 1
                    last_order_at[key] = time.time()

                stats.append(
                    AccountStats(
                        name=account.name,
                        markets_total=len(markets),
                        markets_eligible=len(eligible),
                        orders_planned=orders_planned,
                        orders_placed=orders_placed,
                    )
                )

            live.update(build_table(stats))
            time.sleep(cfg.app.refresh_seconds)
