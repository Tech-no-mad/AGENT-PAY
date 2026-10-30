"""
Mandate routes — create, list, view, and cancel spending mandates.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status

from backend.config import settings
from backend.models.schemas import MandateCreate, MandateResponse, TransactionResponse
from backend.routes.auth import get_current_user
from backend.services.agent_orchestrator import AgentOrchestrator
from backend.services.cloudflare_ai import CloudflareAI
from backend.services.mandate_service import MandateService
from backend.services.paypal_service import PayPalService
from backend.services.supabase_client import get_supabase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mandates", tags=["mandates"])


def get_mandate_service() -> MandateService:
    """Dependency injection helper for MandateService."""
    sb = get_supabase()
    cf_ai = CloudflareAI(
        account_id=settings.CLOUDFLARE_ACCOUNT_ID,
        api_token=settings.CLOUDFLARE_API_TOKEN,
    )
    paypal = PayPalService()
    orchestrator = AgentOrchestrator(
        cloudflare_ai=cf_ai,
        paypal_service=paypal,
        supabase_client=sb,
    )
    return MandateService(
        supabase_client=sb,
        agent_orchestrator=orchestrator,
        cloudflare_ai=cf_ai,
    )


@router.post("", response_model=dict[str, Any])
async def create_mandate(
    payload: MandateCreate,
    current_user: dict[str, Any] = Depends(get_current_user),
    service: MandateService = Depends(get_mandate_service),
) -> dict[str, Any]:
    """
    Create a new spending mandate. The natural language description is parsed
    by Cloudflare AI into structured policy constraints, and the autonomous
    agent is kicked off in the background.
    """
    try:
        constraints_dict = payload.constraints.model_dump(exclude_none=True) if payload.constraints else None
        mandate = await service.create_mandate(
            user_id=current_user["id"],
            description=payload.description,
            payment_token_id=payload.payment_token_id,
            constraints=constraints_dict,
        )
        return mandate
    except Exception as exc:
        logger.error("Error creating mandate: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create mandate: {str(exc)}",
        )


@router.get("", response_model=list[dict[str, Any]])
async def list_mandates(
    current_user: dict[str, Any] = Depends(get_current_user),
    service: MandateService = Depends(get_mandate_service),
) -> list[dict[str, Any]]:
    """List all mandates for the authenticated user."""
    try:
        return await service.get_mandates(current_user["id"])
    except Exception as exc:
        logger.error("Error listing mandates: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch mandates",
        )


@router.get("/{mandate_id}", response_model=dict[str, Any])
async def get_mandate(
    mandate_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
    service: MandateService = Depends(get_mandate_service),
) -> dict[str, Any]:
    """Retrieve details of a single mandate."""
    mandate = await service.get_mandate(mandate_id, current_user["id"])
    if not mandate:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Mandate not found",
        )
    return mandate


@router.post("/{mandate_id}/cancel", response_model=dict[str, Any])
async def cancel_mandate(
    mandate_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
    service: MandateService = Depends(get_mandate_service),
) -> dict[str, Any]:
    """
    Cancel an active mandate. If funds are currently authorized on PayPal,
    the authorization is automatically voided.
    """
    cancelled = await service.cancel_mandate(mandate_id, current_user["id"])
    if not cancelled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Mandate not found or already completed/cancelled",
        )
    return cancelled


@router.get("/transactions/all", response_model=list[dict[str, Any]])
async def get_all_transactions(
    current_user: dict[str, Any] = Depends(get_current_user),
    service: MandateService = Depends(get_mandate_service),
) -> list[dict[str, Any]]:
    """Get all audit transactions across all user mandates."""
    return await service.get_all_user_transactions(current_user["id"])


@router.get("/{mandate_id}/transactions", response_model=list[dict[str, Any]])
async def get_mandate_transactions(
    mandate_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
    service: MandateService = Depends(get_mandate_service),
) -> list[dict[str, Any]]:
    """Get the audit log of all transactions and agent actions for a mandate."""
    return await service.get_transactions(mandate_id, current_user["id"])
