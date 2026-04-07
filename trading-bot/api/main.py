"""FastAPI dashboard API — read-only endpoints for the Next.js frontend."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Allow imports from trading-bot root
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.routes import status, portfolio, positions, watchlist, signals, performance, pipeline, risk, market, doctor

API_KEY = os.environ.get("DASHBOARD_API_KEY", "dev-key")

app = FastAPI(title="Trading Bot API", version="1.0.0")

# CORS — allow Vercel frontend + local dev
ALLOWED_ORIGINS = os.environ.get(
    "CORS_ORIGINS",
    "http://localhost:3000,http://127.0.0.1:3000"
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.middleware("http")
async def verify_api_key(request: Request, call_next):
    if request.url.path in ("/api/health", "/api/doctor"):
        return await call_next(request)
    key = request.headers.get("X-API-Key")
    if key != API_KEY:
        return JSONResponse(status_code=401, content={"detail": "Invalid API key"})
    return await call_next(request)


app.include_router(status.router, prefix="/api")
app.include_router(portfolio.router, prefix="/api")
app.include_router(positions.router, prefix="/api")
app.include_router(watchlist.router, prefix="/api")
app.include_router(signals.router, prefix="/api")
app.include_router(performance.router, prefix="/api")
app.include_router(pipeline.router, prefix="/api")
app.include_router(risk.router, prefix="/api")
app.include_router(market.router, prefix="/api")
app.include_router(doctor.router, prefix="/api")


@app.get("/api/health")
def health():
    return {"status": "ok"}
