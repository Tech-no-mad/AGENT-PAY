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


def run(coroutine):
    return asyncio.run(coroutine)


@pytest.fixture
def mandate():
    return {"id": "mandate", "user_id": "buyer", "payment_token_id": "db-token",
            "description": "Headphones", "constraints": {"max_price": 100, "currency": "USD",
            "category": "electronics", "auto_execute": True}}


@pytest.fixture
def agent():
    instance = AgentOrchestrator(AsyncMock(), MagicMock(), MagicMock())
    with patch("backend.services.agent_orchestrator.update_mandate_status"), patch("backend.services.agent_orchestrator.log_transaction"):
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
    response = run(agent._create_order(purchase(**overrides), mandate, {}))
    assert "error" in response
    agent.paypal.create_authorized_order.assert_not_called()


@pytest.mark.parametrize("constraint", [{"max_price": None}, {"max_price": float("nan")},
    {"deadline": "invalid"}, {"deadline": "2000-01-01T00:00:00Z"}])
def test_invalid_or_expired_constraints_fail_closed(agent, mandate, constraint):
    mandate["constraints"].update(constraint)
    assert "error" in run(agent._create_order(purchase(), mandate, {}))
    agent.paypal.create_authorized_order.assert_not_called()


def test_changed_amount_after_policy_approval_is_rejected(agent, mandate):
    assert agent._check_policy({**purchase(), "price": 50}, mandate)["decision"] == "APPROVED"
    assert "error" in run(agent._create_order(purchase(amount=150), mandate, {}))
    agent.paypal.create_authorized_order.assert_not_called()


def authorized_order():
    return SimpleNamespace(id="order", status="COMPLETED", purchase_units=[
        SimpleNamespace(payments=SimpleNamespace(authorizations=[SimpleNamespace(id="owned-auth")]))])


def test_valid_purchase_resolves_vault_identifier_and_persists_schema(agent, mandate):
    agent.paypal.create_authorized_order.return_value = authorized_order()
    result = {}
    with patch("backend.services.agent_orchestrator.resolve_payment_token", return_value={"paypal_token_id": "vault-id"}) as resolve, patch("backend.services.agent_orchestrator.update_mandate_status") as update:
        run(agent._create_order(purchase(), mandate, result))
    resolve.assert_called_once_with(agent.sb, "db-token", "buyer")
    assert agent.paypal.create_authorized_order.call_args.kwargs["payment_token_id"] == "vault-id"
    assert result["status"] == "authorized"
    assert update.call_args.args[2] == {"paypal_order_id": "order", "paypal_authorization_id": "owned-auth", "executed_amount": 50}
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
    run(agent._capture({"authorization_id": "owned-auth"}, mandate, result))
    assert (result["status"] == "completed") is completed
    agent.paypal.capture_authorization.assert_called_once_with("owned-auth")


