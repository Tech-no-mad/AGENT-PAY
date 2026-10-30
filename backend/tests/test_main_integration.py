"""Regressions for reconciling main's BUG-9–15 fixes with the durable fixes."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.config import settings
from backend.services.agent_orchestrator import AgentOrchestrator
from backend.services.categories import category_allowed, normalize_category
from backend.services.cloudflare_ai import CloudflareAI
from backend.services.mandate_service import MandateService
from backend.services import supabase_client as db
from backend.tests.fake_supabase import FakeSupabase


def test_main_cloudflare_model_config_and_explicit_override_are_preserved(monkeypatch):
    monkeypatch.setattr(settings, "CLOUDFLARE_MODEL", "configured-model")
    assert CloudflareAI("account", "token").model == "configured-model"
    overridden = CloudflareAI("account", "token", model="explicit-model")
    assert overridden.model == "explicit-model" and overridden._base_url.endswith("/explicit-model")


@pytest.mark.parametrize("service_key,expected", [("service-key", "service-key"), ("", "anonymous-key")])
def test_main_development_client_fallback_is_preserved(monkeypatch, service_key, expected):
    monkeypatch.setattr(db, "_supabase_client", None)
    monkeypatch.setattr(settings, "SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setattr(settings, "SUPABASE_SERVICE_KEY", service_key)
    monkeypatch.setattr(settings, "SUPABASE_ANON_KEY", "anonymous-key")
    with patch.object(db, "create_client") as create:
        assert db.get_supabase() is create.return_value
        create.assert_called_once_with("https://example.supabase.co", expected)


@pytest.mark.parametrize("event", ["ORDER_FAILED", "CAPTURE_FAILED", "CAPTURE_DECLINED", "MANDATE_FAILED"])
def test_main_fuzzy_normalization_cannot_label_failures_as_success(event):
    assert db.normalize_transaction_event(event) == "failed"


def test_main_explicit_resource_id_contract_is_preserved():
    sb = FakeSupabase({"id": "mandate", "user_id": "buyer", "payment_token_id": "db-token", "description": "test"})
    row = db.log_transaction("mandate", "authorized", paypal_resource_id="resource", supabase_client=sb)
    assert row["paypal_resource_id"] == "resource"


@pytest.mark.parametrize("alias,expected", [("tech", "electronics"), ("travel", "flights"), ("grocery", "food"), (None, "general")])
def test_main_category_aliases_remain_supported(alias, expected):
    assert normalize_category(alias) == expected


def test_general_category_is_not_a_wildcard_for_restricted_mandates():
    assert not category_allowed("food", "general")
    assert not category_allowed("electronics", "general")
    assert not category_allowed("services", "general")
    assert category_allowed("physical_goods", "general")


def test_request_scoped_services_cancel_the_same_process_worker_durably():
    sb = FakeSupabase()
    paypal = MagicMock()

    async def scenario():
        waiting, resume = asyncio.Event(), asyncio.Event()
        async def chat(*args):
            waiting.set()
            await resume.wait()
            return {"result": {"response": "done"}}
        ai = SimpleNamespace(chat_with_tools=chat, parse_mandate=AsyncMock(return_value={
            "max_price": 100, "currency": "USD", "category": "electronics", "auto_execute": True}))
        creator = MandateService(sb, AgentOrchestrator(ai, paypal, sb), ai)
        row = await creator.create_mandate("buyer", "Headphones under $100", "db-token")
        await waiting.wait()
        worker = creator._active_tasks[row["id"]]
        canceller = MandateService(sb, AgentOrchestrator(AsyncMock(), MagicMock(), sb), AsyncMock())
        assert (await canceller.cancel_mandate(row["id"], "buyer"))["status"] == "cancelled"
        await worker
        assert row["id"] not in creator._active_tasks
        assert creator.lifecycle.current(row["id"])["cancel_requested"]

    asyncio.run(scenario())
    paypal.create_authorized_order.assert_not_called()
    paypal.capture_authorization.assert_not_called()
