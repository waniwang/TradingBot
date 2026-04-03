"""Shared dependencies for the FastAPI dashboard API."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent

@lru_cache
def get_config() -> dict:
    with open(ROOT / "config.yaml") as f:
        cfg = yaml.safe_load(f)
    cfg.setdefault("alpaca", {})["api_key"] = (
        os.environ.get("ALPACA_API_KEY") or cfg["alpaca"].get("api_key", "")
    )
    cfg.setdefault("alpaca", {})["secret_key"] = (
        os.environ.get("ALPACA_SECRET_KEY") or cfg["alpaca"].get("secret_key", "")
    )
    return cfg


@lru_cache
def get_db_engine():
    from db.models import init_db
    config = get_config()
    return init_db(config["database"]["url"])


@lru_cache
def get_alpaca():
    from executor.alpaca_client import AlpacaClient
    client = AlpacaClient(get_config())
    client.connect()
    return client
