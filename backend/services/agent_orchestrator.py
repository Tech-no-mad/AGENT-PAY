"""
Agent Orchestrator — drives the AI agent loop that processes mandates.

Flow:
  1. System prompt + user message built from the mandate
  2. Cloudflare AI called with tools in a loop (max 10 turns)
  3. Each tool_call is dispatched, results fed back
  4. Mandate status updated at every stage
  5. All actions logged to the transactions table
"""

from __future__ import annotations

import json
import logging
import random
import math
from typing import Any, Optional

from backend.services.cloudflare_ai import CloudflareAI
from backend.services.categories import category_allowed, normalize_category
from backend.services.mandate_lifecycle import (
    MandateLifecycle, ExecutionStopped, CancellationPending, CancellationFailed, PaymentOutcomeUnknown, field,
)
from backend.services.paypal_service import PayPalOrderBuildError, PayPalService
from backend.services.supabase_client import AuditLogError, get_supabase, resolve_payment_token, log_transaction, update_mandate_status
from backend.services.time_utils import parse_utc_timestamp, utc_now
from backend.tools.agent_tools import AGENT_TOOLS

logger = logging.getLogger(__name__)

# Maximum agentic loop iterations
MAX_TURNS = 10

# ---------------------------------------------------------------------------
# System prompt for the AI agent
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are AgentPay, an autonomous AI shopping agent. Your job is to fulfill \
a user's spending mandate by searching for products/services, verifying the \
purchase against the mandate's policy constraints, and completing the PayPal \
payment — all without human intervention.

## Rules
1. ALWAYS call `search_products` first to find options.
2. Pick the BEST option that satisfies the mandate.
3. ALWAYS call `check_mandate_policy` BEFORE creating an order.
4. If the policy check returns REJECTED, do NOT proceed — call `void_authorization` \
   if an authorization exists, then stop and explain why.
5. If APPROVED, call `create_authorized_order` to hold funds.
6. If auto_execute is true, immediately call `capture_authorized_payment`.
7. If auto_execute is false, stop after authorization and let the user review.
8. Never exceed the max_price constraint.
9. Prefer lower prices when quality is comparable.
10. Always provide clear reasoning for your choices.
11. Use the supplied current UTC timestamp for time-sensitive decisions. Keep the \
mandate's saved deadline and resolved travel/event dates fixed; do not shift them \
by reinterpreting the original relative wording on later turns.

