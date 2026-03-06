from __future__ import annotations

from typing import Optional, List

from pydantic import BaseModel, Field


class AppConfig(BaseModel):
    refresh_seconds: int = 20
    order_refresh_seconds: int = 300
    dry_run: bool = True
    max_order_usdc: float = 100.0
    max_open_exposure_usdc: float = 200.0
    max_markets_per_account: int = 20
    max_markets_to_scan: int = 200
    min_days_to_expiry: int = 7
    min_price: float = 0.05
    max_price: float = 0.95
    enforce_incentive_cap: bool = True
    only_reward_markets: bool = True
    require_rewards_daily_rate: bool = True
    http_timeout_seconds: int = 8


class AccountConfig(BaseModel):
    name: str
    signature_type: str = "proxy"
    funder: str
    private_key_env: str
    chain_id: int = 137
    api_key_env: Optional[str] = None
    api_secret_env: Optional[str] = None
    api_passphrase_env: Optional[str] = None
    http_proxy: Optional[str] = None


class StrategyConfig(BaseModel):
    side: str = "buy"
    order_type: str = "GTC"
    post_only: bool = True
    price_offset_bps: int = 10
    cancel_replace_threshold_bps: int = 5
    check_scoring: bool = True
    max_competition_size: Optional[float] = None
    respect_max_incentive_spread: bool = True
    require_spread_within_reward: bool = True
    auto_level_selection: bool = True
    auto_level_min: int = 1
    auto_level_depth: int = 3
    fill_risk_weight: float = 0.5
    min_incentive_size_key: str = "min_incentive_size"
    max_incentive_spread_key: str = "max_incentive_spread"


class RootConfig(BaseModel):
    app: AppConfig = Field(default_factory=AppConfig)
    accounts: List[AccountConfig]
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
