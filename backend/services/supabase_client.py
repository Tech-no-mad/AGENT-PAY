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


def get_auth_client() -> Client:
    """Use a fresh, anonymous-key client per authentication request."""
    if not settings.SUPABASE_URL or not settings.SUPABASE_ANON_KEY:
        raise RuntimeError("SUPABASE_URL and SUPABASE_ANON_KEY must be set")
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_ANON_KEY)


def resolve_payment_token(sb: Any, token_id: str, user_id: str) -> dict[str, Any]:
    """Resolve only an active database token belonging to the mandate owner."""
    token = (sb.table("payment_tokens").select("*").eq("id", token_id)
             .eq("user_id", user_id).eq("is_active", True).maybe_single().execute()).data
    if not token or not token.get("paypal_token_id"):
        raise ValueError("An active payment token owned by the user is required")
    return token


def get_supabase() -> Client:
    """Return (and lazily initialise) a Supabase client using service-role key or anon key."""
    global _supabase_client
    if _supabase_client is None:
        key = settings.SUPABASE_SERVICE_KEY or settings.SUPABASE_ANON_KEY
        if not settings.SUPABASE_URL or not key:
            raise RuntimeError("SUPABASE_URL and at least SUPABASE_ANON_KEY must be set")
        _supabase_client = create_client(settings.SUPABASE_URL, key)
        role_label = "service-role" if settings.SUPABASE_SERVICE_KEY else "anon"
        logger.info("Supabase client initialised (%s)", role_label)
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

VALID_TRANSACTION_EVENTS = {
    "mandate_created",
    "search_started",
    "deal_found",
    "policy_approved",
    "policy_rejected",
    "order_created",
    "authorized",
    "captured",
    "voided",
    "failed",
    "refunded",
}

EVENT_TYPE_MAPPING = {
    "mandate_created": "mandate_created",
    "created": "mandate_created",
    "mandate_cancelled": "voided",
    "cancelled": "voided",
    "mandate_completed": "captured",
    "completed": "captured",
    "mandate_failed": "failed",
    "search_products": "search_started",
    "search_started": "search_started",
    "searching": "search_started",
    "deal_found": "deal_found",
    "policy_approved": "policy_approved",
    "policy_rejected": "policy_rejected",
    "order_created": "order_created",
    "payment_authorized": "authorized",
    "authorized": "authorized",
    "payment_captured": "captured",
    "captured": "captured",
    "authorization_voided": "voided",
    "voided": "voided",
    "capture_denied": "failed",
    "capture_declined": "failed",
    "payment_refunded": "refunded",
    "refunded": "refunded",
    "failed": "failed",
    "error": "failed",
}


def normalize_transaction_event(event_type: str) -> str:
    """Normalize any arbitrary event string to the transactions table check constraint."""
    lower = (event_type or "").strip().lower()
    if lower in VALID_TRANSACTION_EVENTS:
        return lower
    if lower in EVENT_TYPE_MAPPING:
        return EVENT_TYPE_MAPPING[lower]
    # Fuzzy match on substrings
    if "search" in lower:
        return "search_started"
    if "deal" in lower or "found" in lower:
        return "deal_found"
    if "approve" in lower:
        return "policy_approved"
    if "reject" in lower:
        return "policy_rejected"
    if "order" in lower:
        return "order_created"
    if "auth" in lower:
        return "authorized"
    if "captur" in lower or "complet" in lower:
        return "captured"
    if "void" in lower or "cancel" in lower:
        return "voided"
    if "refund" in lower:
        return "refunded"
    return "failed"


def log_transaction(
    mandate_id: str,
    event_type: str,
    *,
    user_id: Optional[str] = None,
    paypal_resource_id: Optional[str] = None,
    paypal_order_id: Optional[str] = None,
    paypal_authorization_id: Optional[str] = None,
    paypal_capture_id: Optional[str] = None,
    amount: Optional[float] = None,
    currency: Optional[str] = "USD",
    status: Optional[str] = None,
    details: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    """Insert a row into the transactions table strictly conforming to database schema."""
    sb = get_supabase()

    # Resolve user_id if not explicitly provided
    resolved_user_id = user_id
    if not resolved_user_id:
        try:
            m_res = sb.table("mandates").select("user_id").eq("id", mandate_id).maybe_single().execute()
            if m_res and m_res.data:
                resolved_user_id = m_res.data.get("user_id")
        except Exception as lookup_err:
            logger.debug("Could not resolve user_id for mandate %s: %s", mandate_id, lookup_err)

    normalized_event = normalize_transaction_event(event_type)
    resource_id = (
        paypal_resource_id
        or paypal_capture_id
        or paypal_authorization_id
        or paypal_order_id
    )

    details_payload = dict(details or {})
    if paypal_order_id:
        details_payload["paypal_order_id"] = paypal_order_id
    if paypal_authorization_id:
        details_payload["paypal_authorization_id"] = paypal_authorization_id
    if paypal_capture_id:
        details_payload["paypal_capture_id"] = paypal_capture_id
    if status:
        details_payload["status"] = status
    if event_type and event_type.lower() != normalized_event:
        details_payload["source_event_type"] = event_type

    row: dict[str, Any] = {
        "mandate_id": mandate_id,
        "event_type": normalized_event,
        "paypal_resource_id": resource_id,
        "amount": amount,
        "currency": currency or "USD",
        "details": details_payload,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if resolved_user_id:
        row["user_id"] = resolved_user_id

    try:
        result = sb.table("transactions").insert(row).execute()
        return result.data[0] if result.data else None
    except Exception as exc:
        logger.error("Failed to log transaction for mandate %s: %s", mandate_id, exc)
        return None
