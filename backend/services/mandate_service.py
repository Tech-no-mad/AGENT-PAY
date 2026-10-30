"""
Mandate Service — business logic for creating, listing, and managing mandates.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from backend.services.agent_orchestrator import AgentOrchestrator
from backend.services.cloudflare_ai import CloudflareAI
from backend.services.supabase_client import get_supabase, log_transaction, update_mandate_status, resolve_payment_token

logger = logging.getLogger(__name__)


class MandateService:
    """High-level mandate lifecycle management."""

    def __init__(
        self,
        supabase_client: Any,
        agent_orchestrator: AgentOrchestrator,
        cloudflare_ai: CloudflareAI,
    ) -> None:
        self.sb = supabase_client
        self.orchestrator = agent_orchestrator
        self.ai = cloudflare_ai

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def create_mandate(
        self,
        user_id: str,
        description: str,
        payment_token_id: str,
        constraints: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """
        Create a new mandate, parse constraints via AI if not provided,
        and kick off the agent execution in the background.
        """
        resolve_payment_token(self.sb, payment_token_id, user_id)

        # Parse constraints from natural language if not explicitly provided
        if not constraints:
            constraints = await self.ai.parse_mandate(description)

        mandate_row = {
            "user_id": user_id,
            "description": description,
            "payment_token_id": payment_token_id,
            "status": "active",
            "constraints": constraints,
        }

        result = self.sb.table("mandates").insert(mandate_row).execute()
        mandate = result.data[0] if result.data else mandate_row
        mandate_id = mandate.get("id", "")

        logger.info("Mandate created: %s for user %s", mandate_id, user_id)

        log_transaction(
            mandate_id=mandate_id,
            event_type="MANDATE_CREATED",
            status="pending",
            details={"description": description},
        )

        # Fire-and-forget: kick off agent execution in the background
        asyncio.create_task(self._run_agent(mandate))

        return mandate

    async def _run_agent(self, mandate: dict[str, Any]) -> None:
        """Background task that runs the agent orchestrator."""
        try:
            await self.orchestrator.execute_mandate(mandate)
        except Exception as exc:
            logger.exception("Background agent execution failed for mandate %s", mandate.get("id"))
            update_mandate_status(
                mandate.get("id", ""),
                "failed",
                {"agent_reasoning": f"Background execution error: {exc}"},
            )

    async def get_mandates(self, user_id: str) -> list[dict[str, Any]]:
        """List all mandates for a user, ordered by most recent first."""
        result = (
            self.sb.table("mandates")
            .select("*")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .execute()
        )
        return result.data or []

    async def get_mandate(self, mandate_id: str, user_id: str) -> Optional[dict[str, Any]]:
        """Fetch a single mandate ensuring it belongs to the user."""
        result = (
            self.sb.table("mandates")
            .select("*")
            .eq("id", mandate_id)
            .eq("user_id", user_id)
            .maybe_single()
            .execute()
        )
        return result.data

    async def cancel_mandate(self, mandate_id: str, user_id: str) -> Optional[dict[str, Any]]:
        """
        Cancel a mandate. If there's an active authorization, void it first.
        """
        mandate = await self.get_mandate(mandate_id, user_id)
        if not mandate:
            return None

        current_status = mandate.get("status", "")
        if current_status in ("completed", "cancelled", "failed"):
            logger.warning("Cannot cancel mandate %s — already %s", mandate_id, current_status)
            return mandate

        # Void authorization if one exists
        authorization_id = mandate.get("paypal_authorization_id")
        if authorization_id:
            try:
                from backend.services.paypal_service import PayPalService
                paypal = PayPalService()
                paypal.void_authorization(authorization_id)
                log_transaction(
                    mandate_id=mandate_id,
                    event_type="AUTHORIZATION_VOIDED",
                    paypal_authorization_id=authorization_id,
                    status="voided",
                    details={"reason": "mandate_cancelled"},
                )
            except Exception as exc:
                logger.error("Failed to void authorization %s: %s", authorization_id, exc)

        updated = update_mandate_status(mandate_id, "cancelled")
        log_transaction(
            mandate_id=mandate_id,
            event_type="MANDATE_CANCELLED",
            status="cancelled",
        )
        return updated

    # ------------------------------------------------------------------
    # Status transitions
    # ------------------------------------------------------------------

    async def mark_completed(self, mandate_id: str, details: Optional[dict[str, Any]] = None) -> None:
        """Transition mandate to completed status."""
        extra = details or {}
        update_mandate_status(mandate_id, "completed", extra)
        log_transaction(mandate_id=mandate_id, event_type="MANDATE_COMPLETED", status="completed", details=extra)

    async def mark_cancelled(self, mandate_id: str, reason: str = "") -> None:
        """Transition mandate to cancelled status."""
        update_mandate_status(mandate_id, "cancelled", {"agent_reasoning": reason} if reason else None)
        log_transaction(mandate_id=mandate_id, event_type="MANDATE_CANCELLED", status="cancelled", details={"reason": reason})

    async def mark_failed(self, mandate_id: str, error: str = "") -> None:
        """Transition mandate to failed status."""
        update_mandate_status(mandate_id, "failed", {"agent_reasoning": error} if error else None)
        log_transaction(mandate_id=mandate_id, event_type="MANDATE_FAILED", status="failed", details={"error": error})

    # ------------------------------------------------------------------
    # Transactions
    # ------------------------------------------------------------------

    async def get_transactions(self, mandate_id: str, user_id: str) -> list[dict[str, Any]]:
        """Get all transactions for a mandate (after verifying ownership)."""
        # Verify mandate belongs to user
        mandate = await self.get_mandate(mandate_id, user_id)
        if not mandate:
            return []

        result = (
            self.sb.table("transactions")
            .select("*")
            .eq("mandate_id", mandate_id)
            .order("created_at", desc=True)
            .execute()
        )
        return result.data or []

    async def get_all_user_transactions(self, user_id: str) -> list[dict[str, Any]]:
        """Get all transactions across all mandates belonging to the user."""
        mandates = await self.get_mandates(user_id)
        if not mandates:
            return []
        mandate_ids = [m["id"] for m in mandates if "id" in m]
        if not mandate_ids:
            return []
        result = (
            self.sb.table("transactions")
            .select("*")
            .in_("mandate_id", mandate_ids)
            .order("created_at", desc=True)
            .execute()
        )
        return result.data or []
