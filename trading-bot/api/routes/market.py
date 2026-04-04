"""Market context endpoint — SPY/QQQ daily change."""

from __future__ import annotations

from fastapi import APIRouter

from db.models import get_engine

router = APIRouter()


@router.get("/market")
def get_market():
    """Return SPY and QQQ current price and daily change %."""
    try:
        from executor.alpaca_client import AlpacaClient
        import yaml
        from pathlib import Path

        config_path = Path(__file__).parent.parent.parent / "config.yaml"
        with open(config_path) as f:
            cfg = yaml.safe_load(f)

        import os
        api_key = os.environ.get("ALPACA_API_KEY", cfg.get("alpaca", {}).get("api_key", ""))
        secret_key = os.environ.get("ALPACA_SECRET_KEY", cfg.get("alpaca", {}).get("secret_key", ""))
        env = os.environ.get("ENVIRONMENT", cfg.get("environment", "paper"))

        if not api_key or not secret_key:
            return {"indices": [], "error": "Alpaca credentials not configured"}

        client = AlpacaClient(api_key, secret_key, paper=(env == "paper"))
        snapshots = client.get_snapshots(["SPY", "QQQ"])

        indices = []
        for ticker in ["SPY", "QQQ"]:
            snap = snapshots.get(ticker)
            if snap:
                price = snap.get("price", 0)
                prev_close = snap.get("prev_close", 0)
                change_pct = ((price - prev_close) / prev_close * 100) if prev_close else 0
                indices.append({
                    "ticker": ticker,
                    "price": round(price, 2),
                    "change_pct": round(change_pct, 2),
                })

        return {"indices": indices}
    except Exception as e:
        return {"indices": [], "error": str(e)[:200]}
