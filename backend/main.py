"""
AgentPay Backend — FastAPI Application Entry Point.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.config import settings
from backend.routes.auth import router as auth_router
from backend.routes.mandates import router as mandates_router
from backend.routes.vault import router as vault_router
from backend.routes.webhooks import router as webhooks_router

# Log each record's actual creation time in UTC, with an explicit zone.
_log_formatter = logging.Formatter(
    fmt="%(asctime)s.%(msecs)03dZ [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
_log_formatter.converter = time.gmtime
_log_handler = logging.StreamHandler()
_log_handler.setFormatter(_log_formatter)
logging.basicConfig(
    level=logging.INFO,
    handlers=[_log_handler],
)
logger = logging.getLogger("agentpay")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup and shutdown lifecycle events."""
    logger.info("Starting AgentPay API server...")
    if not settings.PAYPAL_CLIENT_ID:
        logger.warning("PAYPAL_CLIENT_ID is not configured in .env")
    if not settings.CLOUDFLARE_ACCOUNT_ID:
        logger.warning("CLOUDFLARE_ACCOUNT_ID is not configured in .env")
    if not settings.SUPABASE_URL:
        logger.warning("SUPABASE_URL is not configured in .env")

    yield

    logger.info("Shutting down AgentPay API server...")


app = FastAPI(
    title="AgentPay API",
    description="Autonomous Agentic Payments powered by PayPal SDK, Supabase & Cloudflare Workers AI",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS configuration
configured_origins = [o.strip() for o in settings.FRONTEND_URL.split(",") if o.strip()]
origins = list(set([
    *configured_origins,
    "https://agent-pay-black.vercel.app",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:8000",
]))

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_origin_regex=r"^https:\/\/.*\.vercel\.app$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register API Routers
app.include_router(auth_router)
app.include_router(vault_router)
app.include_router(mandates_router)
app.include_router(webhooks_router)


@app.get("/")
async def root() -> dict[str, str]:
    """Root endpoint for status check."""
    return {
        "project": "AgentPay",
        "description": "Autonomous Agentic Commerce with PayPal, Supabase & Cloudflare Workers AI",
        "status": "online",
        "docs": "/docs",
    }


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
