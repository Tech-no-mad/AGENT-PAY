"use client";

import React, { useEffect, useRef } from "react";
import {
  Search,
  CheckCircle,
  XCircle,
  AlertCircle,
  CreditCard,
  Activity,
  ShieldCheck,
  DollarSign,
} from "lucide-react";
import type { Transaction } from "@/lib/api";

interface AgentActivityLogProps {
  transactions: Transaction[];
}

const eventIcons: Record<string, React.ReactNode> = {
  search: <Search className="w-4 h-4 text-yellow-400" />,
  evaluate: <Activity className="w-4 h-4 text-orange-400" />,
  authorize: <ShieldCheck className="w-4 h-4 text-purple-400" />,
  capture: <CheckCircle className="w-4 h-4 text-green-400" />,
  payment: <CreditCard className="w-4 h-4 text-primary-400" />,
  order: <CreditCard className="w-4 h-4 text-purple-400" />,
  policy: <ShieldCheck className="w-4 h-4 text-emerald-400" />,
  void: <XCircle className="w-4 h-4 text-gray-400" />,
  complete: <CheckCircle className="w-4 h-4 text-green-400" />,
  cancel: <XCircle className="w-4 h-4 text-gray-400" />,
  error: <AlertCircle className="w-4 h-4 text-red-400" />,
  fail: <AlertCircle className="w-4 h-4 text-red-400" />,
  refund: <DollarSign className="w-4 h-4 text-amber-400" />,
};

const eventColors: Record<string, string> = {
  search: "border-yellow-500/30",
  evaluate: "border-orange-500/30",
  authorize: "border-purple-500/30",
  capture: "border-green-500/30",
  payment: "border-primary-500/30",
  order: "border-purple-500/30",
  policy: "border-emerald-500/30",
  void: "border-gray-500/30",
  complete: "border-green-500/30",
  cancel: "border-gray-500/30",
  error: "border-red-500/30",
  fail: "border-red-500/30",
  refund: "border-amber-500/30",
};

function getEventKey(eventType: string): string {
  const lower = eventType.toLowerCase();
  const canonical: Record<string, string> = {
    policy_approved: "evaluate", policy_rejected: "error", captured: "complete",
    voided: "cancel", failed: "error", order_created: "payment",
  };
  if (canonical[lower]) return canonical[lower];
  for (const key of Object.keys(eventIcons)) {
    if (lower.includes(key)) return key;
  }
  return "search";
}

export default function AgentActivityLog({
  transactions,
}: AgentActivityLogProps) {
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [transactions]);

  const formatTime = (dateStr: string) => {
    return new Date(dateStr).toLocaleString("en-US", {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  };

  if (transactions.length === 0) {
    return (
      <div className="text-center py-12 text-gray-500">
        <Activity className="w-10 h-10 mx-auto mb-3 opacity-50" />
        <p>No activity yet</p>
        <p className="text-sm mt-1">Agent actions will appear here in real-time</p>
      </div>
    );
  }

  return (
    <div ref={scrollRef} className="max-h-[500px] overflow-y-auto space-y-0 pr-2">
      {transactions.map((tx, index) => {
        const eventKey = getEventKey(tx.event_type);
        const icon = eventIcons[eventKey];
        const borderColor = eventColors[eventKey];
        const isLast = index === transactions.length - 1;

        return (
          <div key={tx.id} className="relative flex gap-4 pb-6">
            {/* Timeline line */}
            {!isLast && (
              <div className="absolute left-[15px] top-[32px] bottom-0 w-px bg-gray-800" />
            )}

            {/* Icon */}
            <div
              className={`relative z-10 flex-shrink-0 w-8 h-8 rounded-full border-2 ${borderColor} bg-gray-900 flex items-center justify-center`}
            >
              {icon}
            </div>

            {/* Content */}
            <div className="flex-1 min-w-0">
              <div className="flex items-center justify-between gap-2 mb-1">
                <span className="text-sm font-medium text-gray-200 capitalize">
                  {tx.event_type.replace(/_/g, " ")}
                </span>
                <span className="text-xs text-gray-500 flex-shrink-0">
                  {formatTime(tx.created_at)}
                </span>
              </div>
              <p className="text-sm text-gray-400">
                {typeof tx.details === "string"
                  ? tx.details
                  : tx.details
                  ? JSON.stringify(tx.details)
                  : ""}
              </p>
              {tx.amount && (
                <p className="text-sm text-primary-400 mt-1 font-medium">
                  ${tx.amount.toFixed(2)}
                </p>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
