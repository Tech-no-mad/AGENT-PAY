"""
PayPal Service — wraps the paypal-server-sdk for Orders v2 and Vault v3.

Uses the SDK controllers where available, and falls back to httpx + OAuth
token for Vault v3 endpoints not yet exposed by the SDK.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import httpx
from paypalserversdk.http.auth.o_auth_2 import ClientCredentialsAuthCredentials
from paypalserversdk.logging.configuration.api_logging_configuration import (
    LoggingConfiguration,
    RequestLoggingConfiguration,
    ResponseLoggingConfiguration,
)
from paypalserversdk.models.amount_with_breakdown import AmountWithBreakdown
from paypalserversdk.models.checkout_payment_intent import CheckoutPaymentIntent
from paypalserversdk.models.order_request import OrderRequest
from paypalserversdk.models.payment_source import PaymentSource
from paypalserversdk.models.token import Token
from paypalserversdk.models.purchase_unit_request import PurchaseUnitRequest
from paypalserversdk.paypalserversdk_client import PaypalserversdkClient

from backend.config import settings

logger = logging.getLogger(__name__)


class PayPalService:
    """High-level wrapper around PayPal SDK + REST endpoints."""

    SANDBOX_BASE = "https://api-m.sandbox.paypal.com"

    def __init__(self) -> None:
        self.client = PaypalserversdkClient(
            client_credentials_auth_credentials=ClientCredentialsAuthCredentials(
                o_auth_client_id=settings.PAYPAL_CLIENT_ID,
                o_auth_client_secret=settings.PAYPAL_CLIENT_SECRET,
            ),
            logging_configuration=LoggingConfiguration(
                log_level=logging.INFO,
                mask_sensitive_headers=True,
                request_logging_config=RequestLoggingConfiguration(
                    log_body=True,
                ),
                response_logging_config=ResponseLoggingConfiguration(
                    log_body=True,
                ),
            ),
            environment="sandbox",
        )
        self._orders_controller = self.client.orders
        self._payments_controller = self.client.payments
        logger.info("PayPal SDK client initialised (sandbox)")

    # ------------------------------------------------------------------
    # Internal: get an OAuth bearer token for raw REST calls
    # ------------------------------------------------------------------

    async def _get_access_token(self) -> str:
        """Obtain an OAuth2 access token from the SDK's auth manager."""
        auth_manager = self.client.auth_managers.get("httpBasic") or self.client.auth_managers.get("Oauth2")
        # The SDK caches tokens internally; we fetch from its credential store
        if hasattr(self.client, "config") and hasattr(self.client.config, "client_credentials_auth_credentials"):
            async with httpx.AsyncClient() as http:
                resp = await http.post(
                    f"{self.SANDBOX_BASE}/v1/oauth2/token",
                    data={"grant_type": "client_credentials"},
                    auth=(settings.PAYPAL_CLIENT_ID, settings.PAYPAL_CLIENT_SECRET),
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                resp.raise_for_status()
                return resp.json()["access_token"]
        raise RuntimeError("Unable to obtain PayPal access token")

    async def _auth_headers(self) -> dict[str, str]:
        token = await self._get_access_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------
    # Vault v3 — setup tokens & payment tokens (httpx)
    # ------------------------------------------------------------------

    async def create_setup_token(self, return_url: str, cancel_url: str) -> dict[str, Any]:
        """Create a Vault v3 setup token for PayPal wallet vaulting."""
        headers = await self._auth_headers()
        body = {
            "payment_source": {
                "paypal": {
                    "usage_type": "MERCHANT",
                    "experience_context": {
                        "return_url": return_url,
                        "cancel_url": cancel_url,
                        "shipping_preference": "NO_SHIPPING",
                        "vault_instruction": "ON_CREATE_PAYMENT_TOKENS",
                    },
                }
            },
        }
        async with httpx.AsyncClient() as http:
            resp = await http.post(
                f"{self.SANDBOX_BASE}/v3/vault/setup-tokens",
                json=body,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
            logger.info("Created setup token %s", data.get("id"))
            return data

    async def create_payment_token(self, setup_token_id: str) -> dict[str, Any]:
        """Exchange a setup token for a permanent payment token."""
        headers = await self._auth_headers()
        body = {"payment_source": {"token": {"id": setup_token_id, "type": "SETUP_TOKEN"}}}
        async with httpx.AsyncClient() as http:
            resp = await http.post(
                f"{self.SANDBOX_BASE}/v3/vault/payment-tokens",
                json=body,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
            logger.info("Created payment token %s", data.get("id"))
            return data

    # ------------------------------------------------------------------
    # Orders v2 — create, authorize, capture, void (SDK controllers)
    # ------------------------------------------------------------------

    def create_authorized_order(
        self,
        amount: float,
        currency: str,
        description: str,
        payment_token_id: str,
        mandate_id: str,
    ) -> Any:
        """Create an order with AUTHORIZE intent using a vaulted payment token."""
        order_body = OrderRequest(
            intent=CheckoutPaymentIntent.AUTHORIZE,
            purchase_units=[
                PurchaseUnitRequest(
                    amount=AmountWithBreakdown(
                        currency_code=currency,
                        value=f"{amount:.2f}",
                    ),
                    description=description,
                    custom_id=mandate_id,
                )
            ],
            payment_source=PaymentSource(
                token=Token(
                    id=payment_token_id,
                    type="PAYMENT_METHOD_TOKEN",
                ),
            ),
        )
        try:
            result = self._orders_controller.orders_create({"body": order_body})
            logger.info("Order created: %s", result.body.id if result.body else "unknown")
            return result.body
        except Exception as exc:
            logger.error("Failed to create order: %s", exc)
            raise

    def authorize_order(self, order_id: str) -> Any:
        """Authorize a created order."""
        try:
            result = self._orders_controller.orders_authorize({"id": order_id})
            logger.info("Order authorized: %s", order_id)
            return result.body
        except Exception as exc:
            logger.error("Failed to authorize order %s: %s", order_id, exc)
            raise

    def capture_authorization(self, authorization_id: str) -> Any:
        """Capture a previously authorized payment."""
        try:
            result = self._payments_controller.authorizations_capture(
                {"authorization_id": authorization_id}
            )
            logger.info("Authorization captured: %s", authorization_id)
            return result.body
        except Exception as exc:
            logger.error("Failed to capture authorization %s: %s", authorization_id, exc)
            raise

    def void_authorization(self, authorization_id: str) -> Any:
        """Void (release) an authorization."""
        try:
            result = self._payments_controller.authorizations_void(
                {"authorization_id": authorization_id}
            )
            logger.info("Authorization voided: %s", authorization_id)
            return result.body
        except Exception as exc:
            logger.error("Failed to void authorization %s: %s", authorization_id, exc)
            raise

    def get_order(self, order_id: str) -> Any:
        """Get order details."""
        try:
            result = self._orders_controller.orders_get({"id": order_id})
            return result.body
        except Exception as exc:
            logger.error("Failed to get order %s: %s", order_id, exc)
            raise
