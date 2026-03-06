from __future__ import annotations

from typing import Optional, List

from pydantic import BaseModel, Field


class AppConfig(BaseModel):
    refresh_seconds: int = 20
    dry_run: bool = True
    max_order_usdc: float = 100.0
    max_open_exposure_usdc: float = 200.0
    max_markets_per_account: int = 20
    min_price: float = 0.05
    max_price: float = 0.95
    enforce_incentive_cap: bool = True


class AccountConfig(BaseModel):
    name: str
    signature_type: str = "proxy"
    funder: str
    private_key_env: str
    api_key_env: Optional[str] = None
    api_secret_env: Optional[str] = None
    api_passphrase_env: Optional[str] = None
    http_proxy: Optional[str] = None


class StrategyConfig(BaseModel):
    side: str = "buy"
    order_type: str = "GTC"
    post_only: bool = True
    price_offset_bps: int = 10
    respect_max_incentive_spread: bool = True
    min_incentive_size_key: str = "min_incentive_size"
    max_incentive_spread_key: str = "max_incentive_spread"


class RootConfig(BaseModel):
    app: AppConfig = Field(default_factory=AppConfig)
    accounts: List[AccountConfig]
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
