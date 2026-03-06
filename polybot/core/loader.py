from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Any

import yaml

from polybot.core.config import RootConfig


def load_config(path: str) -> RootConfig:
    cfg_path = Path(path)
    if not cfg_path.exists():
        raise FileNotFoundError(f"Config not found: {cfg_path}")
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    return RootConfig.model_validate(data)


def load_secret(env_name: str) -> str:
    value = os.getenv(env_name, "")
    if not value:
        raise RuntimeError(f"Missing required env var: {env_name}")
    return value


def resolve_account_secrets(account: Dict[str, Any]) -> Dict[str, Any]:
    resolved = dict(account)
    resolved["private_key"] = load_secret(account["private_key_env"])

    if account.get("api_key_env"):
        resolved["api_key"] = os.getenv(account["api_key_env"], "")
        resolved["api_secret"] = os.getenv(account["api_secret_env"], "")
        resolved["api_passphrase"] = os.getenv(account["api_passphrase_env"], "")

    return resolved
