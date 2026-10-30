"""
Vault routes — PayPal Payment Method Tokens (Vault v3) setup and management.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status

from backend.models.schemas import VaultConfirmRequest, VaultSetupRequest, VaultSetupResponse
from backend.routes.auth import get_current_user
from backend.services.paypal_service import PayPalService
from backend.services.supabase_client import get_supabase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/vault", tags=["vault"])


@router.post("/setup", response_model=VaultSetupResponse)
async def setup_vault(
    payload: VaultSetupRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> VaultSetupResponse:
    """
    Create a PayPal Vault v3 setup token to let the user save their
    PayPal wallet or payment method.
    """
    try:
        paypal = PayPalService()
        result = await paypal.create_setup_token(
            return_url=payload.return_url,
            cancel_url=payload.cancel_url,
        )

        setup_token_id = result.get("id", "")
        approval_url = ""

        # Extract approval URL from links
        for link in result.get("links", []):
            if link.get("rel") in ("approve", "payer-action"):
                approval_url = link.get("href", "")
                break

        if not approval_url:
            # Fallback if direct link not labeled 'approve'
            for link in result.get("links", []):
                if "approve" in link.get("href", "").lower():
                    approval_url = link.get("href", "")
                    break

        return VaultSetupResponse(
            setup_token_id=setup_token_id,
            approval_url=approval_url,
        )
    except Exception as exc:
        logger.error("Failed to create vault setup token: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"PayPal Vault setup failed: {str(exc)}",
        )


@router.post("/confirm")
async def confirm_vault(
    payload: VaultConfirmRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """
    Exchange an approved setup token for a permanent payment method token,
    and persist it in Supabase `payment_tokens`.
    """
    try:
        paypal = PayPalService()
        token_data = await paypal.create_payment_token(payload.setup_token_id)
        payment_token_id = token_data.get("id", "")

        # Extract details like payment source type or email
        payment_source = token_data.get("payment_source", {})
        source_type = "paypal"
        display_name = "PayPal Wallet"

        if "paypal" in payment_source:
            source_type = "paypal"
            payer_email = payment_source["paypal"].get("email_address", "")
            display_name = f"PayPal ({payer_email})" if payer_email else "PayPal Wallet"
        elif "card" in payment_source:
            source_type = "card"
            last_digits = payment_source["card"].get("last_digits", "****")
            brand = payment_source["card"].get("brand", "Card")
            display_name = f"{brand} •••• {last_digits}"

        sb = get_supabase()
        db_payload = {
            "user_id": current_user["id"],
            "paypal_token_id": payment_token_id,
            "payment_source_type": source_type,
            "display_name": display_name,
            "is_active": True,
        }

        insert_res = sb.table("payment_tokens").insert(db_payload).execute()
        saved_record = insert_res.data[0] if insert_res.data else db_payload

        return {
            "message": "Payment method successfully saved",
            "token": saved_record,
        }
    except Exception as exc:
        logger.error("Failed to confirm vault payment token: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save payment method: {str(exc)}",
        )


@router.get("/tokens")
async def list_vault_tokens(
    current_user: dict[str, Any] = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """List all saved payment methods for the authenticated user."""
    sb = get_supabase()
    try:
        res = (
            sb.table("payment_tokens")
            .select("*")
            .eq("user_id", current_user["id"])
            .eq("is_active", True)
            .order("created_at", desc=True)
            .execute()
        )
        return res.data or []
    except Exception as exc:
        logger.error("Failed to list vault tokens: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve payment tokens",
        )


@router.delete("/tokens/{token_id}")
async def delete_vault_token(
    token_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, str]:
    """Deactivate or delete a saved payment method."""
    sb = get_supabase()
    try:
        # Soft delete / set is_active=false
        res = (
            sb.table("payment_tokens")
            .update({"is_active": False})
            .eq("id", token_id)
            .eq("user_id", current_user["id"])
            .execute()
        )
        return {"message": "Payment method removed"}
    except Exception as exc:
        logger.error("Failed to delete token %s: %s", token_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete payment token",
        )
