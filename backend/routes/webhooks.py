"""
PayPal Webhook Handler — receives and processes asynchronous payment events.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Header, Request, status

from backend.config import settings
from backend.services.supabase_client import get_supabase, log_transaction, update_mandate_status

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


@router.post("/paypal")
async def paypal_webhook(
    request: Request,
    paypal_auth_algo: str | None = Header(None, alias="PAYPAL-AUTH-ALGO"),
    paypal_transmission_id: str | None = Header(None, alias="PAYPAL-TRANSMISSION-ID"),
    paypal_cert_url: str | None = Header(None, alias="PAYPAL-CERT-URL"),
    paypal_transmission_sig: str | None = Header(None, alias="PAYPAL-TRANSMISSION-SIG"),
    paypal_transmission_time: str | None = Header(None, alias="PAYPAL-TRANSMISSION-TIME"),
) -> dict[str, str]:
    """
    Handle PayPal webhook notifications.
    Supports AUTHORIZATION, CAPTURE, and VOID lifecycle events.
    """
    try:
        body = await request.json()
    except Exception as exc:
        logger.error("Failed to parse webhook JSON: %s", exc)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON body")

    event_type = body.get("event_type", "UNKNOWN")
    resource = body.get("resource", {})
    resource_id = resource.get("id", "")
    summary = body.get("summary", "")

    logger.info("Received PayPal webhook: %s (resource_id=%s)", event_type, resource_id)

    # Find custom_id / mandate_id from resource or purchase_units
    mandate_id = resource.get("custom_id")
    if not mandate_id and "purchase_units" in resource:
        units = resource.get("purchase_units", [])
        if units and isinstance(units, list):
            mandate_id = units[0].get("custom_id") or units[0].get("reference_id")

    amount_val = None
    currency_code = "USD"
    if "amount" in resource:
        amount_dict = resource.get("amount", {})
        try:
            amount_val = float(amount_dict.get("value", 0))
            currency_code = amount_dict.get("currency_code", "USD")
        except (ValueError, TypeError):
            pass

    # Map events to system actions
    if event_type == "PAYMENT.CAPTURE.COMPLETED":
        if mandate_id:
            update_mandate_status(
                mandate_id,
                "completed",
                {
                    "executed_amount": amount_val,
                    "agent_reasoning": "Payment captured successfully via PayPal webhook confirmation.",
                },
            )
            log_transaction(
                mandate_id=mandate_id,
                event_type="PAYMENT_CAPTURED",
                paypal_capture_id=resource_id,
                amount=amount_val,
                currency=currency_code,
                status="completed",
                details={"summary": summary, "webhook_event": event_type},
            )

    elif event_type == "PAYMENT.AUTHORIZATION.CREATED":
        if mandate_id:
            update_mandate_status(
                mandate_id,
                "authorized",
                {"paypal_authorization_id": resource_id},
            )
            log_transaction(
                mandate_id=mandate_id,
                event_type="PAYMENT_AUTHORIZED",
                paypal_authorization_id=resource_id,
                amount=amount_val,
                currency=currency_code,
                status="authorized",
                details={"summary": summary, "webhook_event": event_type},
            )

    elif event_type == "PAYMENT.AUTHORIZATION.VOIDED":
        if mandate_id:
            update_mandate_status(
                mandate_id,
                "cancelled",
                {"agent_reasoning": "PayPal authorization voided."},
            )
            log_transaction(
                mandate_id=mandate_id,
                event_type="AUTHORIZATION_VOIDED",
                paypal_authorization_id=resource_id,
                status="voided",
                details={"summary": summary, "webhook_event": event_type},
            )

    elif event_type in ("PAYMENT.CAPTURE.DENIED", "PAYMENT.CAPTURE.DECLINED"):
        if mandate_id:
            update_mandate_status(
                mandate_id,
                "failed",
                {"agent_reasoning": f"PayPal capture was denied: {summary}"},
            )
            log_transaction(
                mandate_id=mandate_id,
                event_type="CAPTURE_DENIED",
                paypal_capture_id=resource_id,
                status="failed",
                details={"summary": summary, "webhook_event": event_type},
            )

    elif event_type == "PAYMENT.CAPTURE.REFUNDED":
        if mandate_id:
            log_transaction(
                mandate_id=mandate_id,
                event_type="PAYMENT_REFUNDED",
                paypal_capture_id=resource_id,
                amount=amount_val,
                currency=currency_code,
                status="refunded",
                details={"summary": summary, "webhook_event": event_type},
            )

    return {"status": "success", "event": event_type}
