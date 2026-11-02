"""
Cloudflare Workers AI integration — chat completions with function calling
using the configured Cloudflare Workers AI model.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

import httpx
from backend.models.schemas import ExtractedMandateConstraints
from backend.services.categories import CATEGORIES
from backend.services.time_utils import utc_now

from backend.config import settings

logger = logging.getLogger(__name__)

# Maximum retries for transient failures
_MAX_RETRIES = 3
_RETRY_BACKOFF = [1.0, 2.0, 4.0]


class ConstraintParseError(ValueError):
    """Extraction failed; no mandate may be started with invented defaults."""


class CloudflareAI:
    """Async wrapper around the Cloudflare Workers AI REST API."""

    def __init__(
        self,
        account_id: str,
        api_token: str,
        model: Optional[str] = None,
    ) -> None:
        self.account_id = account_id
        self.api_token = api_token
        self.model = model or settings.CLOUDFLARE_MODEL or "@cf/meta/llama-3.3-70b-instruct-fp8-fast"
        self._base_url = (
            f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{self.model}"
        )
        logger.info("CloudflareAI initialised — model=%s", self.model)

    # ------------------------------------------------------------------
    # Core chat-with-tools call
    # ------------------------------------------------------------------

    async def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """
        Send a chat-completion request with function-calling tools.

        Returns the parsed JSON body from the Cloudflare API.
        """
        payload: dict[str, Any] = {
            "messages": messages,
        }
        # Workers AI rejects tools=[]; extraction needs a plain chat request.
        if tools:
            payload["tools"] = tools

        last_error: Optional[Exception] = None
        for attempt in range(_MAX_RETRIES):
            try:
                async with httpx.AsyncClient(timeout=60.0) as http:
                    # Refresh the clock for every extraction, agent turn and retry.
                    # Copy the first message so historical conversation is not mutated.
                    now = utc_now()
                    clock_context = f"Current UTC date and time: {now.isoformat()} ({now:%A})."
                    request_messages = list(messages)
                    if request_messages and request_messages[0].get("role") == "system":
                        request_messages[0] = {
                            **request_messages[0],
                            "content": clock_context + "\n\n" + (request_messages[0].get("content") or ""),
                        }
                    else:
                        request_messages.insert(0, {"role": "system", "content": clock_context})
                    resp = await http.post(
                        self._base_url,
                        json={**payload, "messages": request_messages},
                        headers={
                            "Authorization": f"Bearer {self.api_token}",
                            "Content-Type": "application/json",
                        },
                    )
                    resp.raise_for_status()
                    body = resp.json()

                    if not body.get("success", True):
                        errors = body.get("errors", [])
                        logger.warning("Cloudflare AI returned errors: %s", errors)

                    return body

            except httpx.HTTPStatusError as exc:
                last_error = exc
                # Don't retry 4xx (except 429)
                if exc.response.status_code == 429 or exc.response.status_code >= 500:
                    wait = _RETRY_BACKOFF[min(attempt, len(_RETRY_BACKOFF) - 1)]
                    logger.warning(
                        "Cloudflare AI %s (attempt %d/%d) — retrying in %.1fs",
                        exc.response.status_code,
                        attempt + 1,
                        _MAX_RETRIES,
                        wait,
                    )
                    import asyncio
                    await asyncio.sleep(wait)
                else:
                    raise
            except (httpx.ConnectError, httpx.ReadTimeout) as exc:
                last_error = exc
                wait = _RETRY_BACKOFF[min(attempt, len(_RETRY_BACKOFF) - 1)]
                logger.warning(
                    "Cloudflare AI network error (attempt %d/%d): %s — retrying in %.1fs",
                    attempt + 1,
                    _MAX_RETRIES,
                    exc,
                    wait,
                )
                import asyncio
                await asyncio.sleep(wait)

        raise RuntimeError(f"Cloudflare AI request failed after {_MAX_RETRIES} attempts: {last_error}")

    # ------------------------------------------------------------------
    # Mandate parsing helper
    # ------------------------------------------------------------------

    async def parse_mandate(self, description: str) -> dict[str, Any]:
        """
        Use AI to extract structured constraints from a natural-language
        mandate description.

        Returns a dict compatible with MandateConstraints.
        """
        system_prompt = (
            "You are a JSON extraction assistant. Given a natural-language spending mandate, "
            "extract structured constraints. Resolve relative dates such as 'this Friday' using the current "
            "UTC date and time in the system context, never a year from training data. "
            "Respond with ONLY a valid JSON object (no markdown, no explanation) "
            "with these fields:\n"
            '  "max_price": number or null,\n'
            '  "currency": string (ISO 4217, default "USD"),\n'
            '  "category": string or null,\n'
            '  "deadline": string (ISO 8601 date-time with timezone) or null,\n'
            '  "auto_execute": boolean (default true),\n'
            '  "additional_rules": string or null\n'
            f"Use canonical broad categories where applicable: {', '.join(CATEGORIES)}. "
            "Never invent a budget or deadline. Use null when none is specified. "
            "deadline means an explicitly requested latest time to COMPLETE THE PURCHASE, such as 'buy by Friday'. "
            "Departure dates, hotel check-in dates, event dates, and delivery dates are product requirements, "
            "not purchase deadlines: preserve them with resolved ISO calendar dates in additional_rules. "
            "For example, 'book a flight departing this Friday' has deadline=null; keep its departure date in additional_rules. "
            "Use UTC unless the instructions specify a timezone. For an explicit purchase deadline with a date "
            "but no time, use 23:59:59 on that date in the applicable timezone. "
            "Use auto_execute=false if approval is requested. Include all six fields."
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": description},
        ]

        try:
            result = await self.chat_with_tools(messages, tools=[])
            if not isinstance(result, dict) or result.get("success") is False:
                raise ValueError("Extraction provider returned an unsuccessful response")
            response = result.get("result")
            response_text = response.get("response") if isinstance(response, dict) else response

            if isinstance(response_text, str):
                # Strip markdown code fences if present
                cleaned = response_text.strip()
                if cleaned.startswith("```"):
                    cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0]
                parsed = json.loads(cleaned)
            else:
                parsed = response_text

            return ExtractedMandateConstraints.model_validate(parsed).model_dump()
        except (ValueError, KeyError, TypeError, httpx.HTTPError, RuntimeError) as exc:
            logger.warning("Mandate extraction rejected: %s", type(exc).__name__)
            raise ConstraintParseError("Unable to safely parse spending constraints; review the instructions and retry") from exc
