"""BUG-9/10/11/12/14/15: schema, orchestration and cancellation regressions."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.models.schemas import MandateConstraints, MandateCreate
from backend.routes.mandates import create_mandate, cancel_mandate
from backend.services.agent_orchestrator import AgentOrchestrator, _generate_mock_products
from backend.services.categories import category_allowed
from backend.services.cloudflare_ai import CloudflareAI, ConstraintParseError
from backend.services.mandate_lifecycle import (
    CancellationFailed, CancellationPending, ExecutionStopped, MandateLifecycle, PaymentOutcomeUnknown,
)
from backend.services.mandate_service import MandateService
from backend.services.supabase_client import AuditLogError, log_transaction, update_mandate_status
from backend.tests.fake_supabase import FakeSupabase


@pytest.fixture
def mandate():
    return {"id": "mandate", "user_id": "buyer", "payment_token_id": "db-token", "description": "Headphones under $150",
            "constraints": {"max_price": 150, "currency": "USD", "category": "physical_goods", "auto_execute": True}}


@pytest.fixture
def database(mandate):
    return FakeSupabase(mandate)


def extracted(**overrides):
    return {"max_price": 150, "currency": "EUR", "category": "electronics", "deadline": "2030-12-01T00:00:00Z",
            "auto_execute": False, "additional_rules": "No used items", **overrides}


@pytest.mark.parametrize("event,status,expected", [
    ("MANDATE_CREATED", "active", "mandate_created"), ("PRODUCT_SEARCH", "completed", "search_started"),
    ("POLICY_CHECK", "APPROVED", "policy_approved"), ("POLICY_CHECK", "REJECTED", "policy_rejected"),
    ("ORDER_CREATED", "created", "order_created"), ("ORDER_AUTHORIZED", "authorized", "authorized"),
    ("PAYMENT_AUTHORIZED", "authorized", "authorized"), ("PAYMENT_CAPTURED", "completed", "captured"),
    ("AUTHORIZATION_VOIDED", "voided", "voided"), ("PAYMENT_REFUNDED", "refunded", "refunded"),
    ("CAPTURE_DENIED", "failed", "failed"), ("ORDER_FAILED", "failed", "failed"),
    ("CAPTURE_FAILED", "failed", "failed"), ("MANDATE_FAILED", "failed", "failed"),
    ("MANDATE_CANCELLED", "cancelled", "voided"), ("MANDATE_COMPLETED", "completed", "captured"),
])
def test_audit_events_insert_against_migration_schema(database, event, status, expected):
    row = log_transaction("mandate", event, status=status, amount=50, currency="USD",
        paypal_order_id="order", paypal_authorization_id="auth", paypal_capture_id="capture",
        details={"reason": "regression"}, supabase_client=database)
    assert row["user_id"] == "buyer" and row["event_type"] == expected
    assert row["paypal_resource_id"] == "capture"
    assert row["details"] == {"reason": "regression", "original_event": event, "status": status,
        "paypal_order_id": "order", "paypal_authorization_id": "auth", "paypal_capture_id": "capture"}
    assert not {"status", "paypal_order_id", "paypal_authorization_id", "paypal_capture_id"}.intersection(row)


def test_audit_failures_are_not_silently_discarded(database):
    with pytest.raises(AuditLogError):
        log_transaction("mandate", "not_an_event", supabase_client=database)
    with pytest.raises(AuditLogError):
        log_transaction("missing", "MANDATE_CREATED", supabase_client=database)
    with pytest.raises(AuditLogError):
        log_transaction("mandate", "MANDATE_CREATED", user_id="foreign", supabase_client=database)
    assert database.table("transactions").select("*").execute().data == []


@pytest.mark.parametrize("amount", [float("nan"), float("inf"), "invalid"])
def test_rejected_malformed_amount_can_still_be_audited(database, amount):
    row = log_transaction("mandate", "POLICY_CHECK", status="REJECTED", amount=amount, supabase_client=database)
    assert row["amount"] is None and row["details"]["submitted_amount"]


@pytest.mark.parametrize("body", ["not json", "[]", "null", '"string"', "{}",
    json.dumps(extracted(auto_execute="false")), json.dumps(extracted(max_price=-1)),
    json.dumps(extracted(max_price=True)), json.dumps(extracted(max_price=float("nan"))),
    json.dumps(extracted(deadline="tomorrow")), json.dumps(extracted(deadline="2030-01-01")),
    json.dumps(extracted(currency="not-currency")), json.dumps(extracted(extra_field="unexpected"))])
def test_malformed_extraction_is_rejected(body):
    ai = CloudflareAI("account", "token")
    ai.chat_with_tools = AsyncMock(return_value={"result": {"response": body}})
    with pytest.raises(ConstraintParseError):
        asyncio.run(ai.parse_mandate("Headphones under $150"))


def test_failed_provider_response_does_not_enable_execution():
    ai = CloudflareAI("account", "token")
    ai.chat_with_tools = AsyncMock(return_value={"success": False, "result": {"response": json.dumps(extracted())}})
    with pytest.raises(ConstraintParseError):
        asyncio.run(ai.parse_mandate("buy"))


def test_valid_extraction_preserves_all_restrictions():
    ai = CloudflareAI("account", "token")
    ai.chat_with_tools = AsyncMock(return_value={"result": {"response": "```json\n" + json.dumps(extracted()) + "\n```"}})
    parsed = asyncio.run(ai.parse_mandate("buy"))
    assert parsed["max_price"] == 150 and parsed["auto_execute"] is False
    assert parsed["deadline"] == "2030-12-01T00:00:00+00:00" and parsed["additional_rules"] == "No used items"


def service_for(database, parsed):
    orchestrator = SimpleNamespace(paypal=MagicMock(), execute_mandate=AsyncMock())
    ai = SimpleNamespace(parse_mandate=AsyncMock(return_value=parsed))
    return MandateService(database, orchestrator, ai)


def create_and_wait(service, constraints):
    async def work():
        row = await service.create_mandate("buyer", "Headphones under $150", "db-token", constraints)
        await asyncio.sleep(0)
        return row
    return asyncio.run(work())


def test_partial_ui_constraints_do_not_drop_description_limits():
    database = FakeSupabase()
    service = service_for(database, extracted())
    row = create_and_wait(service, {"category": "product", "auto_execute": True})
    assert row["constraints"]["max_price"] == 150
    assert row["constraints"]["currency"] == "EUR"
    assert row["constraints"]["deadline"] == "2030-12-01T00:00:00+00:00"
    assert row["constraints"]["category"] == "physical_goods"
    assert row["constraints"]["auto_execute"] is True
    assert row["constraints"]["additional_rules"] == "No used items"
    service.orchestrator.execute_mandate.assert_awaited_once()


def test_explicit_limits_override_parsed_values_and_null_does_not_clear_restrictions():
    row = create_and_wait(service_for(FakeSupabase(), extracted()), {"max_price": 75, "currency": "USD", "deadline": None})
    assert row["constraints"]["max_price"] == 75 and row["constraints"]["currency"] == "USD"
    assert row["constraints"]["auto_execute"] is False and row["constraints"]["deadline"]


def test_explicit_budget_can_complete_valid_extraction_with_no_instruction_budget():
    row = create_and_wait(service_for(FakeSupabase(), extracted(max_price=None)), {"max_price": 75})
    assert row["constraints"]["max_price"] == 75


def test_route_passes_only_explicit_fields_for_merge():
    service = SimpleNamespace(create_mandate=AsyncMock(return_value={"id": "new"}))
    payload = MandateCreate(description="under $150", payment_token_id="db-token", constraints=MandateConstraints(category="product"))
    asyncio.run(create_mandate(payload, {"id": "buyer"}, service))
    assert service.create_mandate.call_args.kwargs["constraints"] == {"category": "physical_goods"}


@pytest.mark.parametrize("failure", ["parse", "missing_budget"])
def test_invalid_constraints_never_insert_or_start_a_worker(failure):
    database = FakeSupabase()
    service = service_for(database, extracted(max_price=None))
    if failure == "parse":
        service.ai.parse_mandate.side_effect = ConstraintParseError("invalid")
    with pytest.raises(ValueError):
        create_and_wait(service, {"category": "product"})
    assert database.table("mandates").select("*").execute().data == []
    service.orchestrator.execute_mandate.assert_not_awaited()


@pytest.mark.parametrize("required,actual,allowed", [("product", "electronics", True), ("physical_goods", "general", True),
    ("physical_goods", "food", False), ("physical_goods", "flights", False), ("flight", "flights", True),
    ("hotel", "hotels", True), ("service", "services", True), ("electronics", "general", False),
    ("food", "seafood-unrelated", False)])
def test_shared_category_matching_is_explicit(required, actual, allowed):
    assert category_allowed(required, actual) is allowed


def test_ui_physical_goods_accepts_search_results(database, mandate):
    agent = AgentOrchestrator(AsyncMock(), MagicMock(), database)
    products = _generate_mock_products("headphones electronics", "physical_goods", 500, "USD")
    assert products
    product = products[0]
    mandate["constraints"]["max_price"] = 500
    assert agent._check_policy({"price": product["price"], "currency": "USD", "category": product["category"],
                               "product_name": product["name"]}, mandate)["decision"] == "APPROVED"


def test_cancellation_while_ai_is_waiting_blocks_all_later_payment_tools(database, mandate):
    paypal = MagicMock()
    ai = SimpleNamespace(chat_with_tools=None)
    agent = AgentOrchestrator(ai, paypal, database)

    async def scenario():
        waiting, resume = asyncio.Event(), asyncio.Event()
        async def chat(*args):
            waiting.set()
            await resume.wait()
            return {"result": {"tool_calls": [
                {"name": "create_authorized_order", "arguments": {"amount": 50, "category": "electronics", "currency": "USD"}},
                {"name": "capture_authorized_payment", "arguments": {"authorization_id": "fake"}},
            ]}}
        ai.chat_with_tools = chat
        worker = asyncio.create_task(agent.execute_mandate(mandate))
        await waiting.wait()
        cancelled = MandateLifecycle(database, paypal).cancel("mandate", "buyer")
        assert cancelled["status"] == "cancelled"
        resume.set()
        return await worker

    result = asyncio.run(scenario())
    assert result["status"] == "cancelled"
    paypal.create_authorized_order.assert_not_called()
    paypal.capture_authorization.assert_not_called()
    assert update_mandate_status("mandate", "completed", supabase_client=database) is None
    assert database.table("mandates").select("*").maybe_single().execute().data["status"] == "cancelled"


def test_cancellation_during_authorization_releases_hold_and_blocks_capture(database, mandate):
    paypal = MagicMock()
    agent = AgentOrchestrator(AsyncMock(), paypal, database)
    other_worker = MandateLifecycle(database, paypal)
    def authorize(**kwargs):
        with pytest.raises(CancellationPending):
            other_worker.cancel("mandate", "buyer")
        return {"id": "order", "status": "COMPLETED", "purchase_units": [{"payments": {"authorizations": [{"id": "held-auth"}]}}]}
    paypal.create_authorized_order.side_effect = authorize
    with pytest.raises(ExecutionStopped):
        asyncio.run(agent._create_order({"amount": 50, "currency": "USD", "category": "electronics"}, mandate, {}))
    assert agent.lifecycle.current("mandate")["status"] == "cancelled"
    paypal.void_authorization.assert_called_once_with("held-auth")
    paypal.capture_authorization.assert_not_called()


def test_cancellation_after_order_creation_prevents_explicit_authorization(database, mandate):
    paypal = MagicMock()
    agent = AgentOrchestrator(AsyncMock(), paypal, database)
    def create(**kwargs):
        with pytest.raises(CancellationPending):
            MandateLifecycle(database, paypal).cancel("mandate", "buyer")
        return {"id": "order", "status": "CREATED"}
    paypal.create_authorized_order.side_effect = create
    with pytest.raises(ExecutionStopped):
        asyncio.run(agent._create_order({"amount": 50, "currency": "USD", "category": "electronics"}, mandate, {}))
    assert agent.lifecycle.current("mandate")["status"] == "cancelled"
    paypal.authorize_order.assert_not_called()
    paypal.capture_authorization.assert_not_called()


def test_audit_failure_does_not_hide_a_confirmed_charge_or_repeat_payment(database, mandate):
    paypal = MagicMock()
    paypal.create_authorized_order.return_value = {"id": "order", "status": "COMPLETED", "purchase_units": [
        {"payments": {"authorizations": [{"id": "auth"}]}}]}
    paypal.capture_authorization.return_value = {"id": "capture", "status": "COMPLETED"}
    ai = SimpleNamespace(chat_with_tools=AsyncMock(return_value={"result": {"tool_calls": [
        {"name": "create_authorized_order", "arguments": {"amount": 50, "currency": "USD", "category": "electronics"}},
        {"name": "capture_authorized_payment", "arguments": {"authorization_id": "auth"}},
    ]}}))
    def audit(*args, **kwargs):
        if args[1] == "PAYMENT_CAPTURED":
            raise AuditLogError("database unavailable")
        return log_transaction(*args, **kwargs)
    agent = AgentOrchestrator(ai, paypal, database)
    with patch("backend.services.agent_orchestrator.log_transaction", side_effect=audit):
        result = asyncio.run(agent.execute_mandate(mandate))
    assert result["status"] == "completed" and result["error"]
    assert agent.lifecycle.current("mandate")["status"] == "completed"
    assert paypal.create_authorized_order.call_count == paypal.capture_authorization.call_count == 1


def test_conditional_claims_serialize_two_workers(database):
    barrier = Barrier(2)
    def claim():
        barrier.wait()
        try:
            return MandateLifecycle(database, MagicMock()).claim("mandate", "authorize")
        except CancellationPending:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: claim(), range(2)))
    assert sum(result is not None for result in results) == 1
    with pytest.raises(CancellationPending):
        MandateLifecycle(database, MagicMock()).cancel("mandate", "buyer")
    assert MandateLifecycle(database, MagicMock()).current("mandate")["cancel_requested"]


def test_void_failure_is_reported_and_retryable_without_resuming_capture(database):
    database.table("mandates").update({"status": "authorized", "paypal_authorization_id": "held-auth"}).eq("id", "mandate").execute()
    paypal = MagicMock()
    paypal.void_authorization.side_effect = [TimeoutError("timeout"), None]
    paypal.get_authorization.side_effect = RuntimeError("unavailable")
    lifecycle = MandateLifecycle(database, paypal)
    with pytest.raises(CancellationFailed):
        lifecycle.cancel("mandate", "buyer")
    row = lifecycle.current("mandate")
    assert row["status"] == "authorized" and row["cancel_requested"]
    assert row["payment_operation_id"] is None
    with pytest.raises(ExecutionStopped):
        lifecycle.claim("mandate", "capture")
    assert lifecycle.cancel("mandate", "buyer")["status"] == "cancelled"
    assert paypal.void_authorization.call_count == 2
    events = database.table("transactions").select("*").execute().data
    assert any(e["event_type"] == "failed" and e["details"].get("retryable") for e in events)


def test_ambiguous_void_is_confirmed_only_after_provider_reconciliation(database):
    database.table("mandates").update({"status": "authorized", "paypal_authorization_id": "held-auth"}).eq("id", "mandate").execute()
    paypal = MagicMock()
    paypal.void_authorization.side_effect = TimeoutError()
    paypal.get_authorization.return_value = {"status": "VOIDED"}
    assert MandateLifecycle(database, paypal).cancel("mandate", "buyer")["status"] == "cancelled"


@pytest.mark.parametrize("error,status", [(CancellationPending("pending"), 409), (CancellationFailed("retry"), 503)])
def test_cancellation_route_surfaces_pending_and_failed_void(error, status):
    from fastapi import HTTPException
    service = SimpleNamespace(cancel_mandate=AsyncMock(side_effect=error))
    with pytest.raises(HTTPException) as raised:
        asyncio.run(cancel_mandate("mandate", {"id": "buyer"}, service))
    assert raised.value.status_code == status


def test_capture_timeout_retains_claim_instead_of_allowing_duplicate_charge(database, mandate):
    database.table("mandates").update({"status": "authorized", "paypal_authorization_id": "held-auth"}).eq("id", "mandate").execute()
    paypal = MagicMock()
    paypal.capture_authorization.side_effect = TimeoutError()
    agent = AgentOrchestrator(AsyncMock(), paypal, database)
    result = {"status": "authorized", "authorization_id": "held-auth"}
    with pytest.raises(PaymentOutcomeUnknown):
        asyncio.run(agent._capture({}, mandate, result))
    assert agent.lifecycle.current("mandate")["payment_operation_id"]
    with pytest.raises(CancellationPending):
        agent.lifecycle.cancel("mandate", "buyer")
    assert agent.lifecycle.current("mandate")["status"] != "cancelled"
    assert paypal.capture_authorization.call_count == 1
