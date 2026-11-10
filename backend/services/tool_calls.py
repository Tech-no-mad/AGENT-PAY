"""Normalize model tool-call encodings before dispatch, never policy decisions."""

from __future__ import annotations

import json
import math
import re
from typing import Any

from backend.tools.agent_tools import AGENT_TOOLS


_FUNCTIONS = {tool["function"]["name"]: tool["function"] for tool in AGENT_TOOLS}
_DECIMAL_NUMBER = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?\Z")


class ToolCallFormatError(ValueError):
    """The model attempted an action but did not supply an executable call."""


def _decode_json(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Some models escape apostrophes inside double-quoted JSON strings.
        # Removing only this invalid escape cannot introduce JSON structure.
        cleaned = re.sub(r"(?<!\\)((?:\\\\)*)\\'", r"\1'", text)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise ToolCallFormatError("AI returned malformed tool-call JSON; no action was executed") from exc


def _text_calls(text: str) -> list[dict[str, Any]]:
    """Accept only a complete JSON action, not JSON embedded in prose."""
    candidate = text.strip()
    if candidate.startswith("```"):
        fenced = re.fullmatch(r"```(?:json)?\s*\n([\s\S]*?)\n?```", candidate, re.IGNORECASE)
        if not fenced:
            raise ToolCallFormatError("AI returned an incomplete tool-call code block")
        candidate = fenced[1].strip()
    if not candidate.startswith(("{", "[")):
        return []
    parsed = _decode_json(candidate)
    if isinstance(parsed, dict) and set(parsed) == {"tool_calls"}:
        parsed = parsed["tool_calls"]
    calls = parsed if isinstance(parsed, list) else [parsed]
    if not calls:
        raise ToolCallFormatError("AI returned an empty tool-call list")
    allowed_keys = {"name", "arguments", "parameters", "function", "id", "type"}
    for call in calls:
        if not isinstance(call, dict) or not set(call).issubset(allowed_keys):
            raise ToolCallFormatError("AI returned JSON that is not a recognized tool call")
    return calls


def _normalize_call(call: Any, turn: int, index: int) -> dict[str, Any]:
    if not isinstance(call, dict):
        raise ToolCallFormatError("AI tool call must be an object")
    function = call.get("function", call)
    if not isinstance(function, dict) or not isinstance(function.get("name"), str):
        raise ToolCallFormatError("AI tool call is missing its function name")
    name = function["name"]
    if name not in _FUNCTIONS:
        raise ToolCallFormatError(f"AI requested an unsupported tool: {name}")
    if "arguments" in function and "parameters" in function:
        raise ToolCallFormatError("AI supplied ambiguous tool arguments and parameters")
    arguments = function.get("arguments", function.get("parameters"))
    if isinstance(arguments, str):
        arguments = _decode_json(arguments)
    if not isinstance(arguments, dict):
        raise ToolCallFormatError(f"AI tool arguments for {name} must be a JSON object")
    arguments = dict(arguments)
    # Convert plain decimal strings only on fields declared numeric by the
    # tool schema. Invalid values still reach the strict policy check and reject.
    for field, schema in _FUNCTIONS[name]["parameters"]["properties"].items():
        value = arguments.get(field)
        if schema.get("type") == "number" and isinstance(value, str) and _DECIMAL_NUMBER.fullmatch(value):
            number = float(value)
            if math.isfinite(number):
                arguments[field] = number
    identifier = call.get("id") or f"call_{turn}_{index}"
    if not isinstance(identifier, str):
        raise ToolCallFormatError("AI tool-call identifier must be text")
    return {"id": identifier, "type": "function", "function": {
        "name": name, "arguments": json.dumps(arguments),
    }}


def parse_tool_response(response: Any, turn: int) -> tuple[str, list[dict[str, Any]]]:
    """Support native/nested calls and complete JSON calls emitted as text."""
    if not isinstance(response, dict) or response.get("success") is False:
        raise ToolCallFormatError("Shopping AI provider returned an unsuccessful response")
    result = response.get("result")
    if not isinstance(result, dict):
        raise ToolCallFormatError("Shopping AI provider returned an invalid response")
    choices = result.get("choices") or []
    if not isinstance(choices, list) or (choices and not isinstance(choices[0], dict)):
        raise ToolCallFormatError("Shopping AI provider returned invalid choices")
    message = (choices[0].get("message") or {}) if choices else {}
    if not isinstance(message, dict):
        raise ToolCallFormatError("Shopping AI provider returned an invalid message")
    text = message.get("content") or result.get("response") or ""
    if not isinstance(text, str):
        raise ToolCallFormatError("Shopping AI response content must be text")
    calls = message.get("tool_calls") or result.get("tool_calls") or []
    if not calls:
        calls = _text_calls(text)
        if calls:
            text = ""
    if not isinstance(calls, list):
        raise ToolCallFormatError("AI tool calls must be a list")
    return text, [_normalize_call(call, turn, index) for index, call in enumerate(calls)]
