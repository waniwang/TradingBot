"""Shared dependencies for the FastAPI dashboard API."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent

@lru_cache
def get_config() -> dict:
    # BOT_CONFIG lets a second API instance point at a personal config file
    # (e.g. config.ib.local.yaml) without touching the shared config.yaml.
    # Absolute paths are honored; relative paths resolve against the repo root.
    config_path = os.environ.get("BOT_CONFIG", "config.yaml")
    with open(ROOT / config_path) as f:
        cfg = yaml.safe_load(f)
    cfg.setdefault("alpaca", {})["api_key"] = (
        os.environ.get("ALPACA_API_KEY") or cfg["alpaca"].get("api_key", "")
    )
    cfg.setdefault("alpaca", {})["secret_key"] = (
        os.environ.get("ALPACA_SECRET_KEY") or cfg["alpaca"].get("secret_key", "")
    )
    # Most route modules call db.models.get_engine() directly, which reads
    # DATABASE_URL from the environment. Export the config's DB URL so those
    # routes pick up the right database when BOT_CONFIG selects a personal
    # config (e.g. the IBKR bot's DB). An explicit DATABASE_URL env var, if
    # already set, still wins.
    db_url = (cfg.get("database") or {}).get("url")
    if db_url:
        os.environ.setdefault("DATABASE_URL", db_url)
    return cfg


@lru_cache
def get_enabled_strategies() -> frozenset[str]:
    """Return the set of strategy slugs enabled in config.yaml."""
    cfg = get_config()
    enabled = cfg.get("strategies", {}).get("enabled", []) or []
    return frozenset(enabled)


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
