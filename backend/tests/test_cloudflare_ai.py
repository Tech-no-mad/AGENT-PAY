"""Workers AI request contracts exercised through the HTTP transport."""

import asyncio
from datetime import datetime, timedelta, timezone
import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from backend.services.agent_orchestrator import AgentOrchestrator
from backend.services.cloudflare_ai import CloudflareAI
from backend.tests.fake_supabase import FakeSupabase


def test_mandate_extraction_omits_empty_tools_and_preserves_constraints():
    constraints = {
        "max_price": 150,
        "currency": "USD",
        "category": "electronics",
        "deadline": None,
        "auto_execute": False,
        "additional_rules": "No used items",
    }
    requests = []

    def provider(request):
        payload = json.loads(request.content)
        requests.append(payload)
        # Workers AI rejects an empty tools array before running inference.
        if payload.get("tools") == []:
            return httpx.Response(400, json={
                "success": False,
                "errors": [{"code": 8007, "message": "tools must not be an empty array"}],
            })
        return httpx.Response(200, json={
            "success": True, "result": {"response": json.dumps(constraints)},
        })

    async def extract():
        client = httpx.AsyncClient(transport=httpx.MockTransport(provider))
        with patch("backend.services.cloudflare_ai.httpx.AsyncClient", return_value=client):
            return await CloudflareAI("account", "token").parse_mandate(
                "Find new headphones under 150 USD; ask for approval before buying."
            )

    assert asyncio.run(extract()) == constraints
    assert len(requests) == 1
    assert "tools" not in requests[0]


def test_relative_date_extraction_receives_current_time_and_preserves_departure_rule():
    reference = datetime(2026, 11, 2, 16, 45, tzinfo=timezone.utc)
    constraints = {
        "max_price": 350, "currency": "USD", "category": "flights",
        "deadline": None, "auto_execute": True,
        "additional_rules": "Round-trip SFO to JFK, economy, departing 2026-11-06",
    }
    requests = []

    def provider(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={
            "success": True, "result": {"response": json.dumps(constraints)},
        })

    async def extract():
        client = httpx.AsyncClient(transport=httpx.MockTransport(provider))
        with patch("backend.services.cloudflare_ai.httpx.AsyncClient", return_value=client), \
                patch("backend.services.time_utils.datetime", wraps=datetime) as clock:
            clock.now.return_value = reference
            return await CloudflareAI("account", "token").parse_mandate(
                "Book a round-trip flight SFO to JFK under $350 departing this Friday economy class."
            )

    parsed = asyncio.run(extract())
    messages = requests[0]["messages"]
    assert reference.isoformat() in messages[0]["content"]
    assert "Monday" in messages[0]["content"]
    assert parsed["deadline"] is None
    assert "2026-11-06" in parsed["additional_rules"]


def test_agent_requests_keep_nonempty_tools_and_return_tool_calls():
    tools = [{
        "type": "function",
        "function": {
            "name": "search_products",
            "description": "Search products",
            "parameters": {"type": "object", "properties": {}},
        },
    }]
    body = {"success": True, "result": {"tool_calls": [
        {"name": "search_products", "arguments": {}},
    ]}}

    def provider(request):
        payload = json.loads(request.content)
        assert payload["tools"] == tools
        assert payload["messages"][0]["role"] == "system"
        assert "Current UTC date and time:" in payload["messages"][0]["content"]
        assert payload["messages"][1:] == [{"role": "user", "content": "Find headphones"}]
        return httpx.Response(200, json=body)

    async def chat():
        client = httpx.AsyncClient(transport=httpx.MockTransport(provider))
        with patch("backend.services.cloudflare_ai.httpx.AsyncClient", return_value=client):
            return await CloudflareAI("account", "token").chat_with_tools(
                [{"role": "user", "content": "Find headphones"}], tools,
            )

    assert asyncio.run(chat()) == body


