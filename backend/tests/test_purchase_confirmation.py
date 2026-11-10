import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routes.auth import get_current_user
from backend.routes.mandates import get_mandate_service, router
from backend.services.agent_orchestrator import AgentOrchestrator
from backend.services.mandate_lifecycle import ExecutionStopped, MandateLifecycle
from backend.services.mandate_service import MandateService
from backend.services.purchase_confirmation import ConfirmationConflict, PurchaseConfirmation
from backend.services.supabase_client import AuditLogError
from backend.tests.fake_supabase import FakeSupabase


@pytest.fixture(autouse=True)
def confirmation_window(monkeypatch):
    monkeypatch.setattr("backend.services.purchase_confirmation.CONFIRMATION_SECONDS", 10)


@pytest.fixture
def setup(monkeypatch):
    now = [datetime(2026, 11, 10, 12, tzinfo=timezone.utc)]
    monkeypatch.setattr("backend.services.purchase_confirmation.utc_now", lambda: now[0])
    mandate = {"id": "mandate", "user_id": "buyer", "payment_token_id": "db-token", "description": "Power bank",
               "constraints": {"max_price": 50, "currency": "USD", "category": "electronics", "auto_execute": True}}
    sb, paypal = FakeSupabase(mandate), MagicMock()
    agent = AgentOrchestrator(AsyncMock(), paypal, sb)
    gate = PurchaseConfirmation(agent.lifecycle)
    product = {"product_name": "Power bank", "price": 39.99, "currency": "USD", "category": "electronics"}
    return SimpleNamespace(now=now, mandate=mandate, sb=sb, paypal=paypal, agent=agent, gate=gate, product=product)


@pytest.mark.parametrize("decision", ["continue", "cancel", "timeout"])
def test_payment_waits_for_decision_or_full_ten_seconds(setup, decision):
    s = setup
    s.paypal.create_authorized_order.return_value = {"id": "order", "status": "COMPLETED", "purchase_units": [{"payments": {"authorizations": [{"id": "auth"}]}}]}
    real_sleep = asyncio.sleep
    checks = []
    async def advance(delay):
        s.paypal.create_authorized_order.assert_not_called()
        row = s.agent.lifecycle.current("mandate")
        checks.append(row["purchase_confirmation_status"])
        assert row["purchase_confirmation_product"] == s.product
        if decision == "timeout":
            s.now[0] += timedelta(seconds=1)
        else:
            s.gate.decide("mandate", "buyer", row["purchase_confirmation_id"], decision)
        await real_sleep(0)
    async def execute():
        with patch("backend.services.purchase_confirmation.asyncio.sleep", side_effect=advance):
            return await s.agent._create_order({**s.product, "amount": 39.99}, s.mandate, {})
    if decision == "cancel":
        with pytest.raises(ExecutionStopped):
            asyncio.run(execute())
        assert s.agent.lifecycle.current("mandate")["status"] == "cancelled"
        s.paypal.create_authorized_order.assert_not_called()
    else:
        assert asyncio.run(execute())["status"] == "AUTHORIZED"
        s.paypal.create_authorized_order.assert_called_once()
        assert len(checks) == (10 if decision == "timeout" else 1)


def test_deadline_and_late_or_stale_decisions(setup):
    s = setup
    pending = s.gate.begin("mandate", s.product)
    identifier = pending["purchase_confirmation_id"]
    assert datetime.fromisoformat(pending["purchase_confirmation_expires_at"]) == s.now[0] + timedelta(seconds=10)
    with pytest.raises(ConfirmationConflict):
        s.gate.decide("mandate", "buyer", "stale-id", "continue")
    with pytest.raises(PermissionError):
        s.gate.decide("mandate", "someone-else", identifier, "cancel")
    s.now[0] += timedelta(seconds=10)
    with pytest.raises(ConfirmationConflict):
        s.gate.decide("mandate", "buyer", identifier, "cancel")
    assert asyncio.run(s.gate.wait("mandate", identifier))["purchase_confirmation_status"] == "auto_approved"


