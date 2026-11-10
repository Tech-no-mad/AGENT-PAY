"""Regression coverage for irrelevant mock matches and silent post-search failures."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.services.agent_orchestrator import AgentOrchestrator, _generate_mock_products
from backend.tests.fake_supabase import FakeSupabase


QUERY = "high-capacity portable power bank 20000mAh+"


@pytest.mark.parametrize("category", ["electronics", "physical_goods", "general"])
@pytest.mark.parametrize("query", [QUERY, "portable charger 20,000 mAh", "powerbank 20k mAh"])
def test_power_bank_queries_return_relevant_products_with_capacity(category, query):
    products = _generate_mock_products(query, category, 120, "USD")
    assert products
    assert all("Power Bank" in p["name"] and p["category"] == "electronics" for p in products)
    assert all(p["capacity_mah"] >= 20000 and p["price"] <= 120 for p in products)
    assert products == _generate_mock_products(query, category, 120, "USD")


@pytest.mark.parametrize("query,category,ceiling", [
    (QUERY, "electronics", 1), ("power bank 50000mAh+", "electronics", 500),
    ("tablet computer", "electronics", 500), ("hotel in Paris", "hotels", 500),
])
def test_no_match_never_substitutes_an_unrelated_product(query, category, ceiling):
    assert _generate_mock_products(query, category, ceiling, "USD") == []


def test_keyboard_query_does_not_return_headphones():
    products = _generate_mock_products("ergonomic mechanical keyboard with quiet switches", "electronics", 120, "USD")
    assert products and all("Keyboard" in product["name"] for product in products)


def test_power_bank_price_is_consistent_across_query_wording_and_under_45():
    products = _generate_mock_products(QUERY, "electronics", 45, "USD")
    assert products and products[0]["price"] == 39.99
    assert products == _generate_mock_products("portable charger 20000mAh", "electronics", 45, "USD")


def scenario_agent(pricing_response, responses):
    plan = json.loads(pricing_response["result"]["response"])
    plan.update(min_price=20, max_price=60, auto_execute=False)
    mandate = {"id": "mandate", "user_id": "buyer", "description": QUERY, "payment_token_id": "db-token"}
    db = FakeSupabase(mandate)
    ai = SimpleNamespace(chat_with_tools=AsyncMock(side_effect=[
        {"result": {"response": json.dumps(plan)}}, *responses,
    ]))
    agent = AgentOrchestrator(ai, MagicMock(), db)
    return agent, mandate, db


def search_response():
    # Currency/category/budget are optional tool arguments and must inherit the mandate.
    return {"result": {"tool_calls": [{"name": "search_products", "arguments": {"query": QUERY}}]}}


def test_search_audit_retains_actual_results_and_bounded_defaults(pricing_response):
    agent, mandate, db = scenario_agent(pricing_response, [search_response(), {"result": {"response": "No purchase made."}}])
    asyncio.run(agent.execute_mandate(mandate))
    search = db.table("transactions").select("*").eq("event_type", "search_started").maybe_single().execute().data
    details = search["details"]
    assert details["source"] == "demo_catalog" and details["category"] == "electronics"
    assert details["max_price"] == 60 and details["results_count"] == len(details["products"]) > 0
    assert all(p["capacity_mah"] >= 20000 and p["price"] <= 60 for p in details["products"])
    assert agent._check_policy({"price": details["products"][0]["price"], "category": "electronics", "currency": "USD"}, mandate)["decision"] == "APPROVED"


@pytest.mark.parametrize("next_response", [
    {"result": {"response": "No suitable product meets the requirements."}},
    {"result": {"response": ""}},
    {"success": False, "errors": [{"message": "Provider error"}]},
    RuntimeError("AI request timed out"),
])
def test_post_search_failure_is_saved_and_audited(pricing_response, next_response):
    agent, mandate, db = scenario_agent(pricing_response, [search_response(), next_response])
    result = asyncio.run(agent.execute_mandate(mandate))
    assert result["status"] == "failed" and result["error"]
    saved = agent.lifecycle.current(mandate["id"])
    assert saved["status"] == "failed" and saved["agent_reasoning"]
    events = db.table("transactions").select("*").execute().data
    assert [e["event_type"] for e in events] == ["constraints_defined", "search_started", "failed"]
    failure = events[-1]["details"]
    assert failure["reason"] and failure["last_tool"] == "search_products"
    assert failure["stage"] == "shopping_model"
    agent.paypal.create_authorized_order.assert_not_called()


def test_turn_limit_has_an_explicit_failure_reason(pricing_response):
    agent, mandate, db = scenario_agent(pricing_response, [search_response(), search_response()])
    with patch("backend.services.agent_orchestrator.MAX_TURNS", 2):
        result = asyncio.run(agent.execute_mandate(mandate))
    assert "2-turn limit" in result["error"]
    failure = db.table("transactions").select("*").eq("event_type", "failed").maybe_single().execute().data
    assert failure["details"]["reason"] == result["error"]


def test_valid_power_bank_can_proceed_to_authorization(pricing_response):
    calls = [search_response(), {"result": {"tool_calls": [
        {"name": "check_mandate_policy", "arguments": {"product_name": "Demo USB-C Portable Power Bank 20000mAh", "price": 40, "currency": "USD", "category": "electronics"}},
        {"name": "create_authorized_order", "arguments": {"product_name": "Demo USB-C Portable Power Bank 20000mAh", "amount": 40, "currency": "USD", "category": "electronics"}},
    ]}}, {"result": {"response": "Awaiting review."}}]
    agent, mandate, db = scenario_agent(pricing_response, calls)
    agent.paypal.create_authorized_order.return_value = {"id": "order", "status": "COMPLETED", "purchase_units": [{"payments": {"authorizations": [{"id": "auth"}]}}]}
    result = asyncio.run(agent.execute_mandate(mandate))
    assert result["status"] == "authorized" and result["error"] is None
    agent.paypal.create_authorized_order.assert_called_once()
    agent.paypal.capture_authorization.assert_not_called()
    assert db.table("transactions").select("*").eq("event_type", "failed").execute().data == []
