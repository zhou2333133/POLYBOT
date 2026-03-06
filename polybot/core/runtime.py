from __future__ import annotations

import time
from dataclasses import dataclass
from typing import List

from rich.console import Console
from rich.live import Live
from rich.table import Table

from polybot.core.config import RootConfig
from polybot.core.http import HttpClient
from polybot.core.markets import MarketFetcher, filter_markets
from polybot.core.pricing import PricingClient
from polybot.core.strategy import compute_order_price


@dataclass
class AccountStats:
    name: str
    markets_total: int
    markets_eligible: int
    orders_planned: int


def build_table(stats: List[AccountStats]) -> Table:
    table = Table(title="POLYBOT Live", header_style="bold")
    table.add_column("Account")
    table.add_column("Markets Total", justify="right")
    table.add_column("Eligible", justify="right")
    table.add_column("Orders Planned", justify="right")

    for row in stats:
        table.add_row(
            row.name,
            str(row.markets_total),
            str(row.markets_eligible),
            str(row.orders_planned),
        )

    return table


def run_loop(cfg: RootConfig) -> None:
    console = Console()

    with Live(console=console, refresh_per_second=4) as live:
        while True:
            stats: List[AccountStats] = []

            for account in cfg.accounts:
                http = HttpClient(http_proxy=account.http_proxy)
                fetcher = MarketFetcher(http)
                pricing = PricingClient(http)

                markets = fetcher.fetch_markets()
                eligible = filter_markets(markets, cfg.app, cfg.strategy)
                eligible = eligible[: cfg.app.max_markets_per_account]

                orders_planned = 0
                for market in eligible:
                    token_id = market.get("token_id") or market.get("tokenId")
                    if not token_id:
                        continue
                    midpoint = pricing.get_midpoint(str(token_id))
                    if midpoint is None:
                        continue
                    _price = compute_order_price(midpoint, cfg.app, cfg.strategy)
                    orders_planned += 1

                stats.append(
                    AccountStats(
                        name=account.name,
                        markets_total=len(markets),
                        markets_eligible=len(eligible),
                        orders_planned=orders_planned,
                    )
                )

            live.update(build_table(stats))
            time.sleep(cfg.app.refresh_seconds)
