"""Pricing must be the first AI call and finish before shopping/payment."""

import asyncio
from datetime import timedelta
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.services.agent_orchestrator import AgentOrchestrator
from backend.services.cloudflare_ai import ConstraintParseError
from backend.services.mandate_lifecycle import MandateLifecycle
from backend.services.mandate_service import MandateService
from backend.services.pricing_category_agent import PricingCategoryAgent
from backend.services.time_utils import utc_now
from backend.tests.fake_supabase import FakeSupabase


def pricing_plan(**overrides):
    return {
        "min_price": 40, "max_price": 150, "currency": "USD", "category": "electronics",
        "pricing_reasoning": "Estimated range for one pair of midrange headphones, not a live quote.",
        "deadline": None, "auto_execute": False, "additional_rules": "New items only",
        **overrides,
    }


def response(plan):
    return {"success": True, "result": {"response": json.dumps(plan)}}


@pytest.mark.parametrize("shape", ["flat", "nested", "fenced"])
def test_pricing_without_a_user_budget_supports_provider_response_shapes(shape):
    plan = pricing_plan()
    body = response(plan)
    if shape == "nested":
        body["result"] = {"choices": [{"message": {"content": json.dumps(plan)}}]}
    elif shape == "fenced":
        body["result"]["response"] = "```json\n" + json.dumps(plan) + "\n```"
    ai = SimpleNamespace(chat_with_tools=AsyncMock(return_value=body))
    actual = asyncio.run(PricingCategoryAgent(ai).define_constraints("Find good wireless headphones"))
    assert actual == plan
    assert ai.chat_with_tools.call_args.kwargs["tools"] == []
    messages = ai.chat_with_tools.call_args.args[0]
    assert json.loads(messages[1]["content"])["explicit_constraints"] == {}
    assert "not verified live quotes" in messages[0]["content"]


@pytest.mark.parametrize("changes", [
    {"max_price": None}, {"max_price": 0}, {"max_price": -1}, {"max_price": True},
    {"max_price": float("inf")}, {"max_price": "150"}, {"min_price": -1},
    {"min_price": True}, {"min_price": float("nan")}, {"min_price": 151},
    {"category": "made_up"}, {"category": None}, {"category": ""},
    {"pricing_reasoning": " "}, {"auto_execute": "true"}, {"extra": "unexpected"},
    {"deadline": "tomorrow"}, {"deadline": (utc_now() - timedelta(days=1)).isoformat()},
])
def test_invalid_plans_fail_closed(changes):
    ai = SimpleNamespace(chat_with_tools=AsyncMock(return_value=response(pricing_plan(**changes))))
    with pytest.raises(ConstraintParseError):
        asyncio.run(PricingCategoryAgent(ai).define_constraints("Headphones"))


@pytest.mark.parametrize("body", [
    {"success": False, "result": {"response": "{}"}},
    {"result": {"response": "Not JSON"}}, {"result": {"response": "[]"}},
    {"result": {"response": "{}"}}, {"result": {"tool_calls": [{"name": "search_products"}]}},
])
def test_malformed_or_failed_provider_response_stops_execution(body):
    mandate = {"id": "mandate", "user_id": "buyer", "description": "Headphones", "payment_token_id": "db-token"}
    db = FakeSupabase(mandate)
    ai = SimpleNamespace(chat_with_tools=AsyncMock(return_value=body))
    paypal = MagicMock()
    result = asyncio.run(AgentOrchestrator(ai, paypal, db).execute_mandate(mandate))
    assert result["status"] == "failed" and result["error"]
    assert ai.chat_with_tools.await_count == 1
    paypal.create_authorized_order.assert_not_called()
    events = db.table("transactions").select("*").execute().data
    assert len(events) == 1 and events[0]["event_type"] == "failed"
    assert events[0]["details"]["stage"] == "pricing"


def test_explicit_controls_tighten_budget_and_preserve_other_limits():
    ai = SimpleNamespace(chat_with_tools=AsyncMock(return_value=response(pricing_plan(auto_execute=True))))
    deadline = (utc_now() + timedelta(days=1)).isoformat()
    result = asyncio.run(PricingCategoryAgent(ai).define_constraints("Headphones", {
        "max_price": 80, "auto_execute": False, "deadline": deadline, "category": None,
    }))
    assert result["max_price"] == 80 and result["min_price"] == 40
    assert result["category"] == "electronics" and result["auto_execute"] is False
    assert result["deadline"] == deadline and result["additional_rules"] == "New items only"
    result = asyncio.run(PricingCategoryAgent(ai).define_constraints("Headphones", {"max_price": 300}))
    assert result["max_price"] == 150


