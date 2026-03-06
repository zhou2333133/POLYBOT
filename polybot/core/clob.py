from __future__ import annotations

from typing import Optional

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY, SELL

from polybot.core.config import AccountConfig, StrategyConfig


def _signature_type_value(signature_type: str) -> int:
    normalized = signature_type.strip().lower()
    if normalized in {"eoa", "0"}:
        return 0
    if normalized in {"proxy", "magic", "1"}:
        return 1
    if normalized in {"gnosis-safe", "gnosis", "safe", "2"}:
        return 2
    return 1


def create_client(account: AccountConfig, private_key: str) -> ClobClient:
    return ClobClient(
        "https://clob.polymarket.com",
        key=private_key,
        chain_id=account.chain_id,
        signature_type=_signature_type_value(account.signature_type),
        funder=account.funder,
    )


def ensure_api_creds(client: ClobClient) -> None:
    client.set_api_creds(client.create_or_derive_api_creds())


def place_limit_order(
    client: ClobClient,
    strategy: StrategyConfig,
    token_id: str,
    price: float,
    size: float,
) -> dict:
    side = BUY if strategy.side.lower() == "buy" else SELL
    order = OrderArgs(token_id=token_id, price=price, size=size, side=side)
    signed = client.create_order(order)
    order_type = OrderType[strategy.order_type]
    return client.post_order(signed, order_type)
