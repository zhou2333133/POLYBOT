from __future__ import annotations

from typing import Optional

from polybot.core.config import AppConfig, StrategyConfig


def apply_tick_size(price: float, tick_size: Optional[float]) -> float:
    if not tick_size:
        return price
    return round(price / tick_size) * tick_size


def compute_order_price(
    midpoint: float,
    app: AppConfig,
    strategy: StrategyConfig,
) -> float:
    offset = midpoint * (strategy.price_offset_bps / 10000.0)
    price = midpoint - offset if strategy.side.lower() == "buy" else midpoint + offset
    price = max(app.min_price, min(app.max_price, price))
    return price
