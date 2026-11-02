"""Current-clock behavior at persistence and payment decision boundaries."""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.models.schemas import MandateConstraints
from backend.services.agent_orchestrator import AgentOrchestrator
from backend.services.mandate_lifecycle import MandateLifecycle
from backend.services.supabase_client import log_transaction, update_mandate_status
from backend.services.time_utils import parse_utc_timestamp, utc_now, utc_timestamp
from backend.tests.fake_supabase import FakeSupabase


def test_clock_reads_are_fresh_and_utc():
    before = datetime.now(timezone.utc)
    instant = utc_now()
    serialized = parse_utc_timestamp(utc_timestamp())
    after = datetime.now(timezone.utc)
    assert before <= instant <= serialized <= after
    assert instant.utcoffset() == timedelta(0)


@pytest.mark.parametrize("value", ["2030-01-01", "2030-01-01T00:00:00", "not-a-date"])
def test_deadlines_require_explicit_timezones(value):
    with pytest.raises(ValueError):
        parse_utc_timestamp(value)
    with pytest.raises(ValueError):
        MandateConstraints(deadline=value)


def test_offset_deadline_preserves_its_instant_when_normalized():
    local = "2030-01-02T00:30:00+05:30"
    assert parse_utc_timestamp(local).isoformat() == "2030-01-01T19:00:00+00:00"
    assert MandateConstraints(deadline=local).deadline == "2030-01-01T19:00:00+00:00"


def test_persistence_uses_current_clock_without_rewriting_previous_events():
    mandate = {"id": "mandate", "user_id": "buyer", "payment_token_id": "db-token", "description": "Headphones"}
    database = FakeSupabase(mandate)
    first = datetime(2026, 11, 2, 23, 59, 59, tzinfo=timezone.utc)
    references = [first + timedelta(seconds=offset) for offset in range(4)]
    with patch("backend.services.time_utils.datetime", wraps=datetime) as clock:
        clock.now.side_effect = references
        previous = log_transaction("mandate", "MANDATE_CREATED", supabase_client=database)
        current = log_transaction("mandate", "PRODUCT_SEARCH", supabase_client=database)
        updated = update_mandate_status("mandate", "searching", supabase_client=database)
        claim = MandateLifecycle(database, MagicMock()).claim("mandate", "authorize")
    assert previous["created_at"] == references[0].isoformat()
    assert current["created_at"] == references[1].isoformat()
    assert updated["updated_at"] == references[2].isoformat()
    assert claim["payment_started_at"] == references[3].isoformat()
    recorded = database.table("transactions").select("*").eq("id", previous["id"]).maybe_single().execute().data
    assert recorded["created_at"] == previous["created_at"]


@pytest.mark.parametrize("seconds,approved", [(-1, False), (0, False), (1, True)])
def test_policy_deadline_uses_current_time_including_exact_expiry(seconds, approved):
    now = datetime.now(timezone.utc)
    mandate = {"constraints": {"max_price": 150, "currency": "USD", "deadline": (now + timedelta(seconds=seconds)).isoformat()}}
    agent = AgentOrchestrator(AsyncMock(), MagicMock())
    with patch("backend.services.time_utils.datetime", wraps=datetime) as clock:
        clock.now.return_value = now
        decision = agent._check_policy({"price": 100, "currency": "USD"}, mandate)
    assert (decision["decision"] == "APPROVED") is approved


@pytest.mark.parametrize("deadline_kind", ["expired", "equal", "no_timezone", "invalid", "future"])
def test_capture_rechecks_stored_deadline_after_authorization(deadline_kind):
    now = datetime.now(timezone.utc)
    deadlines = {
        "expired": (now - timedelta(seconds=1)).isoformat(),
        "equal": now.isoformat(),
        "no_timezone": now.replace(tzinfo=None).isoformat(),
        "invalid": "invalid",
        "future": (now + timedelta(minutes=1)).isoformat(),
    }
    mandate = {
        "id": "mandate", "user_id": "buyer", "payment_token_id": "db-token", "description": "Headphones",
        "status": "authorized", "paypal_authorization_id": "owned-auth",
        "constraints": {"max_price": 150, "currency": "USD", "auto_execute": True},
    }
    database = FakeSupabase(mandate)
    # The caller's snapshot predates the stored deadline: capture must use the claim.
    database.table("mandates").update({"constraints": {
        **mandate["constraints"], "deadline": deadlines[deadline_kind],
    }}).eq("id", mandate["id"]).execute()
    paypal = MagicMock()
    paypal.capture_authorization.return_value = {"id": "capture", "status": "COMPLETED"}
    agent = AgentOrchestrator(AsyncMock(), paypal, database)
    execution = {"status": "authorized", "authorization_id": "owned-auth", "amount": 100, "currency": "USD"}
    with patch("backend.services.time_utils.datetime", wraps=datetime) as clock:
        clock.now.return_value = now
        response = asyncio.run(agent._capture({"authorization_id": "owned-auth"}, mandate, execution))
    current = agent.lifecycle.current(mandate["id"])
    assert current["payment_operation_id"] is None
    if deadline_kind == "future":
        assert response["status"] == "CAPTURED"
        paypal.capture_authorization.assert_called_once()
    else:
        assert "error" in response
        assert current["status"] == execution["status"] == "authorized"
        paypal.capture_authorization.assert_not_called()
