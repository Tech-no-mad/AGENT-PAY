"""The first policy rejection is a durable stop, never a retryable model hint."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.services.agent_orchestrator import AgentOrchestrator
from backend.services.mandate_lifecycle import CancellationFailed, CancellationPending, ExecutionStopped
from backend.services.supabase_client import AuditLogError, log_transaction
from backend.tests.fake_supabase import FakeSupabase


def policy_args(**overrides):
    return {"price": 285, "currency": "USD", "category": "flights",
            "product_name": "Economy Round-trip — Delta Airlines", "justification": "Within the flight budget",
            **overrides}


def call(name, args):
    return {"name": name, "arguments": args}


def reply(*calls):
    return {"result": {"tool_calls": list(calls)}}


def setup_agent(*responses, **row_fields):
    plan = {"min_price": 200, "max_price": 800, "currency": "USD", "category": "flights",
            "pricing_reasoning": "Economy flight estimate.", "deadline": None, "auto_execute": True,
            "additional_rules": None}
    mandate = {"id": "mandate", "user_id": "buyer", "payment_token_id": "db-token",
               "description": "Book an economy flight SFO to JFK", "constraints": {
                   "max_price": 800, "currency": "USD", "category": "flights", "auto_execute": True,
               }, **row_fields}
    database = FakeSupabase(mandate)
    ai = SimpleNamespace(chat_with_tools=AsyncMock(side_effect=[
        {"result": {"response": json.dumps(plan)}}, *responses,
    ]))
    paypal = MagicMock()
    return AgentOrchestrator(ai, paypal, database), mandate, database


@pytest.mark.parametrize("batched", [True, False])
@pytest.mark.parametrize("invalid", [{"price": "285 USD"}, {"price": 0}, {"price": 801}, {"category": "food"}, {"currency": "EUR"}])
def test_first_rejection_prevents_corrected_check_order_and_capture(batched, invalid):
    reject = call("check_mandate_policy", policy_args(**invalid))
    later = [call("check_mandate_policy", policy_args()),
             call("create_authorized_order", {**policy_args(), "amount": 285}),
             call("capture_authorized_payment", {"authorization_id": "auth"})]
    responses = [reply(reject, *later)] if batched else [reply(reject), reply(*later)]
    agent, mandate, db = setup_agent(*responses)
    result = asyncio.run(agent.execute_mandate(mandate))
    assert result["status"] == "cancelled" and "Policy rejected" in result["agent_reasoning"]
    assert agent.ai.chat_with_tools.await_count == 2  # Planning, then the rejected check only.
    saved = agent.lifecycle.current("mandate")
    assert saved["cancel_requested"] and saved["status"] == "cancelled"
    assert "Policy rejected" in saved["agent_reasoning"]
    events = db.table("transactions").select("*").execute().data
    assert [event["event_type"] for event in events] == ["constraints_defined", "policy_rejected", "voided"]
    if invalid.get("price") == "285 USD":
        assert events[1]["details"]["submitted_amount"] == "285 USD"
    agent.paypal.create_authorized_order.assert_not_called()
    agent.paypal.capture_authorization.assert_not_called()
    # A fresh worker with the old snapshot cannot approve or pay either.
    other = AgentOrchestrator(AsyncMock(), agent.paypal, db)
    with pytest.raises(ExecutionStopped):
        asyncio.run(other._execute_tool("check_mandate_policy", policy_args(), mandate, {}))
    with pytest.raises(ExecutionStopped):
        other.lifecycle.claim("mandate", "authorize")


def test_rejection_after_an_approval_still_stops_the_remaining_batch():
    agent, mandate, db = setup_agent(reply(
        call("check_mandate_policy", policy_args()),
        call("check_mandate_policy", policy_args(price="285 USD")),
        call("check_mandate_policy", policy_args()),
    ))
    assert asyncio.run(agent.execute_mandate(mandate))["status"] == "cancelled"
    decisions = db.table("transactions").select("*").in_("event_type", ["policy_approved", "policy_rejected"]).execute().data
    assert [event["event_type"] for event in decisions] == ["policy_approved", "policy_rejected"]


def test_rejection_voids_an_existing_hold_before_finishing_cancellation():
    agent, mandate, db = setup_agent(status="authorized", paypal_authorization_id="held-auth")
    with pytest.raises(ExecutionStopped):
        asyncio.run(agent._check_policy_wrapper(policy_args(price="285"), mandate, {}))
    agent.paypal.void_authorization.assert_called_once_with("held-auth")
    assert agent.lifecycle.current("mandate")["status"] == "cancelled"
    agent.paypal.capture_authorization.assert_not_called()


def test_failed_void_keeps_stop_flag_and_blocks_capture_until_release_is_confirmed():
    agent, mandate, db = setup_agent(status="authorized", paypal_authorization_id="held-auth")
    agent.paypal.void_authorization.side_effect = RuntimeError("void unavailable")
    agent.paypal.get_authorization.side_effect = RuntimeError("unavailable")
    with pytest.raises(CancellationFailed):
        asyncio.run(agent._check_policy_wrapper(policy_args(price="285"), mandate, {}))
    saved = agent.lifecycle.current("mandate")
    assert saved["cancel_requested"] and saved["status"] == "authorized"
    with pytest.raises(ExecutionStopped):
        agent.lifecycle.claim("mandate", "capture")
    agent.paypal.capture_authorization.assert_not_called()
    agent.paypal.void_authorization.side_effect = None
    assert agent.lifecycle.cancel("mandate", "buyer")["status"] == "cancelled"


def test_stop_flag_is_persisted_before_rejection_audit_and_survives_audit_failure():
    agent, mandate, db = setup_agent(status="authorized", paypal_authorization_id="held-auth")
    def audit(*args, **kwargs):
        if kwargs.get("event_type") == "POLICY_CHECK":
            assert agent.lifecycle.current("mandate")["cancel_requested"]
            raise AuditLogError("audit unavailable")
        return log_transaction(*args, **kwargs)
    with patch("backend.services.agent_orchestrator.log_transaction", side_effect=audit):
        with pytest.raises(AuditLogError):
            asyncio.run(agent._check_policy_wrapper(policy_args(price="285"), mandate, {}))
    assert agent.lifecycle.current("mandate")["status"] == "cancelled"
    agent.paypal.void_authorization.assert_called_once_with("held-auth")
    with pytest.raises(ExecutionStopped):
        agent.lifecycle.claim("mandate", "capture")


def test_authorization_revalidation_requests_stop_before_releasing_its_claim():
    agent, mandate, db = setup_agent()
    from backend.services.mandate_lifecycle import MandateLifecycle
    original_finish = MandateLifecycle.finish
    def finish(lifecycle, claim, **fields):
        assert lifecycle.current("mandate")["cancel_requested"]
        return original_finish(lifecycle, claim, **fields)
    with patch.object(agent, "_check_policy", side_effect=[
        {"decision": "APPROVED"}, {"decision": "REJECTED", "reason": "Budget changed"},
    ]), patch.object(MandateLifecycle, "finish", finish):
        with pytest.raises(ExecutionStopped):
            asyncio.run(agent._create_order({**policy_args(), "amount": 285}, mandate, {}))
    saved = agent.lifecycle.current("mandate")
    assert saved["status"] == "cancelled" and saved["payment_operation_id"] is None
    agent.paypal.create_authorized_order.assert_not_called()


def test_rejection_during_another_payment_blocks_capture_after_authorization_settles():
    agent, mandate, db = setup_agent()
    claim = agent.lifecycle.claim("mandate", "authorize")
    with pytest.raises(CancellationPending):
        asyncio.run(agent._check_policy_wrapper(policy_args(price="285"), mandate, {}))
    assert agent.lifecycle.current("mandate")["cancel_requested"]
    agent.lifecycle.finish(claim, status="authorized", paypal_authorization_id="held-auth")
    with pytest.raises(ExecutionStopped):
        asyncio.run(agent._execute_tool("capture_authorized_payment", {"authorization_id": "held-auth"}, mandate,
                                       {"status": "authorized", "authorization_id": "held-auth"}))
    assert agent.lifecycle.current("mandate")["status"] == "cancelled"
    agent.paypal.void_authorization.assert_called_once_with("held-auth")
    agent.paypal.capture_authorization.assert_not_called()