@pytest.mark.parametrize("has_system_message", [False, True])
def test_clock_refreshes_on_retries_and_later_requests_without_rewriting_history(has_system_message):
    first = datetime(2026, 11, 2, 23, 59, 59, tzinfo=timezone.utc)
    references = [first, first + timedelta(seconds=2), first + timedelta(seconds=3)]
    messages = [{"role": "user", "content": "Find headphones"}]
    if has_system_message:
        messages.insert(0, {"role": "system", "content": "Keep the original shopping policy."})
    original = json.loads(json.dumps(messages))
    requests = []

    def provider(request):
        payload = json.loads(request.content)
        requests.append(payload)
        status = 429 if len(requests) == 1 else 200
        return httpx.Response(status, json={"success": status == 200, "result": {"response": "OK"}})

    async def chat_twice():
        client_class = httpx.AsyncClient
        with patch("backend.services.cloudflare_ai.httpx.AsyncClient", side_effect=lambda **kwargs:
                   client_class(transport=httpx.MockTransport(provider), **kwargs)), \
                patch("backend.services.time_utils.datetime", wraps=datetime) as clock, \
                patch("asyncio.sleep", new_callable=AsyncMock):
            clock.now.side_effect = references
            ai = CloudflareAI("account", "token")
            await ai.chat_with_tools(messages, [])
            await ai.chat_with_tools(messages, [])

    asyncio.run(chat_twice())
    assert messages == original
    assert len(requests) == 3
    for index, (payload, reference) in enumerate(zip(requests, references)):
        assert reference.isoformat() in payload["messages"][0]["content"]
        assert len([message for message in payload["messages"] if message["role"] == "system"]) == 1
        if index:
            assert references[index - 1].isoformat() not in payload["messages"][0]["content"]


@pytest.mark.parametrize("response_format", ["flat", "nested", "both"])
def test_agent_tool_history_has_matching_ids_and_function_objects(response_format):
    mandate = {
        "id": "mandate", "user_id": "buyer", "payment_token_id": "db-token",
        "description": "Find headphones under 150 USD",
        "constraints": {"max_price": 150, "currency": "USD", "category": "electronics"},
    }
    database = FakeSupabase(mandate)
    paypal = MagicMock()
    requests = []

    def provider(request):
        payload = json.loads(request.content)
        turn = len(requests)
        requests.append(payload)
        calls = [call for message in payload["messages"] for call in message.get("tool_calls", [])]
        replies = [message for message in payload["messages"] if message["role"] == "tool"]
        assert len(calls) == len(replies) == turn * 2
        assert len({call["id"] for call in calls}) == len(calls)
        for call, reply in zip(calls, replies):
            assert call["type"] == "function"
            assert call["function"]["name"] == "search_products"
            assert json.loads(call["function"]["arguments"])["category"] == "electronics"
            assert reply["tool_call_id"] == call["id"]
            assert json.loads(reply["content"])["products"]
        if response_format != "flat":
            assert [call["id"] for call in calls] == [
                f"provider-call-{previous}-{index}" for previous in range(turn) for index in range(2)
            ]

        if turn == 2:
            message = {"role": "assistant", "content": "Search complete; no purchase made."}
            result = {"response": message["content"]}
        else:
            flat_calls = [{
                "name": "search_products",
                "arguments": {"query": query, "category": "electronics", "max_price": 150, "currency": "USD"},
            } for query in ("headphones", "earbuds")]
            message = {"role": "assistant", "content": None, "tool_calls": [{
                "id": f"provider-call-{turn}-{index}", "type": "function",
                "function": {"name": call["name"], "arguments": json.dumps(call["arguments"])},
            } for index, call in enumerate(flat_calls)]}
            result = {"response": None, "tool_calls": flat_calls}
        if response_format == "nested":
            result = {}
        if response_format != "flat":
            result["choices"] = [{"message": message}]
        return httpx.Response(200, json={"success": True, "result": result})

    async def execute():
        client_class = httpx.AsyncClient
        with patch("backend.services.cloudflare_ai.httpx.AsyncClient", side_effect=lambda **kwargs:
                   client_class(transport=httpx.MockTransport(provider), **kwargs)):
            ai = CloudflareAI("account", "token")
            return await AgentOrchestrator(ai, paypal, database).execute_mandate(mandate)

    result = asyncio.run(execute())
    assert result["error"] is None
    assert result["agent_reasoning"] == "Search complete; no purchase made."
    assert len(requests) == 3
    paypal.create_authorized_order.assert_not_called()
    paypal.capture_authorization.assert_not_called()
