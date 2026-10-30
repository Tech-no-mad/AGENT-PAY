"""
Supabase client helpers — uses the **service-role** key so the backend
can bypass RLS for mandate / transaction management.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from supabase import Client, create_client

from backend.config import settings

logger = logging.getLogger(__name__)

# Module-level singleton
_supabase_client: Optional[Client] = None


def get_supabase() -> Client:
    """Return (and lazily initialise) a Supabase client using the service-role key."""
    global _supabase_client
    if _supabase_client is None:
        if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_KEY:
            raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set")
        _supabase_client = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
        logger.info("Supabase client initialised (service-role)")
    return _supabase_client


# ---------------------------------------------------------------------------
# User helpers
# ---------------------------------------------------------------------------

def get_user_profile(user_id: str) -> Optional[dict[str, Any]]:
    """Fetch a user profile row by id."""
    sb = get_supabase()
    try:
        result = sb.table("profiles").select("*").eq("id", user_id).maybe_single().execute()
        return result.data
    except Exception as exc:
        logger.error("Failed to fetch user profile %s: %s", user_id, exc)
        return None


# ---------------------------------------------------------------------------
# Mandate helpers
# ---------------------------------------------------------------------------

def update_mandate_status(
    mandate_id: str,
    status: str,
    extra_fields: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    """Update mandate status (and optional extra columns)."""
    sb = get_supabase()
    payload: dict[str, Any] = {
        "status": status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if extra_fields:
        payload.update(extra_fields)

    try:
        result = sb.table("mandates").update(payload).eq("id", mandate_id).execute()
        return result.data[0] if result.data else None
    except Exception as exc:
        logger.error("Failed to update mandate %s to %s: %s", mandate_id, status, exc)
        return None


# ---------------------------------------------------------------------------
# Transaction helpers
# ---------------------------------------------------------------------------

def log_transaction(
    mandate_id: str,
    event_type: str,
    *,
    paypal_order_id: Optional[str] = None,
    paypal_authorization_id: Optional[str] = None,
    paypal_capture_id: Optional[str] = None,
    amount: Optional[float] = None,
    currency: Optional[str] = None,
    status: Optional[str] = None,
    details: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    """Insert a row into the transactions table."""
    sb = get_supabase()
    row: dict[str, Any] = {
        "mandate_id": mandate_id,
        "event_type": event_type,
        "paypal_order_id": paypal_order_id,
        "paypal_authorization_id": paypal_authorization_id,
        "paypal_capture_id": paypal_capture_id,
        "amount": amount,
        "currency": currency,
        "status": status,
        "details": details,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        result = sb.table("transactions").insert(row).execute()
        return result.data[0] if result.data else None
    except Exception as exc:
        logger.error("Failed to log transaction for mandate %s: %s", mandate_id, exc)
        return None