## Response Format
After completing all actions, provide a brief summary of what you did and why.
"""


class AgentOrchestrator:
    """Drives the agentic tool-calling loop for a single mandate."""

    def __init__(
        self,
        cloudflare_ai: CloudflareAI,
        paypal_service: PayPalService,
        supabase_client: Any = None,
    ) -> None:
        self.ai = cloudflare_ai
        self.paypal = paypal_service
        self.sb = supabase_client

    @property
    def lifecycle(self) -> MandateLifecycle:
        return MandateLifecycle(self.sb if self.sb is not None else get_supabase(), self.paypal)

    def _refresh_running(self, mandate: dict[str, Any]) -> None:
        current = self.lifecycle.current(mandate["id"])
        if current.get("cancel_requested"):
            current = self.lifecycle.cancel(current["id"], current["user_id"])
            raise ExecutionStopped(current)
        if current["status"] in ("completed", "cancelled", "failed"):
            raise ExecutionStopped(current)
        mandate.update(current)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def execute_mandate(self, mandate_dict: dict[str, Any]) -> dict[str, Any]:
        """
        Run the full agent loop for a mandate.

        Returns an AgentExecutionResult-compatible dict.
        """
        mandate_id = mandate_dict["id"]
        logger.info("Agent starting execution for mandate %s", mandate_id)

        constraints = mandate_dict.get("constraints") or {}
        user_message = self._build_user_message(mandate_dict)

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ]

        result: dict[str, Any] = {
            "mandate_id": mandate_id,
            "status": "failed",
            "agent_reasoning": "",
            "order_id": None,
            "authorization_id": None,
            "capture_id": None,
            "amount": None,
            "currency": None,
            "product_name": None,
            "error": None,
        }

        try:
            self._refresh_running(mandate_dict)
            update_mandate_status(mandate_id, "searching", supabase_client=self.sb)
            for turn in range(MAX_TURNS):
                logger.info("Agent turn %d/%d for mandate %s", turn + 1, MAX_TURNS, mandate_id)

                response = await self.ai.chat_with_tools(messages, AGENT_TOOLS)
                self._refresh_running(mandate_dict)

                # Parse the Cloudflare response
                ai_result = response.get("result", {})

                # Prefer the full message: Cloudflare's flattened tool_calls
                # omit IDs that must be preserved in the next request.
                choices = ai_result.get("choices") or []
                assistant_message = (choices[0].get("message") or {}) if choices else {}
                response_text = assistant_message.get("content") or ai_result.get("response") or ""
                raw_tool_calls = assistant_message.get("tool_calls") or ai_result.get("tool_calls") or []
                tool_calls = []
                for index, tc in enumerate(raw_tool_calls):
                    function = tc.get("function") or tc
                    arguments = function.get("arguments", {})
                    tool_calls.append({
                        "id": tc.get("id") or f"call_{turn}_{index}",
                        "type": "function",
                        "function": {
                            "name": function.get("name", ""),
                            "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments),
                        },
                    })

                if not tool_calls:
                    # No more tool calls — agent is done
                    result["agent_reasoning"] = response_text or result["agent_reasoning"]
                    break

                # Add assistant message with tool calls to history
                messages.append({
                    "role": "assistant",
                    "content": response_text or "",
                    "tool_calls": tool_calls,
                })

                # Execute each tool call
                for tc in tool_calls:
                    fn_name = tc["function"]["name"]
                    fn_args_raw = tc["function"]["arguments"]
                    tc_id = tc["id"]

                    # Parse arguments
                    if isinstance(fn_args_raw, str):
                        try:
                            fn_args = json.loads(fn_args_raw)
                        except json.JSONDecodeError:
                            fn_args = {}
                    else:
                        fn_args = fn_args_raw

                    logger.info("Tool call: %s(%s)", fn_name, fn_args)

                    tool_result = await self._execute_tool(fn_name, fn_args, mandate_dict, result)

                    # Feed tool result back to the model
                    messages.append({
                        "role": "tool",
                        "name": fn_name,
                        "tool_call_id": tc_id,
                        "content": json.dumps(tool_result) if not isinstance(tool_result, str) else tool_result,
                    })

            # Final status update
            extra_fields: dict[str, Any] = {
                "agent_reasoning": result.get("agent_reasoning"),
            }
            if result.get("order_id"):
                extra_fields["paypal_order_id"] = result["order_id"]
            if result.get("authorization_id"):
                extra_fields["paypal_authorization_id"] = result["authorization_id"]
            if result.get("amount"):
                extra_fields["executed_amount"] = result["amount"]

            self._refresh_running(mandate_dict)
            updated = update_mandate_status(mandate_id, result["status"], extra_fields, supabase_client=self.sb)
            if not updated:
                result["status"] = self.lifecycle.current(mandate_id)["status"]
            logger.info("Agent finished mandate %s — status=%s", mandate_id, result["status"])

        except ExecutionStopped as exc:
            result["status"] = exc.mandate["status"]
            result["agent_reasoning"] = exc.mandate.get("agent_reasoning") or str(exc)
        except (CancellationPending, CancellationFailed, PaymentOutcomeUnknown) as exc:
            result["status"] = self.lifecycle.current(mandate_id)["status"]
            result["error"] = str(exc)
        except Exception as exc:
            logger.exception("Agent execution failed for mandate %s", mandate_id)
            result["error"] = str(exc)
            current = self.lifecycle.current(mandate_id)
            if current.get("payment_operation_id") or current.get("cancel_requested") or current["status"] in ("authorized", "completed", "cancelled"):
                result["status"] = current["status"]
            else:
                update_mandate_status(mandate_id, "failed", {"agent_reasoning": f"Error: {exc}"}, supabase_client=self.sb)
                result["status"] = self.lifecycle.current(mandate_id)["status"]

        return result

    # ------------------------------------------------------------------
    # Tool dispatch
    # ------------------------------------------------------------------

    async def _execute_tool(
        self,
        name: str,
        args: dict[str, Any],
        mandate: dict[str, Any],
        result: dict[str, Any],
    ) -> Any:
        """Dispatch a tool call to the appropriate handler."""
        handlers = {
            "search_products": self._search_products,
            "check_mandate_policy": self._check_policy_wrapper,
            "create_authorized_order": self._create_order,
            "capture_authorized_payment": self._capture,
            "void_authorization": self._void,
        }

        handler = handlers.get(name)
        if not handler:
            return {"error": f"Unknown tool: {name}"}

        try:
            self._refresh_running(mandate)
            return await handler(args, mandate, result)
        except (ExecutionStopped, CancellationPending, CancellationFailed, PaymentOutcomeUnknown, AuditLogError):
            raise
        except Exception as exc:
            logger.error("Tool %s failed: %s", name, exc)
            return {"error": str(exc)}

    # ------------------------------------------------------------------
    # Tool implementations
    # ------------------------------------------------------------------

    async def _search_products(
        self, args: dict[str, Any], mandate: dict[str, Any], result: dict[str, Any],
    ) -> dict[str, Any]:
        """Mock product search — returns realistic fake data for the demo."""
        update_mandate_status(mandate["id"], "searching", supabase_client=self.sb)

        query = args.get("query", "")
        category = normalize_category(args.get("category", "general"))
        max_price = args.get("max_price") or (mandate.get("constraints") or {}).get("max_price") or 500
        currency = args.get("currency", "USD")

        # Generate mock products based on query keywords
        products = _generate_mock_products(query, category, max_price, currency)

        log_transaction(
            mandate_id=mandate["id"],
            event_type="PRODUCT_SEARCH",
            status="completed",
            details={"query": query, "results_count": len(products)},
            user_id=mandate["user_id"], supabase_client=self.sb,
        )

        return {"products": products, "total_results": len(products)}

    async def _check_policy_wrapper(
        self, args: dict[str, Any], mandate: dict[str, Any], result: dict[str, Any],
    ) -> dict[str, Any]:
        """Check mandate policy and return approval/rejection."""
        update_mandate_status(mandate["id"], "evaluating", supabase_client=self.sb)
        policy_result = self._check_policy(args, mandate)

        log_transaction(
            mandate_id=mandate["id"],
            event_type="POLICY_CHECK",
            amount=args.get("price"),
            currency=args.get("currency", "USD"),
            status=policy_result["decision"],
            user_id=mandate["user_id"], supabase_client=self.sb,
            details={
                "product_name": args.get("product_name"),
                "justification": args.get("justification"),
                "reason": policy_result.get("reason"),
            },
        )

        return policy_result

    def _check_policy(self, args: dict[str, Any], mandate: dict[str, Any]) -> dict[str, Any]:
        """
        Compare a proposed purchase against mandate constraints.
        Returns {"decision": "APPROVED"|"REJECTED", "reason": "..."}.
        """
        constraints = mandate.get("constraints") or {}
        price = args.get("price", 0)
        currency = args.get("currency", "USD")
        product_name = args.get("product_name", "")
        category = args.get("category", "")

        if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
            return {"decision": "REJECTED", "reason": "Amount must be a finite positive number"}
        if not isinstance(currency, str) or not currency:
            return {"decision": "REJECTED", "reason": "Currency is required"}

        # Check max_price
        max_price = constraints.get("max_price")
        if isinstance(max_price, bool) or not isinstance(max_price, (int, float)) or not math.isfinite(max_price) or max_price <= 0:
            return {"decision": "REJECTED", "reason": "A finite positive mandate budget is required"}
        if max_price is not None and price > max_price:
            return {
                "decision": "REJECTED",
                "reason": f"Price ${price:.2f} exceeds maximum allowed ${max_price:.2f}",
            }

        # Check currency
        allowed_currency = constraints.get("currency", "USD")
        if currency.upper() != allowed_currency.upper():
            return {
                "decision": "REJECTED",
                "reason": f"Currency {currency} does not match required {allowed_currency}",
            }

        # Check category if specified
        required_category = constraints.get("category")
        if required_category and not category:
            return {"decision": "REJECTED", "reason": "Purchase category is required"}
        if required_category and category:
            if not category_allowed(required_category, category):
                return {
                    "decision": "REJECTED",
                    "reason": f"Category '{category}' does not match required '{required_category}'",
                }

        # Check deadline
        deadline_error = self._deadline_error(constraints.get("deadline"))
        if deadline_error:
            return {"decision": "REJECTED", "reason": deadline_error}

        # Check additional_rules — basic keyword blocking
        additional_rules = constraints.get("additional_rules", "")
        if isinstance(additional_rules, list):
            additional_rules = " ".join(additional_rules)
        if additional_rules:
            # Simple check: if rules say "no" + category, reject
            lower_rules = additional_rules.lower()
            if "do not" in lower_rules or "don't" in lower_rules or "never" in lower_rules:
                blocked_keywords = [w for w in lower_rules.split() if len(w) > 3]
                for kw in blocked_keywords:
                    if kw in product_name.lower():
                        return {
                            "decision": "REJECTED",
                            "reason": f"Product name matches blocked keyword from additional rules",
                        }

        return {
            "decision": "APPROVED",
            "reason": f"Purchase of '{product_name}' at ${price:.2f} {currency} meets all mandate constraints",
        }

    @staticmethod
    def _authorization_id(order: Any) -> Any:
        for unit in field(order, "purchase_units", []) or []:
            for authorization in field(field(unit, "payments", {}), "authorizations", []) or []:
                return field(authorization, "id")
        return None

    def _check_cancel_after_claim(self, claim: dict[str, Any]) -> None:
        if self.lifecycle.current(claim["id"]).get("cancel_requested"):
            self.lifecycle.finish(claim)
            cancelled = self.lifecycle.cancel(claim["id"], claim["user_id"])
            raise ExecutionStopped(cancelled)

    async def _create_order(
        self, args: dict[str, Any], mandate: dict[str, Any], result: dict[str, Any],
    ) -> dict[str, Any]:
        """Validate, claim and authorize; cancellation cannot race a later capture."""
        if result.get("order_id"):
            return {"error": "An order already exists for this execution"}
        amount = args.get("amount", 0)
        policy = self._check_policy({**args, "price": amount}, mandate)
        if policy["decision"] != "APPROVED":
            return {"error": "Purchase rejected by mandate policy", "reason": policy["reason"]}
        token = resolve_payment_token(self.lifecycle.sb, mandate["payment_token_id"], mandate["user_id"])
        claim = self.lifecycle.claim(mandate["id"], "authorize")
        # Constraints may have changed since the AI request was sent.
        policy = self._check_policy({**args, "price": amount}, claim)
        if policy["decision"] != "APPROVED":
            self.lifecycle.finish(claim)
            return {"error": "Purchase rejected by current mandate policy", "reason": policy["reason"]}
        self._check_cancel_after_claim(claim)
        currency = args.get("currency", "USD")
        description = args.get("description", mandate.get("description", ""))
        try:
            order = self.paypal.create_authorized_order(
                amount=amount, currency=currency, description=description,
                payment_token_id=token["paypal_token_id"], mandate_id=mandate["id"],
                request_id=claim["payment_operation_id"],
            )
            order_id = field(order, "id")
            if not order_id:
                raise ValueError("PayPal returned no order ID")
            result["order_id"] = order_id
            self.lifecycle.checkpoint(claim, paypal_order_id=order_id)
            authorization_id = self._authorization_id(order)
            if not authorization_id and field(order, "status") in ("CREATED", "APPROVED"):
                # An order without an authorization holds no funds yet.
                self._check_cancel_after_claim(claim)
                from uuid import NAMESPACE_URL, uuid5
                authorized = self.paypal.authorize_order(order_id, request_id=str(
                    uuid5(NAMESPACE_URL, claim["payment_operation_id"] + ":authorize")))
                authorization_id = self._authorization_id(authorized)
            if not authorization_id:
                raise ValueError("PayPal authorization outcome is unknown")
        except (ExecutionStopped, CancellationPending, CancellationFailed):
            raise
        except PayPalOrderBuildError as exc:
            reason = str(exc)
            self.lifecycle.finish(claim, status="failed", agent_reasoning=reason)
            result.update(status="failed", error=reason, agent_reasoning=reason)
            log_transaction(mandate["id"], "ORDER_FAILED", user_id=mandate["user_id"], supabase_client=self.sb,
                            status="failed", details={"error": reason, "request_sent": False})
            return {"error": reason, "status": "FAILED"}
        except Exception as exc:
            self.lifecycle.checkpoint(claim, agent_reasoning=f"Authorization requires reconciliation: {exc}")
            log_transaction(mandate["id"], "ORDER_FAILED", user_id=mandate["user_id"], supabase_client=self.sb,
                            paypal_order_id=result.get("order_id"), status="unknown", details={"error": str(exc)})
            self.lifecycle.uncertain(claim, "Authorization outcome is unknown; reconcile before retrying")
        result.update(authorization_id=authorization_id, amount=amount, currency=currency,
                      product_name=args.get("product_name", ""), status="authorized")
        self.lifecycle.finish(claim, status="authorized", paypal_order_id=order_id,
                              paypal_authorization_id=authorization_id, executed_amount=amount)
        log_transaction(mandate["id"], "ORDER_AUTHORIZED", user_id=mandate["user_id"], supabase_client=self.sb,
                        paypal_order_id=order_id, paypal_authorization_id=authorization_id, amount=amount,
                        currency=currency, status="authorized", details={"product_name": result["product_name"]})
        self._refresh_running(mandate)
        return {"order_id": order_id, "authorization_id": authorization_id, "status": "AUTHORIZED",
                "amount": amount, "currency": currency}

    async def _capture(
        self, args: dict[str, Any], mandate: dict[str, Any], result: dict[str, Any],
    ) -> dict[str, Any]:
        """Capture only this execution's authorization under an exclusive claim."""
        if (mandate.get("constraints") or {}).get("auto_execute") is not True:
            return {"error": "Manual approval required; agent capture is disabled"}
        authorization_id = result.get("authorization_id")
        if args.get("authorization_id") and args["authorization_id"] != authorization_id:
            return {"error": "Authorization does not belong to this execution"}
        if result.get("status") != "authorized" or not authorization_id:
            return {"error": "Execution is not awaiting capture"}
        claim = self.lifecycle.claim(mandate["id"], "capture")
        if ((claim.get("constraints") or {}).get("auto_execute") is not True
                or claim.get("paypal_authorization_id") != authorization_id):
            self.lifecycle.finish(claim)
            return {"error": "Current mandate does not permit capturing this authorization"}
        self._check_cancel_after_claim(claim)
        deadline_error = self._deadline_error((claim.get("constraints") or {}).get("deadline"))
        if deadline_error:
            self.lifecycle.finish(claim, agent_reasoning=deadline_error)
            return {"error": "Capture rejected by mandate deadline", "reason": deadline_error}
        try:
            captured = self.paypal.capture_authorization(authorization_id, request_id=claim["payment_operation_id"])
        except Exception as exc:
            log_transaction(mandate["id"], "CAPTURE_FAILED", user_id=mandate["user_id"], supabase_client=self.sb,
                            paypal_authorization_id=authorization_id, status="unknown", details={"error": str(exc)})
            self.lifecycle.uncertain(claim, "Capture outcome is unknown; reconcile before retrying")
        capture_id, capture_status = field(captured, "id"), field(captured, "status")
        if capture_status in ("DECLINED", "DENIED", "FAILED"):
            self.lifecycle.finish(claim)
            return {"error": "PayPal declined the capture"}
        if capture_status != "COMPLETED" or not capture_id:
            self.lifecycle.uncertain(claim, "PayPal capture is pending or unconfirmed; reconciliation required")
        result.update(capture_id=capture_id, status="completed")
        self.lifecycle.finish(claim, status="completed")
        log_transaction(mandate["id"], "PAYMENT_CAPTURED", user_id=mandate["user_id"], supabase_client=self.sb,
                        paypal_authorization_id=authorization_id, paypal_capture_id=capture_id,
                        amount=result.get("amount"), currency=result.get("currency"), status="completed")
        return {"capture_id": capture_id, "status": "CAPTURED"}

    async def _void(
        self, args: dict[str, Any], mandate: dict[str, Any], result: dict[str, Any],
    ) -> dict[str, Any]:
        authorization_id = result.get("authorization_id")
        if args.get("authorization_id") and args["authorization_id"] != authorization_id:
            return {"error": "Authorization does not belong to this execution"}
        current = self.lifecycle.current(mandate["id"])
        if not authorization_id or current.get("paypal_authorization_id") != authorization_id:
            return {"error": "No authorization belonging to this execution is available to void"}
        cancelled = self.lifecycle.cancel(mandate["id"], mandate["user_id"])
        result["status"] = cancelled["status"]
        return {"status": "VOIDED", "authorization_id": authorization_id}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _deadline_error(deadline: Optional[str]) -> Optional[str]:
        if not deadline:
            return None
        try:
            deadline_dt = parse_utc_timestamp(deadline)
            current_time = utc_now()
            if deadline_dt <= current_time:
                return f"Mandate deadline has passed ({deadline_dt.isoformat()}; now {current_time.isoformat()})"
        except (ValueError, TypeError, AttributeError):
            return "Invalid mandate deadline"
        return None

    @staticmethod
    def _build_user_message(mandate: dict[str, Any]) -> str:
        """Build the user message from mandate data."""
        constraints = mandate.get("constraints") or {}
        parts = [f"Please fulfill this mandate: {mandate['description']}"]

        if constraints.get("max_price"):
            parts.append(f"Maximum budget: ${constraints['max_price']:.2f} {constraints.get('currency', 'USD')}")
        if constraints.get("category"):
            parts.append(f"Category: {constraints['category']}")
        if constraints.get("deadline"):
            parts.append(f"Deadline to complete the purchase: {constraints['deadline']}")
        if constraints.get("auto_execute") is False:
            parts.append("Auto-execute is DISABLED — stop after authorization for user review.")
        if constraints.get("additional_rules"):
            parts.append(f"Additional rules: {constraints['additional_rules']}")

        parts.append(f"Payment token ID: {mandate.get('payment_token_id', 'N/A')}")

        return "\n".join(parts)


