import { AlertTriangle, XCircle } from "lucide-react";
import type { Mandate, Transaction } from "@/lib/api";
import { getCancellationReason } from "@/lib/cancellation";

interface CancellationNoticeProps {
  mandate: Mandate;
  transactions?: Transaction[];
  compact?: boolean;
}

export default function CancellationNotice({ mandate, transactions, compact = false }: CancellationNoticeProps) {
  const cancelled = mandate.status === "cancelled";
  const pending = mandate.cancel_requested && !cancelled && mandate.status !== "completed";
  if (!cancelled && !pending) return null;

  const reason = getCancellationReason(mandate, transactions);
  const Icon = pending ? AlertTriangle : XCircle;

  return (
    <div
      role="status"
      className={`rounded-xl border border-amber-500/25 bg-amber-500/10 ${compact ? "p-3 mb-4" : "p-5"}`}
    >
      <div className="flex items-start gap-3">
        <Icon className="w-5 h-5 flex-shrink-0 text-amber-400 mt-0.5" aria-hidden="true" />
        <div className="min-w-0 space-y-1">
          <p className="text-sm font-semibold text-amber-200">
            {pending ? "Cancellation pending" : "Reason for cancellation"}
          </p>
          <p
            className={`text-sm text-amber-100/90 leading-relaxed break-words ${compact ? "line-clamp-2" : "whitespace-pre-line"}`}
            title={compact && reason ? reason : undefined}
          >
            {reason || (compact
              ? "Open the mandate to view cancellation details."
              : "No cancellation reason was recorded for this mandate.")}
          </p>
          {pending && !compact && (
            <p className="text-xs text-amber-200/80 pt-1">
              Further payment attempts are blocked. Cancellation will be confirmed after any payment in progress is resolved and the hold is released.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
