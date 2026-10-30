"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import {
  History,
  RefreshCw,
  Search,
  ExternalLink,
  ShieldCheck,
  CreditCard,
  CheckCircle,
  XCircle,
  AlertCircle,
  Clock,
  ArrowRight,
} from "lucide-react";
import { getTransactions, Transaction } from "@/lib/api";

const eventBadgeConfig: Record<
  string,
  { label: string; bg: string; text: string; icon: any }
> = {
  // Uppercase legacy / UI keys
  MANDATE_CREATED: {
    label: "Created",
    bg: "bg-blue-500/10 border-blue-500/20",
    text: "text-blue-400",
    icon: Clock,
  },
  SEARCH_STARTED: {
    label: "Catalog Search",
    bg: "bg-yellow-500/10 border-yellow-500/20",
    text: "text-yellow-400",
    icon: Search,
  },
  POLICY_APPROVED: {
    label: "Policy Pass",
    bg: "bg-emerald-500/10 border-emerald-500/20",
    text: "text-emerald-400",
    icon: ShieldCheck,
  },
  POLICY_REJECTED: {
    label: "Policy Reject",
    bg: "bg-red-500/10 border-red-500/20",
    text: "text-red-400",
    icon: XCircle,
  },
  ORDER_CREATED: {
    label: "Order Created",
    bg: "bg-purple-500/10 border-purple-500/20",
    text: "text-purple-400",
    icon: CreditCard,
  },
  AUTHORIZED: {
    label: "Hold Authorized",
    bg: "bg-purple-500/10 border-purple-500/20",
    text: "text-purple-400",
    icon: ShieldCheck,
  },
  AUTHORIZATION_VOIDED: {
    label: "Hold Voided",
    bg: "bg-gray-500/10 border-gray-500/20",
    text: "text-gray-400",
    icon: XCircle,
  },
  PAYMENT_CAPTURED: {
    label: "Captured",
    bg: "bg-green-500/10 border-green-500/20",
    text: "text-green-400",
    icon: CheckCircle,
  },
  MANDATE_COMPLETED: {
    label: "Completed",
    bg: "bg-green-500/10 border-green-500/20",
    text: "text-green-400",
    icon: CheckCircle,
  },
  MANDATE_CANCELLED: {
    label: "Cancelled",
    bg: "bg-gray-500/10 border-gray-500/20",
    text: "text-gray-400",
    icon: XCircle,
  },
  FAILED: {
    label: "Failed",
    bg: "bg-red-500/10 border-red-500/20",
    text: "text-red-400",
    icon: AlertCircle,
  },

  // Lowercase schema check constraint events (BUG-9)
  mandate_created: {
    label: "Created",
    bg: "bg-blue-500/10 border-blue-500/20",
    text: "text-blue-400",
    icon: Clock,
  },
  search_started: {
    label: "Catalog Search",
    bg: "bg-yellow-500/10 border-yellow-500/20",
    text: "text-yellow-400",
    icon: Search,
  },
  deal_found: {
    label: "Deal Found",
    bg: "bg-cyan-500/10 border-cyan-500/20",
    text: "text-cyan-400",
    icon: CheckCircle,
  },
  policy_approved: {
    label: "Policy Pass",
    bg: "bg-emerald-500/10 border-emerald-500/20",
    text: "text-emerald-400",
    icon: ShieldCheck,
  },
  policy_rejected: {
    label: "Policy Reject",
    bg: "bg-red-500/10 border-red-500/20",
    text: "text-red-400",
    icon: XCircle,
  },
  order_created: {
    label: "Order Created",
    bg: "bg-purple-500/10 border-purple-500/20",
    text: "text-purple-400",
    icon: CreditCard,
  },
  authorized: {
    label: "Authorized",
    bg: "bg-purple-500/10 border-purple-500/20",
    text: "text-purple-400",
    icon: ShieldCheck,
  },
  captured: {
    label: "Captured",
    bg: "bg-green-500/10 border-green-500/20",
    text: "text-green-400",
    icon: CheckCircle,
  },
  voided: {
    label: "Voided",
    bg: "bg-gray-500/10 border-gray-500/20",
    text: "text-gray-400",
    icon: XCircle,
  },
  failed: {
    label: "Failed",
    bg: "bg-red-500/10 border-red-500/20",
    text: "text-red-400",
    icon: AlertCircle,
  },
  refunded: {
    label: "Refunded",
    bg: "bg-amber-500/10 border-amber-500/20",
    text: "text-amber-400",
    icon: RefreshCw,
  },
};

