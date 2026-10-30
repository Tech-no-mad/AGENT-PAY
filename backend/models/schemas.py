"""
AgentPay Pydantic Models / Schemas.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Mandate Constraints
# ---------------------------------------------------------------------------

class MandateConstraints(BaseModel):
    """Structured spending constraints extracted from natural-language mandates."""

    max_price: Optional[float] = Field(None, description="Maximum allowed price")
    currency: str = Field("USD", description="ISO 4217 currency code")
    category: Optional[str] = Field(None, description="Product/service category")
    deadline: Optional[str] = Field(None, description="ISO-8601 deadline for the purchase")
    auto_execute: bool = Field(True, description="Automatically capture after authorization")
    additional_rules: Optional[str] = Field(None, description="Free-text rules the agent must respect")


# ---------------------------------------------------------------------------
# Mandate
# ---------------------------------------------------------------------------

class MandateCreate(BaseModel):
    """Payload for creating a new mandate."""

    description: str = Field(..., min_length=1, description="Natural-language spending instruction")
    payment_token_id: str = Field(..., description="Database UUID of an active payment token owned by the user")
    constraints: Optional[MandateConstraints] = Field(None, description="Optional explicit constraints")


class MandateResponse(BaseModel):
    """Full mandate record returned to the client."""

    id: str
    user_id: str
    description: str
    payment_token_id: str
    status: str
    constraints: Optional[dict[str, Any]] = None
    agent_reasoning: Optional[str] = None
    order_id: Optional[str] = None
    authorization_id: Optional[str] = None
    amount: Optional[float] = None
    currency: Optional[str] = None
    product_name: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


# ---------------------------------------------------------------------------
# Vault
# ---------------------------------------------------------------------------

class VaultSetupRequest(BaseModel):
    """Request to create a PayPal vault setup token."""

    return_url: str = Field(..., description="URL PayPal redirects to after approval")
    cancel_url: str = Field(..., description="URL PayPal redirects to on cancellation")


class VaultSetupResponse(BaseModel):
    """Response with the setup token + approval URL."""

    setup_token_id: str
    approval_url: str


class VaultConfirmRequest(BaseModel):
    """Confirm a vault setup by exchanging setup token for payment token."""

    setup_token_id: str


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------

class TransactionResponse(BaseModel):
    """A single transaction log entry."""

    id: str
    mandate_id: str
    event_type: str
    paypal_order_id: Optional[str] = None
    paypal_authorization_id: Optional[str] = None
    paypal_capture_id: Optional[str] = None
    amount: Optional[float] = None
    currency: Optional[str] = None
    status: Optional[str] = None
    details: Optional[dict[str, Any]] = None
    created_at: Optional[str] = None


# ---------------------------------------------------------------------------
# Agent Execution
# ---------------------------------------------------------------------------

class AgentExecutionResult(BaseModel):
    """Result returned after the AI agent processes a mandate."""

    mandate_id: str
    status: str
    agent_reasoning: Optional[str] = None
    order_id: Optional[str] = None
    authorization_id: Optional[str] = None
    capture_id: Optional[str] = None
    amount: Optional[float] = None
    currency: Optional[str] = None
    product_name: Optional[str] = None
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

class SignUpRequest(BaseModel):
    email: str
    password: str
    full_name: Optional[str] = None


class LoginRequest(BaseModel):
    email: str
    password: str


class AuthResponse(BaseModel):
    access_token: str
    refresh_token: Optional[str] = None
    user: Optional[dict[str, Any]] = None
