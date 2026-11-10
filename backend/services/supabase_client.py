"""
Supabase client helpers — uses the **service-role** key so the backend
can bypass RLS for mandate / transaction management.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Optional

from supabase import Client, create_client

from backend.config import settings
from backend.services.time_utils import utc_timestamp

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
    """Use the service key, or main's anonymous-key development fallback (RLS applies)."""
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
    *,
    supabase_client: Any = None,
) -> Optional[dict[str, Any]]:
    """Update mandate status (and optional extra columns)."""
    sb = supabase_client if supabase_client is not None else get_supabase()
    payload: dict[str, Any] = {
        "status": status,
        "updated_at": utc_timestamp(),
    }
    if extra_fields:
        payload.update(extra_fields)

    try:
        allowed = ["active", "searching", "evaluating"]
        if status not in ("searching", "evaluating"):
            allowed.append("authorized")
        result = (sb.table("mandates").update(payload).eq("id", mandate_id)
                  .in_("status", allowed).eq("cancel_requested", False)
                  .is_("payment_operation_id", "null").execute())
        return result.data[0] if result.data else None
    except Exception as exc:
        logger.error("Failed to update mandate %s to %s: %s", mandate_id, status, exc)
        raise


# ---------------------------------------------------------------------------
# Transaction helpers
# ---------------------------------------------------------------------------

class AuditLogError(RuntimeError):
    """Required audit persistence failed; callers must surface the failure."""


_EVENT_NAMES = {
    "CONSTRAINTS_DEFINED": "constraints_defined",
    "MANDATE_CREATED": "mandate_created", "PRODUCT_SEARCH": "search_started",
    "ORDER_CREATED": "order_created", "ORDER_AUTHORIZED": "authorized",
    "PAYMENT_AUTHORIZED": "authorized", "PAYMENT_CAPTURED": "captured",
    "AUTHORIZATION_VOIDED": "voided", "PAYMENT_REFUNDED": "refunded",
    "ORDER_FAILED": "failed", "CAPTURE_FAILED": "failed", "CAPTURE_DENIED": "failed",
    "MANDATE_FAILED": "failed", "MANDATE_COMPLETED": "captured", "MANDATE_CANCELLED": "voided",
    "CREATED": "mandate_created", "CANCELLED": "voided", "COMPLETED": "captured",
    "SEARCH_PRODUCTS": "search_started", "SEARCHING": "search_started",
    "CAPTURE_DECLINED": "failed", "ERROR": "failed",
}
_SCHEMA_EVENTS = {"mandate_created", "constraints_defined", "search_started", "deal_found", "policy_approved", "policy_rejected",
                  "purchase_confirmation_requested", "purchase_confirmation_approved", "purchase_confirmation_auto_approved",
                  "order_created", "authorized", "captured", "voided", "failed", "refunded"}


def normalize_transaction_event(event_type: str) -> str:
    """Normalize known aliases exactly; failure events must never imply success."""
    event_type = (event_type or "").strip()
    normalized = _EVENT_NAMES.get(event_type.upper(), event_type.lower())
    if normalized not in _SCHEMA_EVENTS:
        raise ValueError(f"Unsupported audit event: {event_type}")
    return normalized

def log_transaction(
    mandate_id: str,
    event_type: str,
    *,
    paypal_order_id: Optional[str] = None,
    paypal_authorization_id: Optional[str] = None,
    paypal_capture_id: Optional[str] = None,
    paypal_resource_id: Optional[str] = None,
    amount: Optional[float] = None,
    currency: Optional[str] = None,
    status: Optional[str] = None,
    details: Optional[dict[str, Any]] = None,
    user_id: Optional[str] = None,
    supabase_client: Any = None,
) -> Optional[dict[str, Any]]:
    """Write the migration's exact audit contract, retaining metadata in details."""
    try:
        sb = supabase_client if supabase_client is not None else get_supabase()
        if not user_id:
            mandate = sb.table("mandates").select("user_id").eq("id", mandate_id).maybe_single().execute().data
            if not mandate:
                raise ValueError("Cannot determine the mandate's audit owner")
            user_id = mandate["user_id"]
        if event_type == "POLICY_CHECK":
            if status not in ("APPROVED", "REJECTED"):
                raise ValueError("Policy audit requires an explicit decision")
            normalized = "policy_approved" if status == "APPROVED" else "policy_rejected"
        else:
            normalized = normalize_transaction_event(event_type)
        if normalized not in _SCHEMA_EVENTS:
            raise ValueError(f"Unsupported audit event: {event_type}")
        metadata = dict(details or {})
        metadata["original_event"] = event_type
        if amount is not None and (isinstance(amount, bool) or not isinstance(amount, (int, float)) or not math.isfinite(amount)):
            metadata["submitted_amount"] = str(amount)
            amount = None
        for key, value in {"status": status, "paypal_order_id": paypal_order_id,
                           "paypal_authorization_id": paypal_authorization_id,
                           "paypal_capture_id": paypal_capture_id}.items():
            if value is not None:
                metadata[key] = value
        row = {
            "mandate_id": mandate_id, "user_id": user_id, "event_type": normalized,
            "paypal_resource_id": paypal_resource_id or paypal_capture_id or paypal_authorization_id or paypal_order_id,
            "amount": amount, "currency": currency or "USD", "details": metadata,
            "created_at": utc_timestamp(),
        }
        result = sb.table("transactions").insert(row).execute()
        if not result.data:
            raise RuntimeError("Audit insert returned no persisted row")
        return result.data[0]
    except Exception as exc:
        logger.exception("Audit persistence failed for mandate %s", mandate_id)
        raise AuditLogError(f"Audit persistence failed for mandate {mandate_id}") from exc
