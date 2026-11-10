"""
Agent Orchestrator — drives the AI agent loop that processes mandates.

Flow:
  1. Pricing/category agent defines and persists constraints before search
  2. Cloudflare AI called with tools in a loop (max 10 turns)
  3. Each tool_call is dispatched, results fed back
  4. Mandate status updated at every stage
  5. All actions logged to the transactions table
"""

from __future__ import annotations

import json
import logging
import math
import re
from typing import Any, NoReturn, Optional

from backend.services.cloudflare_ai import CloudflareAI
from backend.services.errors import error_message
from backend.services.categories import category_allowed, normalize_category
from backend.services.mandate_lifecycle import (
    MandateLifecycle, ExecutionStopped, CancellationPending, CancellationFailed, PaymentOutcomeUnknown, field,
)
from backend.services.paypal_service import PayPalOrderBuildError, PayPalService
from backend.services.pricing_category_agent import PricingCategoryAgent
from backend.services.purchase_confirmation import PurchaseConfirmation, purchase_product
from backend.services.supabase_client import AuditLogError, get_supabase, resolve_payment_token, log_transaction, update_mandate_status
from backend.services.time_utils import parse_utc_timestamp, utc_now
from backend.services.tool_calls import parse_tool_response
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
4. Any policy rejection permanently cancels this mandate. Never retry with
   corrected arguments or a different product. The backend stops execution and
   voids any existing authorization.
5. If APPROVED, call `create_authorized_order` to hold funds.
   The backend waits for the user's 10-second purchase confirmation before
   authorization. Continue only with that exact product, currency and price.
6. If auto_execute is true, immediately call `capture_authorized_payment`.
7. If auto_execute is false, stop after authorization and let the user review.
8. Never exceed the max_price constraint.
9. Prefer lower prices when quality is comparable.
10. Always provide clear reasoning for your choices.
11. Use the supplied current UTC timestamp for time-sensitive decisions. Keep the \
mandate's saved deadline and resolved travel/event dates fixed; do not shift them \
by reinterpreting the original relative wording on later turns.
12. Search results are demo catalog entries. Verify that the actual product and
specifications match the request; never substitute an unrelated item just because
it is in the same broad category. If no suitable option exists, stop and explain
which requirement could not be met. A completed search is not a completed purchase.

