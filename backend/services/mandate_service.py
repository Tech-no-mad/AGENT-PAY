"""
Mandate Service — business logic for creating, listing, and managing mandates.
"""

from __future__ import annotations

import asyncio
import logging
import math
from typing import Any, Optional

from backend.services.agent_orchestrator import AgentOrchestrator, normalize_category
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
        self._active_tasks: dict[str, asyncio.Task] = {}

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
        Create a new mandate, merge parsed instructions with partial explicit
        constraints, fail closed if spending limit is missing, and kick off the
        agent execution in the background.
        """
        resolve_payment_token(self.sb, payment_token_id, user_id)

        # Parse constraints from natural language instruction
        parsed_constraints = await self.ai.parse_mandate(description)
        merged_constraints = dict(parsed_constraints or {})

        # Merge explicit constraints on top of parsed constraints
        if constraints:
            for k, v in constraints.items():
                if v is not None:
                    merged_constraints[k] = v

        # Fail closed: mandate MUST have a finite positive spending limit
        max_price = merged_constraints.get("max_price")
        if (
            max_price is None
            or isinstance(max_price, bool)
            or not isinstance(max_price, (int, float))
            or max_price <= 0
            or math.isnan(max_price)
            or math.isinf(max_price)
        ):
            raise ValueError(
                "A valid positive spending limit (max_price) must be specified or extractable from the instruction."
            )

        # Normalize category
        if merged_constraints.get("category"):
            merged_constraints["category"] = normalize_category(merged_constraints["category"])

        mandate_row = {
            "user_id": user_id,
            "description": description,
            "payment_token_id": payment_token_id,
            "status": "active",
            "constraints": merged_constraints,
        }

        result = self.sb.table("mandates").insert(mandate_row).execute()
        mandate = result.data[0] if result.data else mandate_row
        mandate_id = mandate.get("id", "")

        logger.info("Mandate created: %s for user %s", mandate_id, user_id)

        log_transaction(
            mandate_id=mandate_id,
            user_id=user_id,
            event_type="mandate_created",
            status="active",
            details={"description": description},
        )

        # Launch background task and track it for cancellation
        task = asyncio.create_task(self._run_agent(mandate))
        if mandate_id:
            self._active_tasks[mandate_id] = task

        return mandate

    async def _run_agent(self, mandate: dict[str, Any]) -> None:
        """Background task that runs the agent orchestrator."""
        mandate_id = mandate.get("id", "")
        try:
            await self.orchestrator.execute_mandate(mandate)
        except asyncio.CancelledError:
            logger.info("Background execution for mandate %s was cancelled", mandate_id)
        except Exception as exc:
            logger.exception("Background agent execution failed for mandate %s", mandate_id)
            update_mandate_status(
                mandate_id,
                "failed",
                {"agent_reasoning": f"Background execution error: {exc}"},
            )
        finally:
            if mandate_id:
                self._active_tasks.pop(mandate_id, None)

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
        Cancel a mandate. Stop execution worker, void authorization if present,
        and fail cancellation if voiding fails.
        """
        mandate = await self.get_mandate(mandate_id, user_id)
        if not mandate:
            return None

        current_status = mandate.get("status", "")
        if current_status in ("completed", "cancelled", "failed"):
            logger.warning("Cannot cancel mandate %s — already %s", mandate_id, current_status)
            return mandate

        # 1. Stop background execution immediately (BUG-10)
        self.orchestrator.cancel_mandate(mandate_id)
        active_task = self._active_tasks.get(mandate_id)
        if active_task and not active_task.done():
            active_task.cancel()

        # 2. Void authorization if one exists (BUG-14)
        authorization_id = mandate.get("paypal_authorization_id")
        if authorization_id:
            try:
                from backend.services.paypal_service import PayPalService
                paypal = PayPalService()
                paypal.void_authorization(authorization_id)
                log_transaction(
                    mandate_id=mandate_id,
                    user_id=user_id,
                    event_type="voided",
                    paypal_resource_id=authorization_id,
                    paypal_authorization_id=authorization_id,
                    status="voided",
                    details={"reason": "mandate_cancelled"},
                )
            except Exception as exc:
                logger.error("Failed to void authorization %s: %s", authorization_id, exc)
                log_transaction(
                    mandate_id=mandate_id,
                    user_id=user_id,
                    event_type="failed",
                    paypal_resource_id=authorization_id,
                    paypal_authorization_id=authorization_id,
                    status="failed",
                    details={"error": f"Failed to void PayPal authorization: {exc}", "reason": "cancel_failed"},
                )
                raise RuntimeError(f"Failed to void PayPal authorization {authorization_id}: {exc}") from exc

        # 3. Confirm cancellation only after successful void or when no hold exists
        updated = update_mandate_status(mandate_id, "cancelled")
        log_transaction(
            mandate_id=mandate_id,
            user_id=user_id,
            event_type="voided",
            status="cancelled",
            details={"reason": "mandate_cancelled"},
        )
        return updated

    # ------------------------------------------------------------------
    # Status transitions
    # ------------------------------------------------------------------

    async def mark_completed(self, mandate_id: str, details: Optional[dict[str, Any]] = None) -> None:
        """Transition mandate to completed status."""
        extra = details or {}
        update_mandate_status(mandate_id, "completed", extra)
        log_transaction(mandate_id=mandate_id, event_type="captured", status="completed", details=extra)

    async def mark_cancelled(self, mandate_id: str, reason: str = "") -> None:
        """Transition mandate to cancelled status."""
        update_mandate_status(mandate_id, "cancelled", {"agent_reasoning": reason} if reason else None)
        log_transaction(mandate_id=mandate_id, event_type="voided", status="cancelled", details={"reason": reason})

    async def mark_failed(self, mandate_id: str, error: str = "") -> None:
        """Transition mandate to failed status."""
        update_mandate_status(mandate_id, "failed", {"agent_reasoning": error} if error else None)
        log_transaction(mandate_id=mandate_id, event_type="failed", status="failed", details={"error": error})

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
