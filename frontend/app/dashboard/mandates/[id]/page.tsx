"use client";

import React, { useEffect, useState, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import {
  ArrowLeft,
  RefreshCw,
  XCircle,
  ShieldCheck,
  CheckCircle,
  CreditCard,
  Sparkles,
  ExternalLink,
  Clock,
  AlertTriangle,
} from "lucide-react";
import StatusBadge from "@/components/StatusBadge";
import AgentActivityLog from "@/components/AgentActivityLog";
import {
  cancelMandate,
  getMandate,
  getMandateTransactions,
  Mandate,
  Transaction,
} from "@/lib/api";

export default function MandateDetailPage() {
  const params = useParams();
  const router = useRouter();
  const mandateId = params.id as string;

  const [mandate, setMandate] = useState<Mandate | null>(null);
  const [transactions, setTransactions] = useState<Transaction[]>([]);
  const [loading, setLoading] = useState(true);
  const [cancelling, setCancelling] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    if (!mandateId) return;
    try {
      const [m, txs] = await Promise.all([
        getMandate(mandateId),
        getMandateTransactions(mandateId).catch(() => []),
      ]);
      setMandate(m);
      setTransactions(txs);
    } catch (err: any) {
      setError(err.message || "Failed to load mandate details");
    } finally {
      setLoading(false);
    }
  }, [mandateId]);

  useEffect(() => {
    fetchData();

    // Auto-polling while the agent is running
    const interval = setInterval(() => {
      if (
        mandate &&
        ["active", "searching", "evaluating", "authorized"].includes(mandate.status)
      ) {
        fetchData();
      }
    }, 3000);

    return () => clearInterval(interval);
  }, [fetchData, mandate?.status]);

  const handleCancel = async () => {
    if (!confirm("Are you sure you want to cancel this mandate? Any PayPal holds will be voided.")) {
      return;
    }
    setCancelling(true);
    try {
      await cancelMandate(mandateId);
      await fetchData();
    } catch (err: any) {
      alert(err.message || "Failed to cancel mandate");
    } finally {
      setCancelling(false);
    }
  };

  if (loading) {
    return (
      <div className="p-16 text-center text-gray-500 max-w-5xl mx-auto">
        <RefreshCw className="w-8 h-8 animate-spin mx-auto mb-3 text-primary-400" />
        <span>Loading mandate execution details...</span>
      </div>
    );
  }

  if (error || !mandate) {
    return (
      <div className="max-w-3xl mx-auto space-y-4">
        <Link
          href="/dashboard/mandates"
          className="inline-flex items-center gap-2 text-xs text-gray-400 hover:text-gray-200"
        >
          <ArrowLeft className="w-4 h-4" />
          <span>Back to Mandates</span>
        </Link>
        <div className="p-6 rounded-xl border border-red-500/20 bg-red-500/10 text-red-400 text-sm">
          {error || "Mandate not found"}
        </div>
      </div>
    );
  }

  const isOngoing = ["active", "searching", "evaluating", "authorized"].includes(
    mandate.status
  );

  return (
    <div className="max-w-6xl mx-auto space-y-8">
      {/* Top Breadcrumb & Action bar */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <Link
            href="/dashboard/mandates"
            className="p-2 rounded-lg border border-gray-800 bg-gray-900 text-gray-400 hover:text-gray-200 transition-colors"
          >
            <ArrowLeft className="w-4 h-4" />
          </Link>
          <div>
            <div className="flex items-center gap-2.5">
              <h1 className="text-xl sm:text-2xl font-bold text-gray-100">
                Mandate #{mandate.id.slice(0, 8)}
              </h1>
              <StatusBadge status={mandate.status} />
            </div>
            <p className="text-xs text-gray-500 mt-0.5">
              Created {new Date(mandate.created_at).toLocaleString()}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2.5">
          <button
            onClick={() => fetchData()}
            className="p-2.5 rounded-lg border border-gray-800 bg-gray-900 text-gray-400 hover:text-gray-200 transition-colors"
            title="Refresh"
          >
            <RefreshCw className="w-4 h-4" />
          </button>

          {isOngoing && (
            <button
              onClick={handleCancel}
              disabled={cancelling}
              className="flex items-center gap-2 px-4 py-2 rounded-lg border border-red-500/30 bg-red-500/10 text-red-400 hover:bg-red-500/20 text-xs font-medium transition-colors"
            >
              <XCircle className="w-4 h-4" />
              <span>{cancelling ? "Voiding..." : "Cancel & Void Hold"}</span>
            </button>
          )}
        </div>
      </div>

      {/* Main Instruction Banner */}
      <div className="p-6 rounded-xl border border-gray-800 bg-gray-900/40 space-y-2">
        <span className="text-xs font-medium text-gray-400 uppercase tracking-wider">
          Natural Language Mandate
        </span>
        <p className="text-base sm:text-lg text-gray-100 font-medium leading-relaxed">
          "{mandate.description}"
        </p>
      </div>

      {/* Key Metric Highlights */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {/* Payment Amount */}
        <div className="p-5 rounded-xl border border-gray-800 bg-gray-900/60 space-y-1">
          <div className="flex items-center justify-between text-xs text-gray-400 font-medium">
            <span>Payment Amount</span>
            <CreditCard className="w-4 h-4 text-primary-400" />
          </div>
          <div className="text-2xl font-bold text-gray-100">
            {mandate.executed_amount
              ? `$${mandate.executed_amount.toFixed(2)}`
              : mandate.status === "completed"
              ? "Completed"
              : "Pending Capture"}
          </div>
          <div className="text-[11px] text-gray-500">
            {mandate.paypal_order_id ? (
              <span>PayPal Order: {mandate.paypal_order_id}</span>
            ) : (
              <span>Order will generate upon match</span>
            )}
          </div>
        </div>

        {/* Policy Guard */}
        <div className="p-5 rounded-xl border border-gray-800 bg-gray-900/60 space-y-1">
          <div className="flex items-center justify-between text-xs text-gray-400 font-medium">
            <span>Policy Hard Ceiling</span>
            <ShieldCheck className="w-4 h-4 text-emerald-400" />
          </div>
          <div className="text-2xl font-bold text-gray-100">
            {mandate.constraints && typeof mandate.constraints === "object" && (mandate.constraints as any).max_price
              ? `$${(mandate.constraints as any).max_price.toFixed(2)}`
              : "No Ceiling"}
          </div>
          <div className="text-[11px] text-gray-500">
            Autonomous agent will reject any deal above this limit
          </div>
        </div>

        {/* Live Rail State */}
        <div className="p-5 rounded-xl border border-gray-800 bg-gray-900/60 space-y-1">
          <div className="flex items-center justify-between text-xs text-gray-400 font-medium">
            <span>PayPal Rail State</span>
            <Clock className="w-4 h-4 text-sky-400" />
          </div>
          <div className="text-lg font-bold text-gray-100 capitalize">
            {mandate.status}
          </div>
          <div className="text-[11px] text-gray-500">
            {mandate.paypal_authorization_id
              ? `Auth Hold: ${mandate.paypal_authorization_id.slice(0, 14)}...`
              : "Vault token active"}
          </div>
        </div>
      </div>

      {/* AI Agent Reasoning & Policy Breakdown */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Left: Agent Reasoning */}
        <div className="p-6 rounded-xl border border-gray-800 bg-gray-900/50 space-y-4">
          <h3 className="text-sm font-semibold text-gray-200 flex items-center gap-2">
            <Sparkles className="w-4 h-4 text-primary-400" />
            AI Decision Reasoning
          </h3>

          {mandate.agent_reasoning ? (
            <div className="p-4 rounded-lg bg-gray-950 border border-gray-800 text-sm text-gray-300 leading-relaxed whitespace-pre-line">
              {mandate.agent_reasoning}
            </div>
          ) : isOngoing ? (
            <div className="p-4 rounded-lg bg-gray-950 border border-gray-800 text-xs text-gray-400 flex items-center gap-2.5">
              <RefreshCw className="w-4 h-4 animate-spin text-primary-400" />
              <span>Cloudflare AI is currently evaluating merchant options...</span>
            </div>
          ) : (
            <div className="text-xs text-gray-500">No reasoning recorded.</div>
          )}
        </div>

        {/* Right: Parsed Policy Constraints */}
        <div className="p-6 rounded-xl border border-gray-800 bg-gray-900/50 space-y-4">
          <h3 className="text-sm font-semibold text-gray-200 flex items-center gap-2">
            <ShieldCheck className="w-4 h-4 text-emerald-400" />
            Parsed Policy Constraints
          </h3>

          <div className="p-4 rounded-lg bg-gray-950 border border-gray-800 font-mono text-xs text-gray-300 overflow-x-auto">
            <pre>
              {mandate.constraints
                ? JSON.stringify(mandate.constraints, null, 2)
                : "{}"}
            </pre>
          </div>
        </div>
      </div>

      {/* Real-time Agent Activity Audit Log */}
      <div className="space-y-4">
        <h3 className="text-base font-semibold text-gray-100 flex items-center gap-2">
          <span>Real-time Execution & Audit Trail</span>
          {isOngoing && (
            <span className="w-2 h-2 rounded-full bg-primary-400 animate-ping" />
          )}
        </h3>

        <AgentActivityLog transactions={transactions} />
      </div>
    </div>
  );
}