def test_currency_mismatch_cannot_relabel_a_price_estimate():
    ai = SimpleNamespace(chat_with_tools=AsyncMock(return_value=response(pricing_plan())))
    with pytest.raises(ConstraintParseError):
        asyncio.run(PricingCategoryAgent(ai).define_constraints("Headphones", {"currency": "EUR"}))


def test_creation_to_authorization_runs_pricing_first_and_persists_before_shopping():
    db = FakeSupabase()
    paypal = MagicMock()
    paypal.create_authorized_order.return_value = {
        "id": "order", "status": "COMPLETED",
        "purchase_units": [{"payments": {"authorizations": [{"id": "auth"}]}}],
    }
    calls = []

    async def chat(messages, tools):
        calls.append(tools)
        if len(calls) == 1:
            assert tools == []
            assert db.table("mandates").select("*").maybe_single().execute().data["constraints"] == {"auto_execute": False}
            return response(pricing_plan())
        saved = db.table("mandates").select("*").maybe_single().execute().data
        assert saved["constraints"] == pricing_plan()
        assert "40.00 to 150.00 USD" in messages[1]["content"]
        assert "Category: electronics" in messages[1]["content"]
        if len(calls) == 2:
            return {"result": {"tool_calls": [{"name": "search_products", "arguments": {
                "query": "headphones", "category": "electronics", "max_price": 150, "currency": "USD",
            }}]}}
        if len(calls) == 3:
            return {"result": {"tool_calls": [{"name": "create_authorized_order", "arguments": {
                "amount": 75, "currency": "USD", "category": "electronics", "product_name": "Headphones",
            }}]}}
        return {"result": {"response": "Awaiting review."}}

    ai = SimpleNamespace(chat_with_tools=AsyncMock(side_effect=chat))
    orchestrator = AgentOrchestrator(ai, paypal, db)
    service = MandateService(db, orchestrator, ai)

    async def scenario():
        created = await service.create_mandate("buyer", "Buy good wireless headphones", "db-token", {"auto_execute": False})
        task = service._active_tasks[created["id"]]
        await task
        return orchestrator.lifecycle.current(created["id"])

    saved = asyncio.run(scenario())
    assert len(calls) == 4 and all(calls[1:])
    assert saved["status"] == "authorized"
    events = db.table("transactions").select("*").execute().data
    assert [event["event_type"] for event in events[:3]] == ["mandate_created", "constraints_defined", "search_started"]
    assert events[1]["details"]["pricing_reasoning"] == pricing_plan()["pricing_reasoning"]
    paypal.create_authorized_order.assert_called_once()
    paypal.capture_authorization.assert_not_called()
    for price, category in [(151, "electronics"), (75, "food")]:
        decision = orchestrator._check_policy({"price": price, "category": category, "currency": "USD"}, saved)
        assert decision["decision"] == "REJECTED"
    assert orchestrator._check_policy({"price": 30, "category": "electronics", "currency": "USD"}, saved)["decision"] == "APPROVED"


def test_cancellation_during_pricing_never_starts_search():
    mandate = {"id": "mandate", "user_id": "buyer", "description": "Headphones", "payment_token_id": "db-token"}
    db, paypal = FakeSupabase(mandate), MagicMock()
    ai = SimpleNamespace(chat_with_tools=None)

    async def scenario():
        waiting, resume = asyncio.Event(), asyncio.Event()
        async def chat(*args, **kwargs):
            waiting.set()
            await resume.wait()
            return response(pricing_plan())
        ai.chat_with_tools = AsyncMock(side_effect=chat)
        agent = AgentOrchestrator(ai, paypal, db)
        task = asyncio.create_task(agent.execute_mandate(mandate))
        await waiting.wait()
        MandateLifecycle(db, paypal).cancel("mandate", "buyer")
        resume.set()
        return await task

    assert asyncio.run(scenario())["status"] == "cancelled"
    assert ai.chat_with_tools.await_count == 1
    assert db.table("mandates").select("*").maybe_single().execute().data["constraints"] == {}
    paypal.create_authorized_order.assert_not_called()


def test_unpersisted_pricing_never_starts_shopping():
    mandate = {"id": "mandate", "user_id": "buyer", "description": "Headphones", "payment_token_id": "db-token"}
    ai = SimpleNamespace(chat_with_tools=AsyncMock(return_value=response(pricing_plan())))
    db, paypal = FakeSupabase(mandate), MagicMock()
    with patch("backend.services.agent_orchestrator.update_mandate_status", return_value=None):
        result = asyncio.run(AgentOrchestrator(ai, paypal, db).execute_mandate(mandate))
    assert "persist" in result["error"]
    assert ai.chat_with_tools.await_count == 1
    paypal.create_authorized_order.assert_not_called()
