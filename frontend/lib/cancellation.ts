import type { Mandate, Transaction } from "./api";

function reasonText(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

export function getCancellationReason(
  mandate: Mandate,
  transactions: Transaction[] = []
): string | null {
  // Cancellation events carry the original reason, even if a later void retry
  // replaced agent_reasoning with a payment-release status message.
  const cancelled = transactions.find((tx) =>
    (tx.details?.original_event === "MANDATE_CANCELLED" || tx.event_type.toLowerCase() === "mandate_cancelled") &&
    reasonText(tx.details?.reason)
  );
  if (cancelled) return reasonText(cancelled.details?.reason);

  // The API returns newest events first. Older records may contain a rejection
  // followed by an approval; that old rejection is not the cancellation reason.
  const policyDecision = transactions.find((tx) =>
    ["policy_rejected", "policy_approved"].includes(tx.event_type.toLowerCase()) ||
    tx.details?.original_event === "POLICY_CHECK"
  );
  if (policyDecision && reasonText(policyDecision.details?.reason) &&
      (policyDecision.event_type.toLowerCase() === "policy_rejected" || policyDecision.details?.status === "REJECTED")) {
    return `Policy rejected: ${reasonText(policyDecision.details?.reason)}`;
  }

  const saved = reasonText(mandate.agent_reasoning);
  if (!saved) return null;
  // A release confirmation describes the outcome, not why cancellation happened.
  const reason = saved
    .replace(/\.?\s*Cancellation confirmed; any authorization has been voided\s*$/i, "")
    .trim();
  if (!reason || reason.startsWith("Cancellation pending:")) return null;
  return reason;
}
