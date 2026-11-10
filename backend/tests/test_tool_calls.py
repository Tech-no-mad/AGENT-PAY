"""Model-produced JSON action text must execute through the normal guarded tools."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.services.agent_orchestrator import AgentOrchestrator
from backend.services.tool_calls import ToolCallFormatError, parse_tool_response
from backend.tests.fake_supabase import FakeSupabase


PRODUCT = "Demo USB-C Portable Power Bank 20000mAh"


def policy_arguments(price="39.99"):
    return {"product_name": PRODUCT, "price": price, "currency": "USD", "category": "electronics",
            "justification": "The product meets the mandate's requirements: 20000mAh+ under $50"}


def action(name, arguments):
    return {"name": name, "parameters": arguments}


def text_reply(payload):
    return {"result": {"response": json.dumps(payload)}}


@pytest.mark.parametrize("format", ["flat", "nested", "text", "fenced", "list", "wrapped", "nested_text", "string_arguments"])
def test_known_call_formats_normalize_without_losing_ids_or_arguments(format):
    call = action("check_mandate_policy", policy_arguments())
    call["id"] = "provider-id"
    if format == "flat":
        response = {"result": {"tool_calls": [call]}}
    elif format == "nested":
        response = {"result": {"choices": [{"message": {"tool_calls": [
            {"id": "provider-id", "function": {"name": call["name"], "arguments": call["parameters"]}}
        ]}}]}}
    elif format == "list":
        response = text_reply([call])
    elif format == "wrapped":
        response = text_reply({"tool_calls": [call]})
    elif format == "string_arguments":
        response = text_reply({"id": "provider-id", "name": call["name"], "arguments": json.dumps(call["parameters"])})
    elif format == "nested_text":
        response = {"result": {"choices": [{"message": {"content": json.dumps(call)}}]}}
    else:
        response = text_reply(call)
        if format == "fenced":
            response["result"]["response"] = "```json\n" + response["result"]["response"] + "\n```"
    text, calls = parse_tool_response(response, 2)
    assert text == "" and len(calls) == 1
    assert calls[0]["id"] == "provider-id" and calls[0]["type"] == "function"
    assert calls[0]["function"]["name"] == "check_mandate_policy"
    assert json.loads(calls[0]["function"]["arguments"]) == policy_arguments(price=39.99)


def test_reported_escaped_apostrophe_is_accepted_as_text_inside_json():
    response = text_reply(action("check_mandate_policy", policy_arguments()))
    response["result"]["response"] = response["result"]["response"].replace("mandate's", r"mandate\'s")
    _, calls = parse_tool_response(response, 0)
    assert json.loads(calls[0]["function"]["arguments"])["justification"] == policy_arguments()["justification"]


@pytest.mark.parametrize("price", [True, None, "NaN", "Infinity", "$39.99", "39.99 USD", "", "1e999"])
def test_invalid_amounts_are_not_repaired_or_treated_as_approved(price):
    _, calls = parse_tool_response(text_reply(action("check_mandate_policy", policy_arguments(price))), 0)
    assert json.loads(calls[0]["function"]["arguments"])["price"] == price


@pytest.mark.parametrize("text", [
    '{"name":"create_authorized_order","parameters":',
    '{"name":"unknown_tool","parameters":{}}',
    '{"name":"check_mandate_policy","parameters":[],"arguments":{}}',
    '{"name":"check_mandate_policy","parameters":[]}',
    '{"name":"check_mandate_policy","parameters":{},"explanation":"example"}',
    '[]', '{"tool_calls":{}}',
])
def test_malformed_unknown_or_ambiguous_calls_fail_closed(text):
    with pytest.raises(ToolCallFormatError):
        parse_tool_response({"result": {"response": text}}, 0)


def test_prose_with_json_example_is_not_executed_and_native_calls_take_precedence():
    payload = action("create_authorized_order", {"amount": "39.99"})
    prose = "Example only: " + json.dumps(payload)
    assert parse_tool_response({"result": {"response": prose}}, 0) == (prose, [])
    _, calls = parse_tool_response({"result": {"response": json.dumps(payload), "tool_calls": [
        {"id": "real-call", "name": "search_products", "arguments": {"query": "power bank"}},
    ]}}, 0)
    assert len(calls) == 1 and calls[0]["function"]["name"] == "search_products"


def make_agent(pricing_response, *, price="39.99", automatic=True):
    plan = json.loads(pricing_response["result"]["response"])
    plan.update(min_price=20, max_price=50, auto_execute=automatic)
    mandate = {"id": "mandate", "user_id": "buyer", "description": "Buy a portable power bank 20000mAh+ under $50",
               "payment_token_id": "db-token"}
    database = FakeSupabase(mandate)
    policy = text_reply(action("check_mandate_policy", policy_arguments(price)))
    policy["result"]["response"] = policy["result"]["response"].replace("mandate's", r"mandate\'s")
    ai = SimpleNamespace(chat_with_tools=AsyncMock(side_effect=[
        {"result": {"response": json.dumps(plan)}},
        text_reply(action("search_products", {"query": "portable power bank 20000mAh+", "max_price": "50"})),
        policy,
        text_reply(action("create_authorized_order", {"product_name": PRODUCT, "amount": "39.99", "category": "electronics", "currency": "USD"})),
        text_reply(action("capture_authorized_payment", {"authorization_id": "auth"})),
        {"result": {"response": "Waiting for manual review."}},
    ]))
    paypal = MagicMock()
    paypal.create_authorized_order.return_value = {"id": "order", "status": "COMPLETED", "purchase_units": [
        {"payments": {"authorizations": [{"id": "auth"}]}}]}
    paypal.capture_authorization.return_value = {"id": "capture", "status": "COMPLETED"}
    return AgentOrchestrator(ai, paypal, database), mandate, database


def test_reported_text_call_reaches_policy_authorization_and_confirmed_capture(pricing_response):
    agent, mandate, database = make_agent(pricing_response)
    result = asyncio.run(agent.execute_mandate(mandate))
    assert result["status"] == "completed" and result["error"] is None
    agent.paypal.create_authorized_order.assert_called_once()
    assert agent.paypal.create_authorized_order.call_args.kwargs["amount"] == 39.99
    agent.paypal.capture_authorization.assert_called_once()
    events = database.table("transactions").select("*").execute().data
    assert [e["event_type"] for e in events] == ["constraints_defined", "search_started", "policy_approved", "purchase_confirmation_requested", "purchase_confirmation_auto_approved", "authorized", "captured"]
    # The next request contains a real assistant tool call and matching tool reply.
    messages = agent.ai.chat_with_tools.call_args_list[3].args[0]
    policy_call = next(c for m in messages for c in m.get("tool_calls", []) if c["function"]["name"] == "check_mandate_policy")
    assert json.loads(policy_call["function"]["arguments"])["price"] == 39.99
    assert any(m.get("tool_call_id") == policy_call["id"] and json.loads(m["content"])["decision"] == "APPROVED"
               for m in messages if m.get("name") == "check_mandate_policy")


@pytest.mark.parametrize("price", ["50.01", "39.99 USD", "NaN", True, "-1"])
def test_recovered_call_still_cancels_immediately_on_actual_rejection(pricing_response, price):
    agent, mandate, database = make_agent(pricing_response, price=price)
    assert asyncio.run(agent.execute_mandate(mandate))["status"] == "cancelled"
    assert agent.ai.chat_with_tools.await_count == 3
    agent.paypal.create_authorized_order.assert_not_called()
    agent.paypal.capture_authorization.assert_not_called()
    assert agent.lifecycle.current("mandate")["cancel_requested"]


def test_text_capture_cannot_bypass_manual_approval(pricing_response):
    agent, mandate, database = make_agent(pricing_response, automatic=False)
    assert asyncio.run(agent.execute_mandate(mandate))["status"] == "authorized"
    agent.paypal.capture_authorization.assert_not_called()