## Response Format
Use the provided tools to perform actions. Do not describe a tool call in your
final answer. Send price, amount and max_price as JSON numbers, never strings.
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
        self.pricing_agent = PricingCategoryAgent(cloudflare_ai)
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
        stage = "pricing"
        last_tool = None
        last_tool_error = None

        try:
            self._refresh_running(mandate_dict)
            constraints = await self.pricing_agent.define_constraints(
                mandate_dict["description"], mandate_dict.get("constraints"),
            )
            self._refresh_running(mandate_dict)
            updated = update_mandate_status(
                mandate_id, "searching", {"constraints": constraints}, supabase_client=self.sb,
            )
            if not updated:
                self._refresh_running(mandate_dict)
                raise RuntimeError("Unable to persist AI pricing constraints")
            mandate_dict.update(updated)
            log_transaction(
                mandate_id, "CONSTRAINTS_DEFINED", user_id=mandate_dict["user_id"],
                status="completed", currency=constraints["currency"], details=constraints,
                supabase_client=self.sb,
            )
            messages: list[dict[str, Any]] = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": self._build_user_message(mandate_dict)},
            ]
            stage = "shopping"
            for turn in range(MAX_TURNS):
                logger.info("Agent turn %d/%d for mandate %s", turn + 1, MAX_TURNS, mandate_id)

                stage = "shopping_model"
                response = await self.ai.chat_with_tools(messages, AGENT_TOOLS)
                self._refresh_running(mandate_dict)

                response_text, tool_calls = parse_tool_response(response, turn)

                if not tool_calls:
                    # Text alone never counts as a successful purchase.
                    result["agent_reasoning"] = response_text or result["agent_reasoning"]
                    if result["status"] == "failed":
                        reason = response_text or last_tool_error or (
                            f"Shopping agent stopped after {last_tool or 'planning'} without authorizing a purchase"
                        )
                        result["error"] = f"No purchase was authorized. {reason}"
                        result["agent_reasoning"] = result["error"]
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
                    fn_args = json.loads(tc["function"]["arguments"])
                    tc_id = tc["id"]

                    logger.info("Tool call: %s(%s)", fn_name, fn_args)

                    last_tool = fn_name
                    stage = fn_name
                    tool_result = await self._execute_tool(fn_name, fn_args, mandate_dict, result)
                    if isinstance(tool_result, dict) and (tool_result.get("error") or tool_result.get("decision") == "REJECTED"):
                        last_tool_error = tool_result.get("reason") or tool_result.get("error")

                    # Feed tool result back to the model
                    messages.append({
                        "role": "tool",
                        "name": fn_name,
                        "tool_call_id": tc_id,
                        "content": json.dumps(tool_result) if not isinstance(tool_result, str) else tool_result,
                    })
            else:
                if result["status"] == "failed":
                    result["error"] = f"Shopping agent reached the {MAX_TURNS}-turn limit without authorizing a purchase"
                    result["agent_reasoning"] = result["error"]

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
            elif result["status"] == "failed":
                log_transaction(
                    mandate_id, "MANDATE_FAILED", user_id=mandate_dict["user_id"],
                    status="failed", details={"reason": result["error"], "stage": stage,
                                              "last_tool": last_tool, "last_tool_error": last_tool_error},
                    supabase_client=self.sb,
                )
            logger.info("Agent finished mandate %s — status=%s", mandate_id, result["status"])

        except ExecutionStopped as exc:
            result["status"] = exc.mandate["status"]
            result["agent_reasoning"] = exc.mandate.get("agent_reasoning") or str(exc)
        except (CancellationPending, CancellationFailed, PaymentOutcomeUnknown) as exc:
            result["status"] = self.lifecycle.current(mandate_id)["status"]
            result["error"] = error_message(exc)
        except Exception as exc:
            logger.exception("Agent execution failed for mandate %s", mandate_id)
            reason = error_message(exc)
            result["error"] = reason
            result["agent_reasoning"] = reason
            current = self.lifecycle.current(mandate_id)
            if current.get("payment_operation_id") or current.get("cancel_requested") or current["status"] in ("authorized", "completed", "cancelled", "failed"):
                result["status"] = current["status"]
            else:
                updated = update_mandate_status(mandate_id, "failed", {"agent_reasoning": reason}, supabase_client=self.sb)
                result["status"] = self.lifecycle.current(mandate_id)["status"]
                if updated:
                    log_transaction(
                        mandate_id, "MANDATE_FAILED", user_id=mandate_dict["user_id"],
                        status="failed", details={"reason": reason, "stage": stage, "error_type": type(exc).__name__,
                                                  "cause_type": type(exc.__cause__).__name__ if exc.__cause__ else None,
                                                  "last_tool": last_tool, "last_tool_error": last_tool_error},
                        supabase_client=self.sb,
                    )

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
            return {"error": error_message(exc)}

    # ------------------------------------------------------------------
    # Tool implementations
    # ------------------------------------------------------------------

    async def _search_products(
        self, args: dict[str, Any], mandate: dict[str, Any], result: dict[str, Any],
    ) -> dict[str, Any]:
        """Mock product search — returns realistic fake data for the demo."""
        update_mandate_status(mandate["id"], "searching", supabase_client=self.sb)

        constraints = mandate.get("constraints") or {}
        query = args.get("query", "")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("A product search query is required")
        category = normalize_category(args.get("category") or constraints.get("category"))
        max_price = args.get("max_price") if args.get("max_price") is not None else constraints.get("max_price")
        ceiling = constraints.get("max_price")
        for amount in (max_price, ceiling):
            if isinstance(amount, bool) or not isinstance(amount, (int, float)) or not math.isfinite(amount) or amount <= 0:
                raise ValueError("Product search requires a finite positive budget")
        max_price = min(max_price, ceiling)
        currency = args.get("currency") or constraints.get("currency", "USD")
        if not isinstance(currency, str) or currency.upper() != constraints.get("currency", "USD").upper():
            raise ValueError("Search currency must match the mandate")
        currency = currency.upper()

        # Generate mock products based on query keywords
        products = _generate_mock_products(query, category, max_price, currency)

        log_transaction(
            mandate_id=mandate["id"],
            event_type="PRODUCT_SEARCH",
            status="completed",
            details={"query": query, "category": category, "max_price": max_price,
                     "currency": currency, "source": "demo_catalog", "results_count": len(products),
                     "products": products},
            user_id=mandate["user_id"], supabase_client=self.sb,
        )

        return {
            "products": products, "total_results": len(products), "source": "demo_catalog",
            "message": "Demo catalog results; verify product specifications before purchase." if products else
                       "No matching demo products within the budget. Do not substitute an unrelated product; explain the unmet requirement.",
        }

    async def _check_policy_wrapper(
        self, args: dict[str, Any], mandate: dict[str, Any], result: dict[str, Any],
    ) -> dict[str, Any]:
        """Return approval, or durably cancel and stop on the first rejection."""
        self._refresh_running(mandate)
        update_mandate_status(mandate["id"], "evaluating", supabase_client=self.sb)
        policy_result = self._check_policy(args, mandate)
        if policy_result["decision"] == "REJECTED":
            self._reject_policy(args, mandate, result, policy_result)

        self._log_policy(args, mandate, policy_result)
        await self._confirm_purchase(args, mandate)
        return policy_result

    async def _confirm_purchase(self, args: dict[str, Any], mandate: dict[str, Any]) -> str:
        confirmation = PurchaseConfirmation(self.lifecycle)
        pending = confirmation.begin(mandate["id"], purchase_product(args))
        approved = await confirmation.wait(mandate["id"], pending["purchase_confirmation_id"])
        mandate.update(approved)
        self._refresh_running(mandate)
        return approved["purchase_confirmation_id"]

    def _log_policy(self, args: dict[str, Any], mandate: dict[str, Any], policy_result: dict[str, Any]) -> None:
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

    def _reject_policy(
        self, args: dict[str, Any], mandate: dict[str, Any], result: dict[str, Any],
        policy_result: dict[str, Any], *, claim: Optional[dict[str, Any]] = None,
    ) -> NoReturn:
        """A rejected purchase ends the mandate, including batched/later retries."""
        reason = f"Policy rejected: {policy_result['reason']}"
        lifecycle = self.lifecycle
        # Stop other workers before any audit write or claim release. In-flight
        # payments continue through the existing cancellation/reconciliation path.
        lifecycle.request_cancel(mandate["id"], mandate["user_id"], reason=reason)
        if claim:
            lifecycle.finish(claim)
        try:
            self._log_policy(args, mandate, policy_result)
        finally:
            # Even an audit outage must not leave a rejected hold capturable.
            cancelled = lifecycle.cancel(mandate["id"], mandate["user_id"], reason=reason)
        mandate.update(cancelled)
        result.update(status=cancelled["status"], agent_reasoning=cancelled.get("agent_reasoning"))
        raise ExecutionStopped(cancelled)

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
            self._reject_policy({**args, "price": amount}, mandate, result, policy)
        proposal = {**args, "price": amount}
        if mandate.get("purchase_confirmation_product") != purchase_product(proposal):
            self._log_policy(proposal, mandate, policy)
        confirmation_id = await self._confirm_purchase(proposal, mandate)
        token = resolve_payment_token(self.lifecycle.sb, mandate["payment_token_id"], mandate["user_id"])
        claim = self.lifecycle.claim(mandate["id"], "authorize", confirmation_id=confirmation_id)
        # Constraints may have changed since the AI request was sent.
        policy = self._check_policy({**args, "price": amount}, claim)
        if policy["decision"] != "APPROVED":
            self._reject_policy({**args, "price": amount}, mandate, result, policy, claim=claim)
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
            self._reject_policy(
                {"price": result.get("amount"), "currency": result.get("currency"),
                 "product_name": result.get("product_name")},
                mandate, result, {"decision": "REJECTED", "reason": deadline_error}, claim=claim,
            )
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
        if constraints.get("min_price") is not None:
            parts.append(
                f"AI estimated price range: {constraints['min_price']:.2f} to {constraints['max_price']:.2f} "
                f"{constraints.get('currency', 'USD')}. Lower-priced suitable matches are allowed."
            )
        if constraints.get("pricing_reasoning"):
            parts.append(f"Pricing assumptions: {constraints['pricing_reasoning']}")
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

    templates: dict[str, list[dict[str, Any]]] = {
        "headphones": [
            {"name": "Sony WH-1000XM5 Wireless Headphones", "base_price": 348.00, "rating": 4.8},
            {"name": "Apple AirPods Pro (2nd Gen)", "base_price": 249.00, "rating": 4.7},
            {"name": "Samsung Galaxy Buds2 Pro", "base_price": 189.99, "rating": 4.5},
            {"name": "Bose QuietComfort 45", "base_price": 279.00, "rating": 4.6},
            {"name": "JBL Tune 770NC", "base_price": 99.95, "rating": 4.3},
        ],
        "power_banks": [
            {"name": "Demo USB-C Portable Power Bank 20000mAh", "base_price": 39.99, "rating": 4.6, "capacity_mah": 20000},
            {"name": "Demo Fast-Charge Portable Power Bank 26800mAh", "base_price": 49.99, "rating": 4.5, "capacity_mah": 26800},
            {"name": "Demo High-Capacity Portable Power Bank 30000mAh", "base_price": 59.99, "rating": 4.4, "capacity_mah": 30000},
        ],
        "keyboards": [
            {"name": "Demo Ergonomic Mechanical Keyboard with Quiet Switches", "base_price": 79.99, "rating": 4.5},
            {"name": "Demo Wireless Mechanical Keyboard with Silent Switches", "base_price": 99.99, "rating": 4.6},
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
            {"name": "Kindle Paperwhite (16GB)", "base_price": 139.99, "rating": 4.7, "category": "electronics"},
            {"name": "Hydro Flask 32oz Water Bottle", "base_price": 44.95, "rating": 4.8},
            {"name": "Moleskine Classic Notebook", "base_price": 19.50, "rating": 4.5},
        ],
    }

    # Match the product family before applying broad category/budget filters.
    # Electronics must not silently turn every query into a headphones search.
    category_lower = normalize_category(category) if category else "general"
    query_lower = query.lower().replace("-", " ")
    aliases = {
        "power_banks": ("power bank", "powerbank", "portable charger", "external battery", "powercore"),
        "keyboards": ("keyboard",),
        "headphones": ("headphone", "earbud", "earphone", "airpods", "headset"),
        "flights": ("flight", "airline", "round trip", "airfare"),
        "food": ("food", "meal", "snack", "coffee", "chocolate", "tea", "groceries"),
    }
    selected = [key for key, words in aliases.items() if any(
        re.search(r"\b" + re.escape(word) + r"s?\b", query_lower) for word in words
    )]
    categories = {"headphones": "electronics", "power_banks": "electronics", "keyboards": "electronics"}
    if not selected:
        if normalize_category(query_lower.strip()) == category_lower:
            selected = [key for key in templates if category_lower == "general" or
                        category_allowed(category_lower, categories.get(key, key))]
        else:
            selected = ["general"]

    capacity = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*(k)?\s*mah", query_lower)
    minimum_capacity = float(capacity[1].replace(",", "")) * (1000 if capacity[2] else 1) if capacity else 0

    products = []
    candidates = [(key, item) for key in selected for item in templates[key]]
    for template_key, item in candidates:
        actual_category = item.get("category", categories.get(template_key, template_key))
        if category_lower != "general" and not category_allowed(category_lower, actual_category):
            continue
        if template_key == "general" and normalize_category(query_lower.strip()) != category_lower:
            if not any(word in query_lower for word in ("backpack", "kindle", "paperwhite", "water bottle", "hydro flask", "notebook", "moleskine")
                       if word in item["name"].lower()):
                continue
        if template_key == "power_banks" and item["capacity_mah"] < minimum_capacity:
            continue
        # A demo item's price must not change just because the query is reworded.
        price = item["base_price"]

        if price <= max_price:
            products.append({
                "name": item["name"],
                "price": price,
                "currency": currency,
                "category": actual_category,
                "rating": item["rating"],
                "in_stock": True,
                "estimated_delivery": "3-5 business days",
                **({"capacity_mah": item["capacity_mah"]} if "capacity_mah" in item else {}),
            })

    # Sort by price
    products.sort(key=lambda p: p["price"])
    return products[:5]
