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
from datetime import datetime, timezone
from typing import Any, Optional

from backend.services.cloudflare_ai import CloudflareAI
from backend.services.paypal_service import PayPalService
from backend.services.supabase_client import get_supabase, resolve_payment_token, log_transaction, update_mandate_status
from backend.tools.agent_tools import AGENT_TOOLS

logger = logging.getLogger(__name__)

# Maximum agentic loop iterations
MAX_TURNS = 10

CATEGORY_ALIASES: dict[str, str] = {
    "product": "electronics",
    "products": "electronics",
    "physical goods": "electronics",
    "electronics": "electronics",
    "electronic": "electronics",
    "tech": "electronics",
    "gadget": "electronics",
    "gadgets": "electronics",
    "flight": "flights",
    "flights": "flights",
    "travel": "flights",
    "plane": "flights",
    "airline": "flights",
    "food": "food",
    "foods": "food",
    "grocery": "food",
    "groceries": "food",
    "meal": "food",
    "dining": "food",
    "general": "general",
    "goods": "general",
    "service": "general",
    "services": "general",
}


def normalize_category(cat: Optional[str]) -> str:
    """Normalize a category name to the policy engine's standard domain."""
    if not cat:
        return "general"
    cleaned = str(cat).strip().lower()
    return CATEGORY_ALIASES.get(cleaned, cleaned)


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
        self._cancelled_mandates: set[str] = set()

    def cancel_mandate(self, mandate_id: str) -> None:
        """Register that a mandate has been cancelled."""
        self._cancelled_mandates.add(mandate_id)

    def _is_cancelled(self, mandate_id: str) -> bool:
        """Check whether a mandate has been marked cancelled in memory or database."""
        if mandate_id in self._cancelled_mandates:
            return True
        if self.sb:
            try:
                res = self.sb.table("mandates").select("status").eq("id", mandate_id).maybe_single().execute()
                if res and res.data and res.data.get("status") == "cancelled":
                    self._cancelled_mandates.add(mandate_id)
                    return True
            except Exception:
                pass
        return False

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

        if self._is_cancelled(mandate_id):
            logger.info("Mandate %s was cancelled before execution started", mandate_id)
            return {
                "mandate_id": mandate_id,
                "status": "cancelled",
                "agent_reasoning": "Mandate cancelled before execution started",
                "order_id": None,
                "authorization_id": None,
                "capture_id": None,
                "amount": None,
                "currency": None,
                "product_name": None,
                "error": None,
            }

        # Update status → searching
        update_mandate_status(mandate_id, "searching")

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
            for turn in range(MAX_TURNS):
                if self._is_cancelled(mandate_id):
                    logger.info("Mandate %s was cancelled during turn %d; aborting", mandate_id, turn + 1)
                    if result.get("authorization_id") and result.get("status") == "authorized":
                        try:
                            self.paypal.void_authorization(result["authorization_id"])
                            log_transaction(
                                mandate_id=mandate_id,
                                user_id=mandate_dict.get("user_id"),
                                event_type="voided",
                                paypal_resource_id=result["authorization_id"],
                                details={"reason": "cancelled_during_turn"},
                            )
                        except Exception as void_err:
                            logger.error("Failed to void authorization during cancellation: %s", void_err)
                    result["status"] = "cancelled"
                    break

                logger.info("Agent turn %d/%d for mandate %s", turn + 1, MAX_TURNS, mandate_id)

                response = await self.ai.chat_with_tools(messages, AGENT_TOOLS)

                # Parse the Cloudflare response
                ai_result = response.get("result", {})

                # Handle tool calls
                tool_calls = ai_result.get("tool_calls")
                response_text = ai_result.get("response", "")

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
                    if self._is_cancelled(mandate_id):
                        logger.info("Mandate %s was cancelled before tool dispatch; aborting", mandate_id)
                        result["status"] = "cancelled"
                        break

                    fn_name = tc.get("name", "")
                    fn_args_raw = tc.get("arguments", {})
                    tc_id = tc.get("id", fn_name)

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

                if self._is_cancelled(mandate_id):
                    result["status"] = "cancelled"
                    break

            if self._is_cancelled(mandate_id) or result.get("status") == "cancelled":
                logger.info("Mandate %s ended in cancelled state; preserving without overwrite", mandate_id)
                result["status"] = "cancelled"
                return result

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

            if not self._is_cancelled(mandate_id):
                update_mandate_status(mandate_id, result["status"], extra_fields)
            else:
                result["status"] = "cancelled"
            logger.info("Agent finished mandate %s — status=%s", mandate_id, result["status"])

        except asyncio.CancelledError:
            logger.info("Execution task for mandate %s cancelled via asyncio", mandate_id)
            self.cancel_mandate(mandate_id)
            if result.get("authorization_id") and result.get("status") == "authorized":
                try:
                    self.paypal.void_authorization(result["authorization_id"])
                    log_transaction(
                        mandate_id=mandate_id,
                        user_id=mandate_dict.get("user_id"),
                        event_type="voided",
                        paypal_resource_id=result["authorization_id"],
                        details={"reason": "asyncio_task_cancelled"},
                    )
                except Exception as void_err:
                    logger.error("Failed to void authorization on task cancellation: %s", void_err)
            result["status"] = "cancelled"
            raise
        except Exception as exc:
            logger.exception("Agent execution failed for mandate %s", mandate_id)
            result["status"] = "failed"
            result["error"] = str(exc)
            if not self._is_cancelled(mandate_id):
                update_mandate_status(mandate_id, "failed", {"agent_reasoning": f"Error: {exc}"})
            else:
                result["status"] = "cancelled"
        finally:
            self._cancelled_mandates.discard(mandate_id)

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
        if self._is_cancelled(mandate.get("id", "")):
            return {"error": "Mandate execution was cancelled"}

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
            return await handler(args, mandate, result)
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
        if self._is_cancelled(mandate.get("id", "")):
            return {"error": "Mandate execution was cancelled"}

        update_mandate_status(mandate["id"], "searching")

        query = args.get("query", "")
        category = normalize_category(args.get("category", "general"))
        max_price = args.get("max_price") or (mandate.get("constraints") or {}).get("max_price") or 500
        currency = args.get("currency", "USD")

        # Generate mock products based on query keywords
        products = _generate_mock_products(query, category, max_price, currency)

        log_transaction(
            mandate_id=mandate["id"],
            user_id=mandate.get("user_id"),
            event_type="search_started",
            status="completed",
            details={"query": query, "results_count": len(products)},
        )

        return {"products": products, "total_results": len(products)}

    async def _check_policy_wrapper(
        self, args: dict[str, Any], mandate: dict[str, Any], result: dict[str, Any],
    ) -> dict[str, Any]:
        """Check mandate policy and return approval/rejection."""
        if self._is_cancelled(mandate.get("id", "")):
            return {"decision": "REJECTED", "reason": "Mandate execution was cancelled"}

        update_mandate_status(mandate["id"], "evaluating")
        policy_result = self._check_policy(args, mandate)

        evt = "policy_approved" if policy_result.get("decision") == "APPROVED" else "policy_rejected"
        log_transaction(
            mandate_id=mandate["id"],
            user_id=mandate.get("user_id"),
            event_type=evt,
            amount=args.get("price"),
            currency=args.get("currency", "USD"),
            status=policy_result.get("decision"),
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
            norm_req = normalize_category(required_category)
            norm_actual = normalize_category(category)
            if norm_req != "general" and norm_actual != "general" and norm_req != norm_actual:
                if norm_req not in norm_actual and norm_actual not in norm_req:
                    return {
                        "decision": "REJECTED",
                        "reason": f"Category '{category}' does not match required '{required_category}'",
                    }

        # Check deadline
        deadline = constraints.get("deadline")
        if deadline:
            try:
                deadline_dt = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
                if datetime.now(timezone.utc) > deadline_dt:
                    return {
                        "decision": "REJECTED",
                        "reason": "Mandate deadline has passed",
                    }
            except (ValueError, TypeError):
                return {"decision": "REJECTED", "reason": "Invalid mandate deadline"}

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

    async def _create_order(
        self, args: dict[str, Any], mandate: dict[str, Any], result: dict[str, Any],
    ) -> dict[str, Any]:
        """Create and authorize a PayPal order."""
        if self._is_cancelled(mandate.get("id", "")):
            return {"error": "Mandate execution was cancelled"}

        amount = args.get("amount", 0)
        currency = args.get("currency", "USD")
        description = args.get("description", mandate.get("description", ""))
        product_name = args.get("product_name", "")
        payment_token_id = mandate.get("payment_token_id", "")
        mandate_id = mandate["id"]

        if result.get("order_id"):
            return {"error": "An order already exists for this execution"}
        policy = self._check_policy({**args, "price": amount}, mandate)
        if policy["decision"] != "APPROVED":
            return {"error": "Purchase rejected by mandate policy", "reason": policy["reason"]}
        token = resolve_payment_token(self.sb or get_supabase(), payment_token_id, mandate["user_id"])
        payment_token_id = token["paypal_token_id"]

        try:
            # Create order with AUTHORIZE intent
            order = self.paypal.create_authorized_order(
                amount=amount,
                currency=currency,
                description=description,
                payment_token_id=payment_token_id,
                mandate_id=mandate_id,
            )

            order_id = order.id if hasattr(order, "id") else order.get("id", "")
            order_status = order.status if hasattr(order, "status") else order.get("status", "")

            # Extract authorization ID if the order was auto-authorized
            authorization_id = None
            if hasattr(order, "purchase_units"):
                for pu in order.purchase_units:
                    if hasattr(pu, "payments") and pu.payments and hasattr(pu.payments, "authorizations"):
                        for auth in pu.payments.authorizations:
                            authorization_id = auth.id if hasattr(auth, "id") else auth.get("id")
                            break

            # If not auto-authorized, explicitly authorize
            if not authorization_id and order_status == "CREATED":
                auth_result = self.paypal.authorize_order(order_id)
                if hasattr(auth_result, "purchase_units"):
                    for pu in auth_result.purchase_units:
                        if hasattr(pu, "payments") and pu.payments and hasattr(pu.payments, "authorizations"):
                            for auth in pu.payments.authorizations:
                                authorization_id = auth.id if hasattr(auth, "id") else auth.get("id")
                                break

            result["order_id"] = order_id
            result["authorization_id"] = authorization_id
            result["amount"] = amount
            result["currency"] = currency
            result["product_name"] = product_name
            result["status"] = "authorized" if authorization_id else "failed"

            update_mandate_status(mandate_id, result["status"], {
                "paypal_order_id": order_id,
                "paypal_authorization_id": authorization_id,
                "executed_amount": amount,
            })

            log_transaction(
                mandate_id=mandate_id,
                user_id=mandate.get("user_id"),
                event_type="authorized",
                paypal_order_id=order_id,
                paypal_authorization_id=authorization_id,
                amount=amount,
                currency=currency,
                status="authorized",
                details={"product_name": product_name, "description": description},
            )

            return {
                "order_id": order_id,
                "authorization_id": authorization_id,
                "status": "AUTHORIZED",
                "amount": amount,
                "currency": currency,
            }

        except Exception as exc:
            logger.error("Order creation failed: %s", exc)
            log_transaction(
                mandate_id=mandate_id,
                user_id=mandate.get("user_id"),
                event_type="failed",
                status="failed",
                details={"error": str(exc)},
            )
            return {"error": f"Failed to create order: {exc}"}

    async def _capture(
        self, args: dict[str, Any], mandate: dict[str, Any], result: dict[str, Any],
    ) -> dict[str, Any]:
        """Capture an authorized payment."""
        if self._is_cancelled(mandate.get("id", "")):
            return {"error": "Mandate execution was cancelled"}

        if (mandate.get("constraints") or {}).get("auto_execute") is not True:
            return {"error": "Manual approval required; agent capture is disabled"}
        authorization_id = result.get("authorization_id")
        if args.get("authorization_id") and args["authorization_id"] != authorization_id:
            return {"error": "Authorization does not belong to this execution"}
        if result.get("status") != "authorized":
            return {"error": "Execution is not awaiting capture"}
        if not authorization_id:
            return {"error": "No authorization_id available to capture"}

        try:
            capture_result = self.paypal.capture_authorization(authorization_id)
            capture_id = (
                capture_result.id if hasattr(capture_result, "id") else capture_result.get("id", "")
            )
            capture_status = capture_result.status if hasattr(capture_result, "status") else capture_result.get("status")
            if capture_status != "COMPLETED" or not capture_id:
                return {"error": "PayPal has not confirmed a completed capture"}

            result["capture_id"] = capture_id
            result["status"] = "completed"

            update_mandate_status(mandate["id"], "completed")

            log_transaction(
                mandate_id=mandate["id"],
                user_id=mandate.get("user_id"),
                event_type="captured",
                paypal_authorization_id=authorization_id,
                paypal_capture_id=capture_id,
                amount=result.get("amount"),
                currency=result.get("currency"),
                status="completed",
            )

            return {"capture_id": capture_id, "status": "CAPTURED"}

        except Exception as exc:
            logger.error("Capture failed: %s", exc)
            log_transaction(
                mandate_id=mandate["id"],
                user_id=mandate.get("user_id"),
                event_type="failed",
                paypal_authorization_id=authorization_id,
                status="failed",
                details={"error": str(exc)},
            )
            return {"error": f"Capture failed: {exc}"}

    async def _void(
        self, args: dict[str, Any], mandate: dict[str, Any], result: dict[str, Any],
    ) -> dict[str, Any]:
        """Void an authorization."""
        authorization_id = result.get("authorization_id")
        if args.get("authorization_id") and args["authorization_id"] != authorization_id:
            return {"error": "Authorization does not belong to this execution"}
        if not authorization_id:
            return {"error": "No authorization_id available to void"}

        try:
            self.paypal.void_authorization(authorization_id)
            result["status"] = "cancelled"

            update_mandate_status(mandate["id"], "cancelled")

            log_transaction(
                mandate_id=mandate["id"],
                user_id=mandate.get("user_id"),
                event_type="voided",
                paypal_resource_id=authorization_id,
                paypal_authorization_id=authorization_id,
                status="voided",
            )

            return {"status": "VOIDED", "authorization_id": authorization_id}

        except Exception as exc:
            logger.error("Void failed: %s", exc)
            return {"error": f"Void failed: {exc}"}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

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
            parts.append(f"Deadline: {constraints['deadline']}")
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
    category_lower = normalize_category(category)
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
