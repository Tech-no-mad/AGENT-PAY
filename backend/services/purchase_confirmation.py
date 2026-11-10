"""Durable, timed confirmation between policy approval and PayPal authorization."""

import asyncio
from datetime import timedelta
from typing import Any
from uuid import uuid4

from backend.services.categories import normalize_category
from backend.services.mandate_lifecycle import MandateLifecycle
from backend.services.supabase_client import log_transaction
from backend.services.time_utils import parse_utc_timestamp, utc_now


CONFIRMATION_SECONDS = 10
APPROVED = ("approved", "auto_approved")


class ConfirmationConflict(ValueError):
    """This confirmation has expired, changed, or already been decided."""


def purchase_product(args: dict[str, Any]) -> dict[str, Any]:
    return {
        "product_name": args.get("product_name") or "Selected purchase",
        "price": args["price"], "currency": args.get("currency", "USD").upper(),
        "category": normalize_category(args.get("category")),
    }


class PurchaseConfirmation:
    def __init__(self, lifecycle: MandateLifecycle):
        self.lifecycle = lifecycle
        self.sb = lifecycle.sb

    def _pending_query(self, mandate_id: str, confirmation_id: str, fields: dict[str, Any]):
        return (self.sb.table("mandates").update(fields).eq("id", mandate_id)
                .eq("purchase_confirmation_id", confirmation_id).eq("purchase_confirmation_status", "pending")
                .eq("cancel_requested", False).in_("status", ["active", "searching", "evaluating"])
                .is_("payment_operation_id", "null"))

    def begin(self, mandate_id: str, product: dict[str, Any]) -> dict[str, Any]:
        current = self.lifecycle.ensure_running(mandate_id)
        if current.get("payment_operation_id") or current["status"] == "authorized":
            raise ConfirmationConflict("Payment is already in progress")
        state = current.get("purchase_confirmation_status")
        if current.get("purchase_confirmation_product") == product and state in ("pending", *APPROVED):
            return current
        if state == "pending":
            raise ConfirmationConflict("Another product is awaiting confirmation")
        confirmation_id = str(uuid4())
        fields = {
            "status": "evaluating", "purchase_confirmation_id": confirmation_id,
            "purchase_confirmation_status": "pending", "purchase_confirmation_ready": False,
            "purchase_confirmation_product": product,
            "purchase_confirmation_expires_at": (utc_now() + timedelta(seconds=CONFIRMATION_SECONDS)).isoformat(),
        }
        query = (self.sb.table("mandates").update(fields).eq("id", mandate_id)
                 .eq("cancel_requested", False).in_("status", ["active", "searching", "evaluating"])
                 .is_("payment_operation_id", "null"))
        previous_id = current.get("purchase_confirmation_id")
        query = query.eq("purchase_confirmation_id", previous_id) if previous_id else query.is_("purchase_confirmation_id", "null")
        rows = query.execute().data
        if not rows:
            self.lifecycle.ensure_running(mandate_id)
            raise ConfirmationConflict("Purchase confirmation changed; retry with the current offer")
        self._audit(rows[0], "purchase_confirmation_requested")
        return rows[0]

    def _audit(self, mandate: dict[str, Any], event: str) -> None:
        product = mandate["purchase_confirmation_product"]
        log_transaction(
            mandate["id"], event, user_id=mandate["user_id"], supabase_client=self.sb,
            amount=product["price"], currency=product["currency"],
            details={**product, "confirmation_id": mandate["purchase_confirmation_id"],
                     "expires_at": mandate["purchase_confirmation_expires_at"]},
        )

    def decide(self, mandate_id: str, user_id: str, confirmation_id: str, decision: str) -> dict[str, Any]:
        if decision not in ("continue", "cancel"):
            raise ValueError("Decision must be continue or cancel")
        return self._resolve(mandate_id, confirmation_id, decision, user_id=user_id)

    def _resolve(self, mandate_id: str, confirmation_id: str, decision: str, *, user_id: str) -> dict[str, Any]:
        current = self.lifecycle.current(mandate_id)
        if current["user_id"] != user_id:
            raise PermissionError("Mandate not found")
        if current.get("purchase_confirmation_id") != confirmation_id:
            raise ConfirmationConflict("This purchase offer is no longer current")
        expected = {"continue": "approved", "timeout": "auto_approved", "cancel": "cancelled"}[decision]
        if current.get("purchase_confirmation_status") == expected:
            if decision == "cancel" and current["status"] != "cancelled":
                return self.lifecycle.cancel(mandate_id, user_id, reason="Purchase cancelled by you during confirmation")
            return current
        if current.get("purchase_confirmation_status") != "pending" or current.get("cancel_requested"):
            raise ConfirmationConflict("This purchase confirmation has already been decided")
        now = utc_now()
        expires = parse_utc_timestamp(current["purchase_confirmation_expires_at"])
        if (decision == "timeout" and now < expires) or (decision != "timeout" and now >= expires):
            raise ConfirmationConflict("The purchase confirmation window has ended" if decision != "timeout" else "The confirmation window is still open")
        fields: dict[str, Any] = {"purchase_confirmation_status": expected}
        if decision == "cancel":
            fields.update(cancel_requested=True, agent_reasoning="Purchase cancelled by you during confirmation")
        query = self._pending_query(mandate_id, confirmation_id, fields).eq("user_id", user_id)
        query = query.lte("purchase_confirmation_expires_at", now.isoformat()) if decision == "timeout" else query.gt("purchase_confirmation_expires_at", now.isoformat())
        rows = query.execute().data
        if not rows:
            raise ConfirmationConflict("This purchase confirmation has already been decided")
        current = rows[0]
        if decision == "cancel":
            return self.lifecycle.cancel(mandate_id, user_id, reason="Purchase cancelled by you during confirmation")
        try:
            self._audit(current, "purchase_confirmation_approved" if decision == "continue" else "purchase_confirmation_auto_approved")
        except Exception:
            # Approval is not consumable until its audit has been saved.
            self.lifecycle.cancel(mandate_id, user_id, reason="Purchase confirmation could not be recorded")
            raise
        rows = (self.sb.table("mandates").update({"purchase_confirmation_ready": True})
                .eq("id", mandate_id).eq("purchase_confirmation_id", confirmation_id)
                .eq("purchase_confirmation_status", expected).eq("cancel_requested", False).execute()).data
        if not rows:
            self.lifecycle.ensure_running(mandate_id)
            raise ConfirmationConflict("Purchase confirmation changed before it could be completed")
        return rows[0]

    async def wait(self, mandate_id: str, confirmation_id: str) -> dict[str, Any]:
        while True:
            current = self.lifecycle.ensure_running(mandate_id)
            if current.get("purchase_confirmation_id") != confirmation_id:
                raise ConfirmationConflict("The selected product changed during confirmation")
            state = current.get("purchase_confirmation_status")
            if state in APPROVED and current.get("purchase_confirmation_ready"):
                return current
            expires = parse_utc_timestamp(current["purchase_confirmation_expires_at"])
            if utc_now() > expires + timedelta(seconds=30):
                raise ConfirmationConflict("Purchase confirmation could not finish; no payment was started")
            if state == "pending" and utc_now() >= expires:
                try:
                    self._resolve(mandate_id, confirmation_id, "timeout", user_id=current["user_id"])
                except ConfirmationConflict:
                    await asyncio.sleep(0.25)  # Re-read a simultaneous decision without spinning.
                continue
            if state not in ("pending", *APPROVED):
                raise ConfirmationConflict("Purchase confirmation is not available")
            await asyncio.sleep(0.25)