def test_loop_does_not_complete_authorization_only_payment(agent, mandate):
    mandate["constraints"]["auto_execute"] = False
    agent.ai.chat_with_tools.side_effect = [
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


def test_log_transaction_aligns_with_schema_and_normalizes_events():
    """BUG-9: Verify event type is normalized to check constraint enum and user_id is persisted."""
    sb = MagicMock()
    insert_mock = sb.table.return_value.insert
    with patch("backend.services.supabase_client.get_supabase", return_value=sb):
        row = db.log_transaction(
            mandate_id="mandate-9",
            user_id="user-9",
            event_type="PAYMENT_AUTHORIZED",
            paypal_authorization_id="auth-999",
            amount=75.50,
            currency="USD",
            status="authorized",
            details={"note": "held"},
        )
    assert row["event_type"] == "authorized"
    assert row["user_id"] == "user-9"
    assert row["paypal_resource_id"] == "auth-999"
    assert row["paypal_authorization_id"] == "auth-999"
    assert row["amount"] == 75.50
    assert row["status"] == "authorized"
    assert row["details"]["note"] == "held"
    insert_mock.assert_called_once()
    saved_payload = insert_mock.call_args.args[0]
    assert saved_payload["event_type"] == "authorized"
    assert saved_payload["user_id"] == "user-9"
    assert saved_payload["paypal_resource_id"] == "auth-999"


def test_log_transaction_resolves_user_id_from_mandate_when_missing():
    """BUG-9: When user_id is omitted, it is queried from the mandates table."""
    sb = MagicMock()
    mandates_query = MagicMock()
    sb.table.side_effect = lambda t: mandates_query if t == "mandates" else MagicMock()
    mandates_query.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value.data = {
        "user_id": "resolved-user-123"
    }

    with patch("backend.services.supabase_client.get_supabase", return_value=sb):
        row = db.log_transaction(
            mandate_id="mandate-with-owner",
            event_type="order_created",
            paypal_order_id="ord-111",
        )
    assert row["user_id"] == "resolved-user-123"
    assert row["event_type"] == "order_created"
    assert row["paypal_resource_id"] == "ord-111"


def test_cancelled_mandate_aborts_orchestrator_execution_and_voids_authorization():
    """BUG-10: Orchestrator stops executing tool calls and voids hold if cancelled."""
    agent_inst = AgentOrchestrator(AsyncMock(), MagicMock(), MagicMock())
    mandate_data = {"id": "mandate-10", "user_id": "buyer-10", "constraints": {"auto_execute": True}}

    # Mark mandate cancelled in orchestrator
    agent_inst.cancel_mandate("mandate-10")

    # Order creation must be rejected
    order_res = run(agent_inst._create_order(purchase(), mandate_data, {}))
    assert "error" in order_res
    assert "cancelled" in order_res["error"].lower()
    agent_inst.paypal.create_authorized_order.assert_not_called()

    # Capture must be aborted and active authorization immediately voided
    capture_res = run(
        agent_inst._capture(
            {"authorization_id": "auth-hold-10"},
            mandate_data,
            {"authorization_id": "auth-hold-10", "status": "authorized"},
        )
    )
    assert "error" in capture_res
    assert "cancelled" in capture_res["error"].lower()
    agent_inst.paypal.capture_authorization.assert_not_called()
    agent_inst.paypal.void_authorization.assert_called_once_with("auth-hold-10")


def test_create_mandate_merges_parsed_and_partial_explicit_constraints():
    """BUG-11: Instruction parsing merges with partial explicit constraints without skipping NLP."""
    sb = MagicMock()
    orchestrator = MagicMock()
    cf_ai = AsyncMock()
    cf_ai.parse_mandate.return_value = {
        "max_price": 99.99,
        "currency": "USD",
        "category": "electronics",
        "auto_execute": True,
    }

    # Mandate insert returns created row
    insert_mock = sb.table.return_value.insert.return_value.execute
    insert_mock.return_value.data = [{
        "id": "mandate-11",
        "user_id": "user-11",
        "constraints": {"max_price": 99.99, "currency": "USD", "category": "general", "auto_execute": False},
        "status": "active",
    }]

    service = MandateService(sb, orchestrator, cf_ai)
    with patch("backend.services.mandate_service.resolve_payment_token", return_value={"id": "tok-1", "paypal_token_id": "pv-1"}), \
         patch("backend.services.mandate_service.log_transaction"):
        result = run(
            service.create_mandate(
                user_id="user-11",
                description="Find a wireless keyboard under 100 dollars",
                payment_token_id="tok-1",
                constraints={"category": "general", "auto_execute": False},
            )
        )

    # Parsing was called
    cf_ai.parse_mandate.assert_called_once_with("Find a wireless keyboard under 100 dollars")
    # Saved constraints merged parsed max_price (99.99) and explicit category ("general")
    saved_constraints = sb.table.return_value.insert.call_args.args[0]["constraints"]
    assert saved_constraints["max_price"] == 99.99
    assert saved_constraints["category"] == "general"
    assert saved_constraints["auto_execute"] is False


def test_mandate_creation_fails_closed_when_budget_cannot_be_parsed():
    """BUG-12: Creation fails closed with ValueError when spending ceiling is missing/unparseable."""
    sb = MagicMock()
    cf_ai = AsyncMock()
    # Cloudflare AI cannot extract a positive budget
    cf_ai.parse_mandate.return_value = {"category": "electronics"}

    service = MandateService(sb, MagicMock(), cf_ai)
    with patch("backend.services.mandate_service.resolve_payment_token", return_value={"id": "tok-1", "paypal_token_id": "pv-1"}), \
         pytest.raises(ValueError, match="Mandate requires an explicit or parsed positive numeric max_price"):
        run(
            service.create_mandate(
                user_id="user-12",
                description="Browse some laptops sometime",
                payment_token_id="tok-1",
                constraints=None,
            )
        )
    sb.table.return_value.insert.assert_not_called()


def test_cloudflare_ai_parse_mandate_fails_closed_on_invalid_json():
    """BUG-12: CloudflareAI.parse_mandate raises ValueError instead of returning permissive defaults."""
    from backend.services.cloudflare_ai import CloudflareAI

    cf = CloudflareAI(account_id="acc", api_token="tok")
    cf.chat = AsyncMock(return_value="Sorry, I cannot understand this instruction.")
    with pytest.raises(ValueError, match="Failed to parse spending constraints from instruction"):
        run(cf.parse_mandate("Hello world"))


def test_cancel_mandate_fails_and_preserves_authorized_status_when_void_fails():
    """BUG-14: If PayPal void fails during cancellation, raise RuntimeError and do not mark cancelled."""
    sb = MagicMock()
    orchestrator = MagicMock()
    service = MandateService(sb, orchestrator, AsyncMock())

    service.get_mandate = AsyncMock(return_value={
        "id": "mandate-14",
        "user_id": "user-14",
        "status": "authorized",
        "paypal_authorization_id": "auth-fail-14",
    })

    with patch("backend.services.paypal_service.PayPalService.void_authorization", side_effect=RuntimeError("PayPal connection timed out")), \
         patch("backend.services.mandate_service.update_mandate_status") as update_mock, \
         patch("backend.services.mandate_service.log_transaction") as log_mock:
        with pytest.raises(RuntimeError, match="Failed to void PayPal authorization auth-fail-14"):
            run(service.cancel_mandate("mandate-14", "user-14"))

        # Status must not be updated to cancelled
        update_mock.assert_not_called()
        # Failure transaction must be logged
        assert any(call.kwargs.get("event_type") == "failed" for call in log_mock.call_args_list)


def test_cancel_mandate_route_reports_502_on_void_error():
    """BUG-14: HTTP endpoint returns 502 Bad Gateway when cancellation void fails."""
    from backend.routes import mandates as mandates_route

    app = FastAPI()
    app.include_router(mandates_route.router)
    app.dependency_overrides[mandates_route.get_current_user] = lambda: {"id": "user-14", "email": "user@example.com"}

    mock_service = MagicMock()
    mock_service.cancel_mandate = AsyncMock(side_effect=RuntimeError("PayPal void rejected: 400 Bad Request"))
    app.dependency_overrides[mandates_route.get_mandate_service] = lambda: mock_service

    client = TestClient(app)
    response = client.post("/api/mandates/mandate-14/cancel")
    assert response.status_code == 502
    assert "PayPal void rejected" in response.json()["detail"]


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

