"use client";

import React from "react";
import { useRouter } from "next/navigation";
import { Clock, DollarSign } from "lucide-react";
import StatusBadge from "./StatusBadge";
import type { Mandate } from "@/lib/api";
import { formatTimestamp } from "@/lib/time";

interface MandateCardProps {
  mandate: Mandate;
}

export default function MandateCard({ mandate }: MandateCardProps) {
  const router = useRouter();
  const maxAmount = typeof mandate.constraints?.max_price === "number"
    ? mandate.constraints.max_price : undefined;

  const truncateDescription = (desc: string, maxLen = 120) => {
    if (desc.length <= maxLen) return desc;
    return desc.substring(0, maxLen).trim() + "…";
  };

  return (
    <div
      onClick={() => router.push(`/dashboard/mandates/${mandate.id}`)}
      className="card-hover cursor-pointer group"
    >
      <div className="flex items-start justify-between gap-4 mb-3">
        <h3 className="text-gray-100 font-medium group-hover:text-primary-400 transition-colors line-clamp-1">
          {truncateDescription(mandate.description, 60)}
        </h3>
        <StatusBadge status={mandate.status} size="sm" />
      </div>

      <p className="text-gray-400 text-sm mb-4 line-clamp-2">
        {truncateDescription(mandate.description)}
      </p>

      <div className="flex items-center justify-between text-sm text-gray-500">
        <div className="flex items-center gap-1.5">
          <Clock className="w-3.5 h-3.5" />
          <time dateTime={mandate.created_at} title={formatTimestamp(mandate.created_at)}>
            {formatTimestamp(mandate.created_at, "date")}
          </time>
        </div>

        {(maxAmount || mandate.executed_amount) && (
          <div className="flex items-center gap-1.5">
            <DollarSign className="w-3.5 h-3.5" />
            <span>
              {mandate.executed_amount
                ? `$${mandate.executed_amount.toFixed(2)}`
                : maxAmount
                ? `Max $${maxAmount.toFixed(2)}`
                : ""}
            </span>
          </div>
        )}
      </div>
    </div>
  );
}
