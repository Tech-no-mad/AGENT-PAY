"""
Mandate Service — business logic for creating, listing, and managing mandates.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from backend.models.schemas import MandateConstraints

from backend.services.agent_orchestrator import AgentOrchestrator
from backend.services.cloudflare_ai import CloudflareAI
from backend.services.mandate_lifecycle import MandateLifecycle
from backend.services.supabase_client import log_transaction, update_mandate_status, resolve_payment_token

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
        self.lifecycle = MandateLifecycle(self.sb, self.orchestrator.paypal)

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
        Parse instructions, merge explicit overrides, validate the mandate,
        and kick off the agent execution in the background.
        """
        resolve_payment_token(self.sb, payment_token_id, user_id)

        parsed = await self.ai.parse_mandate(description)
        constraints = MandateConstraints.model_validate({
            **parsed, **{key: value for key, value in (constraints or {}).items() if value is not None},
        }).model_dump()
        if constraints["max_price"] is None:
            raise ValueError("A positive budget is required in the instructions or explicit constraints")

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
            status="active",
            details={"description": description},
            user_id=user_id,
            supabase_client=self.sb,
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
                supabase_client=self.sb,
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

        return self.lifecycle.cancel(mandate_id, user_id)

    # ------------------------------------------------------------------
    # Status transitions
    # ------------------------------------------------------------------

    async def mark_completed(self, mandate_id: str, details: Optional[dict[str, Any]] = None) -> None:
        """Transition mandate to completed status."""
        extra = details or {}
        update_mandate_status(mandate_id, "completed", extra, supabase_client=self.sb)
        log_transaction(mandate_id=mandate_id, event_type="MANDATE_COMPLETED", status="completed", details=extra, supabase_client=self.sb)

    async def mark_cancelled(self, mandate_id: str, reason: str = "") -> None:
        """Use the same confirmed-release path as user and agent cancellation."""
        mandate = self.lifecycle.current(mandate_id)
        self.lifecycle.cancel(mandate_id, mandate["user_id"])

    async def mark_failed(self, mandate_id: str, error: str = "") -> None:
        """Transition mandate to failed status."""
        update_mandate_status(mandate_id, "failed", {"agent_reasoning": error} if error else None, supabase_client=self.sb)
        log_transaction(mandate_id=mandate_id, event_type="MANDATE_FAILED", status="failed", details={"error": error}, supabase_client=self.sb)

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
