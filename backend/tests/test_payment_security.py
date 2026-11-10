"""Regression tests for the five prioritized payment security findings."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.config import settings
from backend.routes import auth, webhooks
from backend.services import supabase_client as db
from backend.services.agent_orchestrator import AgentOrchestrator
from backend.services.mandate_service import MandateService
from backend.services.paypal_service import PayPalService
from backend.services.mandate_lifecycle import ExecutionStopped, PaymentOutcomeUnknown
from backend.tests.fake_supabase import FakeSupabase


def run(coroutine):
    return asyncio.run(coroutine)


@pytest.fixture
def mandate():
    return {"id": "mandate", "user_id": "buyer", "payment_token_id": "db-token",
            "description": "Headphones", "constraints": {"max_price": 100, "currency": "USD",
            "category": "electronics", "auto_execute": True}}


@pytest.fixture
def agent(mandate):
    instance = AgentOrchestrator(AsyncMock(), MagicMock(), FakeSupabase(mandate))
    with patch("backend.services.agent_orchestrator.log_transaction"), patch("backend.services.mandate_lifecycle.log_transaction"):
        yield instance


def purchase(**overrides):
    return {"amount": 50, "currency": "USD", "category": "electronics",
            "product_name": "Headphones", **overrides}


@pytest.mark.parametrize("overrides", [
    {"amount": 101}, {"amount": 0}, {"amount": -1}, {"amount": float("nan")},
    {"amount": float("inf")}, {"amount": True}, {"amount": "50"},
    {"currency": "EUR"}, {"category": "food"}, {"category": ""},
])
def test_authorization_rechecks_actual_purchase(agent, mandate, overrides):
    with pytest.raises(ExecutionStopped):
        run(agent._create_order(purchase(**overrides), mandate, {}))
    assert agent.lifecycle.current(mandate["id"])["status"] == "cancelled"
    agent.paypal.create_authorized_order.assert_not_called()


@pytest.mark.parametrize("constraint", [{"max_price": None}, {"max_price": float("nan")},
    {"deadline": "invalid"}, {"deadline": "2000-01-01T00:00:00Z"}])
def test_invalid_or_expired_constraints_fail_closed(agent, mandate, constraint):
    mandate["constraints"].update(constraint)
    with pytest.raises(ExecutionStopped):
        run(agent._create_order(purchase(), mandate, {}))
    assert agent.lifecycle.current(mandate["id"])["status"] == "cancelled"
    agent.paypal.create_authorized_order.assert_not_called()


def test_changed_amount_after_policy_approval_is_rejected(agent, mandate):
    assert agent._check_policy({**purchase(), "price": 50}, mandate)["decision"] == "APPROVED"
    with pytest.raises(ExecutionStopped):
        run(agent._create_order(purchase(amount=150), mandate, {}))
    assert agent.lifecycle.current(mandate["id"])["status"] == "cancelled"
    agent.paypal.create_authorized_order.assert_not_called()


def authorized_order():
    return SimpleNamespace(id="order", status="COMPLETED", purchase_units=[
        SimpleNamespace(payments=SimpleNamespace(authorizations=[SimpleNamespace(id="owned-auth")]))])


def test_valid_purchase_resolves_vault_identifier_and_persists_schema(agent, mandate):
    agent.paypal.create_authorized_order.return_value = authorized_order()
    result = {}
    with patch("backend.services.agent_orchestrator.resolve_payment_token", return_value={"paypal_token_id": "vault-id"}) as resolve:
        run(agent._create_order(purchase(), mandate, result))
    resolve.assert_called_once_with(agent.sb, "db-token", "buyer")
    assert agent.paypal.create_authorized_order.call_args.kwargs["payment_token_id"] == "vault-id"
    assert result["status"] == "authorized"
    stored = agent.lifecycle.current(mandate["id"])
    assert stored["paypal_order_id"] == "order"
    assert stored["paypal_authorization_id"] == "owned-auth"
    assert stored["executed_amount"] == 50
    assert "error" in run(agent._create_order(purchase(), mandate, result))
    assert agent.paypal.create_authorized_order.call_count == 1


def test_removed_token_cannot_be_used_at_execution(agent, mandate):
    with patch("backend.services.agent_orchestrator.resolve_payment_token", side_effect=ValueError("inactive")):
        assert "error" in run(agent._execute_tool("create_authorized_order", purchase(), mandate, {}))
    agent.paypal.create_authorized_order.assert_not_called()


@pytest.mark.parametrize("automatic, supplied, state", [(False, "owned-auth", "authorized"),
    (True, "foreign-auth", "authorized"), (True, "owned-auth", "completed"),
    (None, "owned-auth", "authorized")])
def test_capture_guards(agent, mandate, automatic, supplied, state):
    mandate["constraints"]["auto_execute"] = automatic
    assert "error" in run(agent._capture({"authorization_id": supplied}, mandate,
        {"authorization_id": "owned-auth", "status": state}))
    agent.paypal.capture_authorization.assert_not_called()


@pytest.mark.parametrize("status, completed", [("COMPLETED", True), ("PENDING", False), ("DECLINED", False)])
def test_capture_requires_paypal_confirmation(agent, mandate, status, completed):
    agent.paypal.capture_authorization.return_value = {"id": "capture", "status": status}
    result = {"authorization_id": "owned-auth", "status": "authorized"}
    agent.sb.table("mandates").update({"status": "authorized", "paypal_authorization_id": "owned-auth"}).eq("id", mandate["id"]).execute()
    if status == "PENDING":
        with pytest.raises(PaymentOutcomeUnknown):
            run(agent._capture({"authorization_id": "owned-auth"}, mandate, result))
    else:
        run(agent._capture({"authorization_id": "owned-auth"}, mandate, result))
    assert (result["status"] == "completed") is completed
    assert agent.paypal.capture_authorization.call_args.args == ("owned-auth",)
    assert agent.paypal.capture_authorization.call_args.kwargs["request_id"]


def test_loop_does_not_complete_authorization_only_payment(agent, mandate, pricing_response):
    mandate["constraints"]["auto_execute"] = False
    agent.sb.table("mandates").update({"constraints": mandate["constraints"]}).eq("id", mandate["id"]).execute()
    agent.ai.chat_with_tools.side_effect = [
        pricing_response,
        {"result": {"tool_calls": [{"name": "create_authorized_order", "arguments": purchase()}]}},
        {"result": {"response": "Awaiting manual review"}},
    ]
    agent.paypal.create_authorized_order.return_value = authorized_order()
    with patch("backend.services.agent_orchestrator.resolve_payment_token", return_value={"paypal_token_id": "vault"}):
        assert run(agent.execute_mandate(mandate))["status"] == "authorized"
    agent.paypal.capture_authorization.assert_not_called()


def test_token_query_scopes_owner_and_activity():
    sb = MagicMock()
    query = sb.table.return_value
    query.select.return_value = query
    query.eq.return_value = query
    query.maybe_single.return_value = query
    query.execute.return_value.data = None
    with pytest.raises(ValueError):
        db.resolve_payment_token(sb, "foreign-or-inactive", "buyer")
    assert query.eq.call_args_list == [(("id", "foreign-or-inactive"),), (("user_id", "buyer"),), (("is_active", True),)]


def test_invalid_token_rejected_before_mandate_insert(mandate):
    sb = MagicMock()
    with patch("backend.services.mandate_service.resolve_payment_token", side_effect=ValueError("invalid")):
        with pytest.raises(ValueError):
            run(MandateService(sb, MagicMock(), AsyncMock()).create_mandate("buyer", "buy", "foreign", {}))
    sb.table.assert_not_called()


def test_auth_clients_are_fresh_and_do_not_modify_privileged_client(monkeypatch):
    monkeypatch.setattr(settings, "SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setattr(settings, "SUPABASE_ANON_KEY", "anon")
    privileged = MagicMock()
    monkeypatch.setattr(db, "_supabase_client", privileged)
    with patch.object(db, "create_client", side_effect=[MagicMock(), MagicMock()]) as create:
        assert db.get_auth_client() is not db.get_auth_client()
    assert all(call.args[1] == "anon" for call in create.call_args_list)
    assert db.get_supabase() is privileged
    privileged.auth.sign_in_with_password.assert_not_called()


def test_login_and_signup_use_request_auth_clients():
    from backend.models.schemas import LoginRequest, SignUpRequest
    with patch.object(auth, "get_auth_client") as factory:
        response = SimpleNamespace(session=SimpleNamespace(access_token="access", refresh_token="refresh"),
            user=SimpleNamespace(id="buyer", email="buyer@example.com", user_metadata={}))
        factory.return_value.auth.sign_in_with_password.return_value = response
        factory.return_value.auth.sign_up.return_value = response
        run(auth.login(LoginRequest(email="buyer@example.com", password="password")))
        run(auth.signup(SignUpRequest(email="buyer@example.com", password="password")))
    assert factory.call_count == 2


@pytest.fixture
def webhook_client(monkeypatch):
    monkeypatch.setattr(settings, "PAYPAL_WEBHOOK_ID", "configured-id")
    app = FastAPI()
    app.include_router(webhooks.router)
    return TestClient(app)


HEADERS = {"PAYPAL-AUTH-ALGO": "SHA256withRSA", "PAYPAL-TRANSMISSION-ID": "transmission",
           "PAYPAL-CERT-URL": "https://api.paypal.com/cert", "PAYPAL-TRANSMISSION-SIG": "signature",
           "PAYPAL-TRANSMISSION-TIME": "2026-10-30T00:00:00Z"}
EVENT = {"event_type": "PAYMENT.AUTHORIZATION.CREATED", "resource": {"id": "auth", "custom_id": "mandate"}}


@pytest.mark.parametrize("verified, error, expected", [(False, None, 401), (True, None, 200), (False, RuntimeError("timeout"), 503)])
def test_webhook_authentication_precedes_writes(webhook_client, verified, error, expected):
    with patch.object(webhooks, "PayPalService") as paypal, patch.object(webhooks, "update_mandate_status") as update, patch.object(webhooks, "log_transaction") as log:
        paypal.return_value.verify_webhook = AsyncMock(return_value=verified, side_effect=error)
        assert webhook_client.post("/api/webhooks/paypal", json=EVENT, headers=HEADERS).status_code == expected
        if expected != 200:
            update.assert_not_called()
            log.assert_not_called()
        else:
            update.assert_called_once()


def test_webhook_missing_headers_or_configuration_does_not_write(webhook_client, monkeypatch):
    with patch.object(webhooks, "PayPalService") as paypal, patch.object(webhooks, "update_mandate_status") as update:
        assert webhook_client.post("/api/webhooks/paypal", json=EVENT).status_code == 400
        monkeypatch.setattr(settings, "PAYPAL_WEBHOOK_ID", "")
        assert webhook_client.post("/api/webhooks/paypal", json=EVENT, headers=HEADERS).status_code == 503
        paypal.assert_not_called()
        update.assert_not_called()


def test_paypal_verification_request_contains_registered_id(monkeypatch):
    monkeypatch.setattr(settings, "PAYPAL_WEBHOOK_ID", "registered")
    service = PayPalService.__new__(PayPalService)
    service._auth_headers = AsyncMock(return_value={"Authorization": "Bearer test"})
    with patch("backend.services.paypal_service.httpx.AsyncClient") as client:
        http = client.return_value.__aenter__.return_value
        http.post = AsyncMock(return_value=httpx.Response(200, json={"verification_status": "SUCCESS"}, request=httpx.Request("POST", "https://example.com")))
        assert run(service.verify_webhook(EVENT, {"transmission_id": "tx"}))
        assert http.post.call_args.kwargs["json"] == {"transmission_id": "tx", "webhook_id": "registered", "webhook_event": EVENT}


# ============================================================================
# Regression tests for BUG-9 through BUG-15
# ============================================================================


def test_log_transaction_aligns_with_schema_and_normalizes_events(mandate):
    """BUG-9: Validate actual persisted columns, not an unconfigured MagicMock."""
    sb = FakeSupabase(mandate)
    row = db.log_transaction("mandate", "PAYMENT_AUTHORIZED", user_id="buyer",
        paypal_authorization_id="auth-999", amount=75.50, currency="USD", status="authorized",
        details={"note": "held"}, supabase_client=sb)
    assert row["event_type"] == "authorized" and row["user_id"] == "buyer"
    assert row["paypal_resource_id"] == "auth-999" and row["amount"] == 75.50
    assert row["details"]["paypal_authorization_id"] == "auth-999"
    assert row["details"]["status"] == "authorized" and row["details"]["note"] == "held"
    assert "paypal_authorization_id" not in row and "status" not in row


def test_log_transaction_resolves_user_id_from_mandate_when_missing(mandate):
    sb = FakeSupabase(mandate)
    row = db.log_transaction("mandate", "order_created", paypal_order_id="ord-111", supabase_client=sb)
    assert row["user_id"] == "buyer" and row["event_type"] == "order_created"
    assert row["paypal_resource_id"] == "ord-111"


def test_cancelled_mandate_aborts_orchestrator_execution_and_voids_authorization(agent, mandate):
    """BUG-10: Cancellation is durable and rejects all later payment claims."""
    from backend.services.mandate_lifecycle import ExecutionStopped
    agent.sb.table("mandates").update({"status": "authorized", "paypal_authorization_id": "auth-hold-10"}).eq("id", "mandate").execute()
    agent.lifecycle.cancel("mandate", "buyer")
    with pytest.raises(ExecutionStopped):
        run(agent._create_order(purchase(), mandate, {}))
    with pytest.raises(ExecutionStopped):
        run(agent._capture({"authorization_id": "auth-hold-10"}, mandate,
            {"authorization_id": "auth-hold-10", "status": "authorized"}))
    agent.paypal.create_authorized_order.assert_not_called()
    agent.paypal.capture_authorization.assert_not_called()
    agent.paypal.void_authorization.assert_called_once_with("auth-hold-10")


def test_create_mandate_preserves_only_explicit_controls_for_background_planning():
    sb = FakeSupabase()
    orchestrator = SimpleNamespace(paypal=MagicMock(), execute_mandate=AsyncMock())
    ai = AsyncMock()
    ai.parse_mandate.return_value = {"max_price": 99.99, "currency": "USD", "category": "electronics", "auto_execute": True}
    service = MandateService(sb, orchestrator, ai)
    result = run(service.create_mandate("buyer", "Find a wireless keyboard under 100 dollars", "db-token",
                                      {"category": "general", "auto_execute": False}))
    assert result["constraints"]["category"] == "general" and result["constraints"]["auto_execute"] is False
    assert "max_price" not in result["constraints"]
    ai.parse_mandate.assert_not_awaited()


def test_mandate_creation_accepts_instructions_without_a_budget():
    sb = FakeSupabase()
    ai = AsyncMock()
    ai.parse_mandate.return_value = {"category": "electronics"}
    orchestrator = SimpleNamespace(paypal=MagicMock(), execute_mandate=AsyncMock())
    service = MandateService(sb, orchestrator, ai)
    created = run(service.create_mandate("buyer", "Find a laptop", "db-token", None))
    assert created["constraints"] == {}
    ai.parse_mandate.assert_not_awaited()
    orchestrator.execute_mandate.assert_awaited_once()


def test_cloudflare_ai_parse_mandate_fails_closed_on_invalid_json():
    from backend.services.cloudflare_ai import CloudflareAI
    cf = CloudflareAI("acc", "tok")
    # Patch the method actually called; patching cf.chat would send a real request.
    cf.chat_with_tools = AsyncMock(return_value={"result": {"response": "Sorry, I cannot understand."}})
    with pytest.raises(ValueError, match="Unable to safely parse"):
        run(cf.parse_mandate("Hello world"))


def test_cancel_mandate_fails_and_preserves_authorized_status_when_void_fails(mandate):
    from backend.services.mandate_lifecycle import CancellationFailed
    sb = FakeSupabase({**mandate, "status": "authorized", "paypal_authorization_id": "auth-fail-14"})
    paypal = MagicMock()
    paypal.void_authorization.side_effect = RuntimeError("PayPal connection timed out")
    paypal.get_authorization.side_effect = RuntimeError("Still unavailable")
    service = MandateService(sb, SimpleNamespace(paypal=paypal), AsyncMock())
    with pytest.raises(CancellationFailed):
        run(service.cancel_mandate("mandate", "buyer"))
    current = service.lifecycle.current("mandate")
    assert current["status"] == "authorized" and current["cancel_requested"]
    assert any(row["event_type"] == "failed" for row in sb.table("transactions").select("*").execute().data)


def test_cancel_mandate_route_reports_502_on_void_error():
    from backend.routes import mandates as mandates_route
    app = FastAPI()
    app.include_router(mandates_route.router)
    app.dependency_overrides[mandates_route.get_current_user] = lambda: {"id": "buyer"}
    service = SimpleNamespace(cancel_mandate=AsyncMock(side_effect=RuntimeError("PayPal void rejected")))
    app.dependency_overrides[mandates_route.get_mandate_service] = lambda: service
    response = TestClient(app).post("/api/mandates/mandate/cancel")
    assert response.status_code == 502 and "PayPal void rejected" in response.json()["detail"]


@pytest.mark.parametrize("mandate_cat, product_cat, expected_decision", [
    ("product", "electronics", "APPROVED"),
    ("electronics", "electronics", "APPROVED"),
    ("flight", "flights", "APPROVED"),
    ("flights", "flight", "APPROVED"),
    ("food", "food", "APPROVED"),
    ("electronics", "food", "REJECTED"),
])
def test_category_normalization_aligns_ui_and_policy(mandate_cat, product_cat, expected_decision):
    """BUG-15: UI category 'product' and variants correctly normalize to policy categories."""
    agent_inst = AgentOrchestrator(AsyncMock(), MagicMock(), MagicMock())
    test_mandate = {
        "id": "mandate-15",
        "user_id": "user-15",
        "constraints": {
            "max_price": 200.0,
            "currency": "USD",
            "category": mandate_cat,
        },
    }
    test_product = {
        "product_name": "Test Item",
        "price": 99.0,
        "currency": "USD",
        "category": product_cat,
    }
    result = agent_inst._check_policy(test_product, test_mandate)
    assert result["decision"] == expected_decision