export default function HistoryPage() {
  const [transactions, setTransactions] = useState<Transaction[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");

  const fetchTxs = async () => {
    setLoading(true);
    try {
      const data = await getTransactions();
      setTransactions(data);
    } catch (err: any) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchTxs();
  }, []);

  const filtered = transactions.filter((tx) => {
    const q = search.toLowerCase();
    return (
      tx.event_type.toLowerCase().includes(q) ||
      (tx.paypal_resource_id && tx.paypal_resource_id.toLowerCase().includes(q)) ||
      (tx.mandate_id && tx.mandate_id.toLowerCase().includes(q)) ||
      (tx.details && JSON.stringify(tx.details).toLowerCase().includes(q))
    );
  });

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-100 flex items-center gap-2">
            <History className="w-6 h-6 text-primary-400" />
            Audit History & Transaction Logs
          </h1>
          <p className="text-gray-400 text-sm mt-1">
            Complete cryptographic audit trail of all AI agent decisions, policy evaluations, and
            PayPal payment lifecycle events.
          </p>
        </div>

        <button
          onClick={fetchTxs}
          disabled={loading}
          className="p-2.5 rounded-lg border border-gray-800 bg-gray-900 text-gray-400 hover:text-gray-200 transition-colors self-start sm:self-auto"
          title="Refresh"
        >
          <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
        </button>
      </div>

      {/* Search Input */}
      <div className="relative">
        <Search className="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
        <input
          type="text"
          placeholder="Filter by event type, order ID, or details..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="w-full bg-gray-900 border border-gray-800 rounded-lg pl-9 pr-3 py-2 text-xs text-gray-200 placeholder-gray-500 focus:outline-none focus:border-primary-500"
        />
      </div>

      {/* Table */}
      {loading ? (
        <div className="p-16 text-center text-gray-500 rounded-xl border border-gray-800 bg-gray-900/30">
          <RefreshCw className="w-6 h-6 animate-spin mx-auto mb-2 text-primary-400" />
          <span>Loading audit history...</span>
        </div>
      ) : filtered.length === 0 ? (
        <div className="p-16 text-center rounded-xl border border-dashed border-gray-800 bg-gray-900/20 space-y-2">
          <History className="w-8 h-8 text-gray-500 mx-auto" />
          <p className="text-sm text-gray-400">No events or transactions recorded yet.</p>
        </div>
      ) : (
        <div className="border border-gray-800 rounded-xl overflow-hidden bg-gray-900/40">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="bg-gray-900/80 text-gray-400 uppercase tracking-wider border-b border-gray-800">
                <tr>
                  <th className="py-3 px-4">Timestamp</th>
                  <th className="py-3 px-4">Event Type</th>
                  <th className="py-3 px-4">Mandate Reference</th>
                  <th className="py-3 px-4">Amount</th>
                  <th className="py-3 px-4">PayPal Ref</th>
                  <th className="py-3 px-4">Details / Metadata</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-800/60">
                {filtered.map((tx) => {
                  const badge = eventBadgeConfig[tx.details?.original_event || tx.details?.source_event_type] || eventBadgeConfig[tx.event_type.toUpperCase()] || eventBadgeConfig[tx.event_type] || {
                    label: tx.event_type,
                    bg: "bg-gray-800",
                    text: "text-gray-300",
                    icon: Clock,
                  };
                  const Icon = badge.icon;

                  return (
                    <tr key={tx.id} className="hover:bg-gray-800/30 transition-colors">
                      <td className="py-3 px-4 text-gray-400 whitespace-nowrap">
                        {new Date(tx.created_at).toLocaleTimeString([], {
                          hour: "2-digit",
                          minute: "2-digit",
                          second: "2-digit",
                        })}{" "}
                        <span className="text-[10px] text-gray-600 block">
                          {new Date(tx.created_at).toLocaleDateString()}
                        </span>
                      </td>

                      <td className="py-3 px-4">
                        <span
                          className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[11px] font-medium border ${badge.bg} ${badge.text}`}
                        >
                          <Icon className="w-3 h-3" />
                          <span>{badge.label}</span>
                        </span>
                      </td>

                      <td className="py-3 px-4">
                        <Link
                          href={`/dashboard/mandates/${tx.mandate_id}`}
                          className="text-primary-400 hover:text-primary-300 font-mono text-[11px] flex items-center gap-1"
                        >
                          <span>#{tx.mandate_id.slice(0, 8)}</span>
                          <ArrowRight className="w-3 h-3" />
                        </Link>
                      </td>

                      <td className="py-3 px-4 font-medium text-gray-200">
                        {tx.amount ? `$${Number(tx.amount).toFixed(2)}` : "—"}
                      </td>

                      <td className="py-3 px-4 font-mono text-[11px] text-gray-400">
                        {tx.paypal_resource_id ? (
                          <span className="text-sky-400">{tx.paypal_resource_id.slice(0, 10)}...</span>
                        ) : (
                          "—"
                        )}
                      </td>

                      <td className="py-3 px-4 text-gray-400 max-w-xs truncate font-mono text-[11px]">
                        {tx.details ? JSON.stringify(tx.details) : "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
