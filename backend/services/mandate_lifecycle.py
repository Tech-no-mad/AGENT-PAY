"""Durable payment/cancellation coordination using atomic conditional row updates."""

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from backend.services.supabase_client import log_transaction


class ExecutionStopped(RuntimeError):
    def __init__(self, mandate: dict[str, Any]):
        self.mandate = mandate
        super().__init__("Mandate is terminal or cancellation has been requested")


class CancellationPending(RuntimeError):
    """A payment already in flight must settle before cancellation is confirmed."""


class CancellationFailed(RuntimeError):
    """Funds have not been confirmed released; cancellation remains retryable."""


class PaymentOutcomeUnknown(RuntimeError):
    """Preserve the operation claim until the provider outcome is reconciled."""


def field(value: Any, name: str, default: Any = None) -> Any:
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


class MandateLifecycle:
    def __init__(self, sb: Any, paypal: Any):
        self.sb, self.paypal = sb, paypal

    def current(self, mandate_id: str) -> dict[str, Any]:
        row = self.sb.table("mandates").select("*").eq("id", mandate_id).maybe_single().execute().data
        if not row:
            raise RuntimeError("Mandate state is unavailable; refusing payment")
        return row

    def ensure_running(self, mandate_id: str) -> dict[str, Any]:
        row = self.current(mandate_id)
        if row.get("cancel_requested") or row["status"] in ("completed", "cancelled", "failed"):
            raise ExecutionStopped(row)
        return row

    def claim(self, mandate_id: str, operation: str) -> dict[str, Any]:
        allowed = {"authorize": ["active", "searching", "evaluating"], "capture": ["authorized"],
                   "void": ["active", "searching", "evaluating", "authorized", "failed"]}[operation]
        query = (self.sb.table("mandates").update({
            "payment_operation": operation, "payment_operation_id": str(uuid4()),
            "payment_started_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", mandate_id).in_("status", allowed).is_("payment_operation_id", "null"))
        if operation != "void":
            query = query.eq("cancel_requested", False)
        rows = query.execute().data
        if rows:
            return rows[0]
        current = self.current(mandate_id)
        if current.get("payment_operation_id"):
            raise CancellationPending("Another payment operation is in progress; retry after reconciliation")
        if current.get("cancel_requested") or current["status"] in ("completed", "cancelled", "failed"):
            raise ExecutionStopped(current)
        raise CancellationPending("Another payment operation is in progress; retry after reconciliation")

    def finish(self, claim: dict[str, Any], **fields: Any) -> dict[str, Any]:
        rows = (self.sb.table("mandates").update({
            **fields, "payment_operation": None, "payment_operation_id": None, "payment_started_at": None,
        }).eq("id", claim["id"]).eq("payment_operation_id", claim["payment_operation_id"]).execute()).data
        if not rows:
            raise RuntimeError("Cannot persist the payment outcome; reconciliation required")
        return rows[0]

    def checkpoint(self, claim: dict[str, Any], **fields: Any) -> None:
        rows = self.sb.table("mandates").update(fields).eq("id", claim["id"]).eq(
            "payment_operation_id", claim["payment_operation_id"]).execute().data
        if not rows:
            raise RuntimeError("Payment checkpoint could not be persisted; reconciliation required")

    def uncertain(self, claim: dict[str, Any], reason: str) -> None:
        # Retain the exclusive claim: a timeout is not evidence that no funds moved.
        self.checkpoint(claim, agent_reasoning=reason)
        raise PaymentOutcomeUnknown(reason)

    def cancel(self, mandate_id: str, user_id: str) -> dict[str, Any]:
        rows = (self.sb.table("mandates").update({"cancel_requested": True})
                .eq("id", mandate_id).eq("user_id", user_id)
                .in_("status", ["active", "searching", "evaluating", "authorized", "failed"]).execute()).data
        current = rows[0] if rows else self.current(mandate_id)
        if current["user_id"] != user_id:
            raise ValueError("Mandate does not belong to this user")
        if current["status"] in ("completed", "cancelled"):
            return current
        if current.get("payment_operation_id"):
            raise CancellationPending("Cancellation requested; a payment is in flight. Retry after it settles")
        try:
            claim = self.claim(mandate_id, "void")
        except ExecutionStopped as exc:
            if exc.mandate["status"] in ("completed", "cancelled"):
                return exc.mandate
            raise
        authorization_id = claim.get("paypal_authorization_id")
        if authorization_id:
            try:
                self.paypal.void_authorization(authorization_id)
            except Exception as exc:
                # A prior/ambiguous void may have succeeded remotely. Confirm, never assume.
                try:
                    confirmed = field(self.paypal.get_authorization(authorization_id), "status") == "VOIDED"
                except Exception:
                    confirmed = False
                if not confirmed:
                    self.finish(claim, agent_reasoning="Cancellation pending: PayPal void failed; retry to release funds")
                    log_transaction(mandate_id, "MANDATE_FAILED", user_id=user_id, supabase_client=self.sb,
                                    paypal_authorization_id=authorization_id, status="void_failed",
                                    details={"action": "cancel", "error": str(exc), "retryable": True})
                    raise CancellationFailed("PayPal could not confirm release of funds; cancellation remains pending. Retry") from exc
        updated = self.finish(claim, status="cancelled", agent_reasoning="Cancellation confirmed; any authorization has been voided")
        if authorization_id:
            log_transaction(mandate_id, "AUTHORIZATION_VOIDED", user_id=user_id, supabase_client=self.sb,
                            paypal_authorization_id=authorization_id, status="voided")
        log_transaction(mandate_id, "MANDATE_CANCELLED", user_id=user_id, supabase_client=self.sb,
                        status="cancelled", details={"had_authorization": bool(authorization_id)})
        return updated
