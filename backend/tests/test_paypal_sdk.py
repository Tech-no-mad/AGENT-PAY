"""Exercise real SDK serialization and responses without contacting PayPal."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5

import pytest
import requests

from backend.config import settings
from backend.services import paypal_service
from backend.services.agent_orchestrator import AgentOrchestrator
from backend.services.mandate_lifecycle import PaymentOutcomeUnknown
from backend.tests.fake_supabase import FakeSupabase


@pytest.fixture
def sandbox(monkeypatch):
    """Stub only HTTP delivery; use the installed SDK models and controllers."""
    state = SimpleNamespace(requests=[], create_status="COMPLETED", failure=None)
    monkeypatch.setattr(settings, "PAYPAL_CLIENT_ID", "sdk-test-client")
    monkeypatch.setattr(settings, "PAYPAL_CLIENT_SECRET", "sdk-test-secret")

    def send(session, request, **kwargs):
        state.requests.append(request)
        assert urlsplit(request.url).netloc == "api-m.sandbox.paypal.com"
        path = urlsplit(request.url).path
        status = 201
        if path == "/v1/oauth2/token":
            payload = {"access_token": "sdk-test-access-token", "token_type": "Bearer", "expires_in": 3600}
            status = 200
        elif path == "/v2/checkout/orders":
            if state.failure:
                raise state.failure
            payload = {"id": "order", "status": state.create_status}
            if state.create_status == "COMPLETED" and request.headers.get("Prefer") == "return=representation":
                payload["purchase_units"] = [{"payments": {"authorizations": [{"id": "held-auth", "status": "CREATED"}]}}]
        elif path == "/v2/checkout/orders/order/authorize":
            payload = {"id": "order", "status": "COMPLETED"}
            if request.headers.get("Prefer") == "return=representation":
                payload["purchase_units"] = [{"payments": {"authorizations": [{"id": "held-auth", "status": "CREATED"}]}}]
        elif path == "/v2/payments/authorizations/held-auth/capture":
            payload = {"id": "capture", "status": "COMPLETED"}
        else:
            pytest.fail(f"Unexpected PayPal request: {request.method} {path}")
        response = requests.Response()
        response.status_code = status
        response.headers["Content-Type"] = "application/json"
        response._content = json.dumps(payload).encode()
        response.request = request
        response.url = request.url
        return response

    monkeypatch.setattr(requests.Session, "send", send)
    mandate = {"id": "mandate", "user_id": "buyer", "payment_token_id": "db-token",
               "description": "Headphones", "constraints": {"max_price": 100, "currency": "USD",
               "category": "electronics", "auto_execute": True}}
    database = FakeSupabase(mandate)
    state.mandate = mandate
    state.agent = AgentOrchestrator(AsyncMock(), paypal_service.PayPalService(), database)
    yield state
    database.connection.close()


def purchase():
    return {"amount": 50, "currency": "USD", "category": "electronics", "product_name": "Headphones"}


@pytest.mark.parametrize("create_status", ["COMPLETED", "APPROVED"])
def test_wallet_authorization_and_capture_through_actual_sdk(sandbox, create_status):
    sandbox.create_status = create_status
    result = {}
    response = asyncio.run(sandbox.agent._create_order(purchase(), sandbox.mandate, result))
    assert response["authorization_id"] == "held-auth"
    stored = sandbox.agent.lifecycle.current("mandate")
    assert stored["status"] == "authorized"
    assert stored["paypal_order_id"] == "order"
    assert stored["paypal_authorization_id"] == "held-auth"
    assert stored["payment_operation_id"] is None

    create = next(r for r in sandbox.requests if urlsplit(r.url).path == "/v2/checkout/orders")
    body = json.loads(create.body)
    assert create.method == "POST"
    assert body["intent"] == "AUTHORIZE"
    assert body["payment_source"] == {"paypal": {"vault_id": "vault-id"}}
    assert body["purchase_units"] == [{"amount": {"currency_code": "USD", "value": "50.00"},
                                       "description": "Headphones", "custom_id": "mandate"}]
    operation_id = create.headers["PayPal-Request-Id"]
    assert operation_id
    assert create.headers["Prefer"] == "return=representation"
    if create_status == "APPROVED":
        authorize = next(r for r in sandbox.requests if r.url.endswith("/authorize"))
        assert authorize.method == "POST"
        assert authorize.headers["PayPal-Request-Id"] == str(uuid5(NAMESPACE_URL, operation_id + ":authorize"))
        assert authorize.headers["Prefer"] == "return=representation"
    else:
        assert not any(r.url.endswith("/authorize") for r in sandbox.requests)
    assert not any(r.url.endswith("/capture") for r in sandbox.requests)

    captured = asyncio.run(sandbox.agent._capture({"authorization_id": "held-auth"}, sandbox.mandate, result))
    assert captured == {"capture_id": "capture", "status": "CAPTURED"}
    capture = sandbox.requests[-1]
    assert capture.method == "POST"
    assert urlsplit(capture.url).path == "/v2/payments/authorizations/held-auth/capture"
    assert capture.headers["PayPal-Request-Id"] != operation_id
    assert sandbox.agent.lifecycle.current("mandate")["status"] == "completed"


def test_order_model_error_is_failed_without_leaving_a_payment_in_flight(sandbox, monkeypatch):
    def invalid_model(*args, **kwargs):
        raise TypeError("Unexpected SDK constructor argument")

    monkeypatch.setattr(paypal_service, "OrderRequest", invalid_model)
    result = {}
    response = asyncio.run(sandbox.agent._create_order(purchase(), sandbox.mandate, result))
    assert "Unexpected SDK constructor argument" in response["error"]
    assert result["status"] == "failed"
    assert not sandbox.requests
    stored = sandbox.agent.lifecycle.current("mandate")
    assert stored["status"] == "failed"
    assert stored["payment_operation_id"] is None
    assert stored["payment_operation"] is None
    assert stored["payment_started_at"] is None
    assert stored["paypal_order_id"] is None
    audit = sandbox.agent.sb.table("transactions").select("*").eq("event_type", "failed").execute().data
    assert len(audit) == 1
    assert audit[0]["details"]["status"] == "failed"
    assert audit[0]["details"]["original_event"] == "ORDER_FAILED"
    assert audit[0]["details"]["request_sent"] is False


@pytest.mark.parametrize("failure", [requests.Timeout("Response timed out"), TypeError("Invalid provider response")])
def test_error_after_sending_order_still_requires_reconciliation(sandbox, failure):
    sandbox.failure = failure
    with pytest.raises(PaymentOutcomeUnknown):
        asyncio.run(sandbox.agent._create_order(purchase(), sandbox.mandate, {}))
    assert sandbox.requests[-1].url.endswith("/v2/checkout/orders")
    stored = sandbox.agent.lifecycle.current("mandate")
    assert stored["payment_operation_id"]
    assert stored["payment_operation"] == "authorize"
    audit = sandbox.agent.sb.table("transactions").select("*").eq("event_type", "failed").execute().data
    assert audit[0]["details"]["status"] == "unknown"