def test_cancel_wins_before_timeout_and_cannot_be_reapproved(setup):
    s = setup
    pending = s.gate.begin("mandate", s.product)
    identifier = pending["purchase_confirmation_id"]
    s.now[0] += timedelta(seconds=9.999)
    s.gate.decide("mandate", "buyer", identifier, "cancel")
    s.now[0] += timedelta(seconds=1)
    with pytest.raises(ExecutionStopped):
        asyncio.run(s.gate.wait("mandate", identifier))
    with pytest.raises(ConfirmationConflict):
        s.gate.decide("mandate", "buyer", identifier, "continue")
    assert "cancelled by you" in s.agent.lifecycle.current("mandate")["agent_reasoning"]


def test_duplicate_continue_does_not_repeat_decision_or_reset_timer(setup):
    s = setup
    pending = s.gate.begin("mandate", s.product)
    assert s.gate.begin("mandate", s.product)["purchase_confirmation_id"] == pending["purchase_confirmation_id"]
    for _ in range(2):
        s.gate.decide("mandate", "buyer", pending["purchase_confirmation_id"], "continue")
    events = s.sb.table("transactions").select("*").execute().data
    assert [event["event_type"] for event in events] == ["purchase_confirmation_requested", "purchase_confirmation_approved"]


def test_changed_price_requires_new_confirmation_and_old_claim_cannot_pay(setup):
    s = setup
    first = s.gate.begin("mandate", s.product)
    s.gate.decide("mandate", "buyer", first["purchase_confirmation_id"], "continue")
    next_offer = s.gate.begin("mandate", {**s.product, "price": 45})
    assert next_offer["purchase_confirmation_id"] != first["purchase_confirmation_id"]
    assert next_offer["purchase_confirmation_status"] == "pending"
    from backend.services.mandate_lifecycle import CancellationPending
    with pytest.raises(CancellationPending):
        s.agent.lifecycle.claim("mandate", "authorize", confirmation_id=first["purchase_confirmation_id"])
    s.paypal.create_authorized_order.assert_not_called()


def test_failed_decision_audit_blocks_payment(setup):
    s = setup
    row = s.gate.begin("mandate", s.product)
    with patch("backend.services.purchase_confirmation.log_transaction", side_effect=AuditLogError("offline")):
        with pytest.raises(AuditLogError):
            s.gate.decide("mandate", "buyer", row["purchase_confirmation_id"], "continue")
    assert s.agent.lifecycle.current("mandate")["cancel_requested"]
    assert not s.agent.lifecycle.current("mandate")["purchase_confirmation_ready"]
    with pytest.raises(ExecutionStopped):
        asyncio.run(s.gate.wait("mandate", row["purchase_confirmation_id"]))


def test_routes_scope_owner_and_validate_decisions(setup):
    s = setup
    row = s.gate.begin("mandate", s.product)
    app = FastAPI()
    app.include_router(router)
    service = MandateService(s.sb, s.agent, s.agent.ai)
    app.dependency_overrides[get_mandate_service] = lambda: service
    app.dependency_overrides[get_current_user] = lambda: {"id": "buyer"}
    client = TestClient(app)
    pending = client.get("/api/mandates/confirmations/pending").json()
    assert len(pending["mandates"]) == 1 and pending["server_time"]
    payload = {"confirmation_id": row["purchase_confirmation_id"], "decision": "continue"}
    assert client.post("/api/mandates/mandate/confirmation", json={**payload, "decision": "timeout"}).status_code == 422
    app.dependency_overrides[get_current_user] = lambda: {"id": "foreign"}
    assert client.get("/api/mandates/confirmations/pending").json()["mandates"] == []
    assert client.post("/api/mandates/mandate/confirmation", json=payload).status_code == 404
    app.dependency_overrides[get_current_user] = lambda: {"id": "buyer"}
    assert client.post("/api/mandates/mandate/confirmation", json=payload).json()["purchase_confirmation_status"] == "approved"
