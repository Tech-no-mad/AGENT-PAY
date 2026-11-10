"""Recover transient AI failures without replaying searches or payments."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from backend.services.agent_orchestrator import AgentOrchestrator
from backend.services.cloudflare_ai import CloudflareAI, CloudflareAIError
from backend.tests.fake_supabase import FakeSupabase


@pytest.mark.parametrize("error_type", [httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout,
    httpx.PoolTimeout, httpx.ConnectError, httpx.ReadError, httpx.WriteError, httpx.RemoteProtocolError, TimeoutError])
def test_blank_transport_errors_retry_the_same_request(error_type):
    requests = []
    def provider(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            raise error_type("")
        return httpx.Response(200, json={"success": True, "result": {"response": "OK"}})
    async def scenario():
        client_type = httpx.AsyncClient
        with patch("backend.services.cloudflare_ai.httpx.AsyncClient", side_effect=lambda **kwargs:
                   client_type(transport=httpx.MockTransport(provider), **kwargs)), patch("asyncio.sleep", new_callable=AsyncMock) as sleep:
            result = await CloudflareAI("account", "token").chat_with_tools([{"role": "user", "content": "Find a quiet keyboard"}], [])
            assert sleep.await_count == 1
            return result
    assert asyncio.run(scenario())["result"]["response"] == "OK"
    assert len(requests) == 2
    assert requests[0]["messages"][1:] == requests[1]["messages"][1:]


def test_persistent_blank_error_has_a_nonempty_reason_and_bounded_retries():
    calls = []
    def provider(request):
        calls.append(request)
        raise httpx.ReadError("")
    async def scenario():
        client_type = httpx.AsyncClient
        with patch("backend.services.cloudflare_ai.httpx.AsyncClient", side_effect=lambda **kwargs:
                   client_type(transport=httpx.MockTransport(provider), **kwargs)), patch("asyncio.sleep", new_callable=AsyncMock) as sleep:
            with pytest.raises(CloudflareAIError, match="after 3 attempts.*ReadError") as raised:
                await CloudflareAI("account", "token").chat_with_tools([], [])
            assert isinstance(raised.value.__cause__, httpx.ReadError)
            assert sleep.await_count == 2
    asyncio.run(scenario())
    assert len(calls) == 3


@pytest.mark.parametrize("status,attempts", [(400, 1), (401, 1), (403, 1), (429, 3), (503, 3)])
def test_only_retryable_http_statuses_are_retried(status, attempts):
    requests = []
    def provider(request):
        requests.append(request)
        return httpx.Response(status, json={"success": False})
    async def scenario():
        client_type = httpx.AsyncClient
        with patch("backend.services.cloudflare_ai.httpx.AsyncClient", side_effect=lambda **kwargs:
                   client_type(transport=httpx.MockTransport(provider), **kwargs)), patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises((httpx.HTTPStatusError, CloudflareAIError)):
                await CloudflareAI("account", "token").chat_with_tools([], [])
    asyncio.run(scenario())
    assert len(requests) == attempts


@pytest.mark.parametrize("error", [RuntimeError(""), httpx.ReadError(""), TimeoutError(), ValueError("   ")])
def test_unexpected_blank_exception_after_search_is_recorded_with_type(error, pricing_response):
    mandate = {"id": "mandate", "user_id": "buyer", "payment_token_id": "db-token", "description": "Quiet keyboard"}
    sb, paypal = FakeSupabase(mandate), MagicMock()
    ai = SimpleNamespace(chat_with_tools=AsyncMock(side_effect=[pricing_response,
        {"result": {"tool_calls": [{"name": "search_products", "arguments": {"query": "quiet keyboard"}}]}}, error]))
    result = asyncio.run(AgentOrchestrator(ai, paypal, sb).execute_mandate(mandate))
    assert result["status"] == "failed" and result["error"].strip()
    row = sb.table("mandates").select("*").maybe_single().execute().data
    event = sb.table("transactions").select("*").eq("event_type", "failed").maybe_single().execute().data
    assert row["agent_reasoning"] == event["details"]["reason"] == result["error"]
    assert event["details"]["error_type"] == type(error).__name__
    assert event["details"]["last_tool"] == "search_products"
    paypal.create_authorized_order.assert_not_called()


def test_keyboard_purchase_recovers_after_search_without_replaying_any_actions(pricing_response):
    plan = json.loads(pricing_response["result"]["response"])
    plan.update(max_price=500, auto_execute=True, pricing_reasoning="Budget for an ergonomic quiet keyboard.")
    mandate = {"id": "mandate", "user_id": "buyer", "payment_token_id": "db-token",
               "description": "Order an ergonomic mechanical keyboard with quiet switches under $500."}
    sb, paypal = FakeSupabase(mandate), MagicMock()
    paypal.create_authorized_order.return_value = {"id": "order", "status": "COMPLETED", "purchase_units": [
        {"payments": {"authorizations": [{"id": "auth"}]}}]}
    paypal.capture_authorization.return_value = {"id": "capture", "status": "COMPLETED"}
    attempts = []
    product = {"product_name": "Demo Ergonomic Mechanical Keyboard with Quiet Switches", "price": 79.99,
               "currency": "USD", "category": "electronics", "justification": "Matches keyboard requirements"}
    def provider(request):
        attempts.append(json.loads(request.content))
        turn = len(attempts)
        if turn == 1:
            result = {"response": json.dumps(plan)}
        elif turn == 2:
            result = {"tool_calls": [{"name": "search_products", "arguments": {"query": "ergonomic mechanical keyboard with quiet switches"}}]}
        elif turn == 3:
            raise httpx.RemoteProtocolError("")
        elif turn == 4:
            result = {"tool_calls": [{"name": "check_mandate_policy", "arguments": product}]}
        elif turn == 5:
            result = {"tool_calls": [{"name": "create_authorized_order", "arguments": {**product, "amount": 79.99}}]}
        elif turn == 6:
            result = {"tool_calls": [{"name": "capture_authorized_payment", "arguments": {"authorization_id": "auth"}}]}
        elif turn == 7:
            result = {"response": "Purchased the ergonomic keyboard for $79.99."}
        else:
            raise AssertionError("Unexpected extra AI request")
        return httpx.Response(200, json={"success": True, "result": result})
    async def scenario():
        client_type = httpx.AsyncClient
        with patch("backend.services.cloudflare_ai.httpx.AsyncClient", side_effect=lambda **kwargs:
                   client_type(transport=httpx.MockTransport(provider), **kwargs)), patch("asyncio.sleep", new_callable=AsyncMock):
            return await AgentOrchestrator(CloudflareAI("account", "token"), paypal, sb).execute_mandate(mandate)
    result = asyncio.run(scenario())
    assert result["status"] == "completed" and result["error"] is None
    assert attempts[2]["messages"][1:] == attempts[3]["messages"][1:]
    paypal.create_authorized_order.assert_called_once()
    paypal.capture_authorization.assert_called_once()
    events = sb.table("transactions").select("*").execute().data
    assert sum(e["event_type"] == "search_started" for e in events) == 1
    assert sum(e["event_type"] == "purchase_confirmation_requested" for e in events) == 1
    assert not any(e["event_type"] == "failed" for e in events)
