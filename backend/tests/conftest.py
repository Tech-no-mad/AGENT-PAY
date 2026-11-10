import json

import pytest


@pytest.fixture(autouse=True)
def confirmation_window(monkeypatch):
    """Existing payment regressions run the real timeout gate without wall-clock delay."""
    monkeypatch.setattr("backend.services.purchase_confirmation.CONFIRMATION_SECONDS", 0)


@pytest.fixture
def pricing_response():
    """A valid first-stage response for existing shopping-loop regressions."""
    return {"result": {"response": json.dumps({
        "min_price": 40, "max_price": 100, "currency": "USD", "category": "electronics",
        "pricing_reasoning": "Estimate for one pair of headphones.", "deadline": None,
        "auto_execute": True, "additional_rules": None,
    })}}