# ---------------------------------------------------------------------------
# Mock product data generator
# ---------------------------------------------------------------------------

def _generate_mock_products(
    query: str, category: str, max_price: float, currency: str,
) -> list[dict[str, Any]]:
    """Generate realistic mock products for demo purposes."""
    random.seed(hash(query) % 2**32)

    templates: dict[str, list[dict[str, Any]]] = {
        "electronics": [
            {"name": "Sony WH-1000XM5 Wireless Headphones", "base_price": 348.00, "rating": 4.8},
            {"name": "Apple AirPods Pro (2nd Gen)", "base_price": 249.00, "rating": 4.7},
            {"name": "Samsung Galaxy Buds2 Pro", "base_price": 189.99, "rating": 4.5},
            {"name": "Bose QuietComfort 45", "base_price": 279.00, "rating": 4.6},
            {"name": "JBL Tune 770NC", "base_price": 99.95, "rating": 4.3},
        ],
        "flights": [
            {"name": "Economy Round-trip — United Airlines", "base_price": 320.00, "rating": 4.0},
            {"name": "Economy Round-trip — Delta Airlines", "base_price": 285.00, "rating": 4.2},
            {"name": "Economy Round-trip — Southwest", "base_price": 198.00, "rating": 3.9},
            {"name": "Premium Economy — American Airlines", "base_price": 450.00, "rating": 4.3},
            {"name": "Basic Economy — Spirit Airlines", "base_price": 129.00, "rating": 3.2},
        ],
        "food": [
            {"name": "Weekly Meal Kit — HelloFresh (4 meals)", "base_price": 49.99, "rating": 4.4},
            {"name": "Monthly Snack Box — Universal Yums", "base_price": 35.00, "rating": 4.6},
            {"name": "Gourmet Coffee Sampler Pack", "base_price": 42.00, "rating": 4.7},
            {"name": "Artisan Chocolate Collection", "base_price": 28.50, "rating": 4.5},
            {"name": "Premium Tea Variety Box", "base_price": 22.00, "rating": 4.3},
        ],
        "general": [
            {"name": "Amazon Basics Backpack", "base_price": 45.00, "rating": 4.3},
            {"name": "Kindle Paperwhite (16GB)", "base_price": 139.99, "rating": 4.7},
            {"name": "Anker PowerCore 20000mAh", "base_price": 39.99, "rating": 4.6},
            {"name": "Hydro Flask 32oz Water Bottle", "base_price": 44.95, "rating": 4.8},
            {"name": "Moleskine Classic Notebook", "base_price": 19.50, "rating": 4.5},
        ],
    }

    # Pick the best matching template
    category_lower = normalize_category(category) if category else "general"
    template_key = "general"
    for key in templates:
        if key in category_lower or key in query.lower():
            template_key = key
            break

    products = []
    for item in templates[template_key]:
        # Slight price variation
        price_variation = random.uniform(0.85, 1.15)
        price = round(item["base_price"] * price_variation, 2)

        if price <= max_price:
            products.append({
                "name": item["name"],
                "price": price,
                "currency": currency,
                "category": template_key,
                "rating": item["rating"],
                "in_stock": True,
                "estimated_delivery": "3-5 business days",
            })

    # Sort by price
    products.sort(key=lambda p: p["price"])
    return products[:5]
