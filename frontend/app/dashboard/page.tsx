"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import {
  FileText,
  CheckCircle,
  CreditCard,
  ShieldCheck,
  Plus,
  ArrowRight,
  Sparkles,
  RefreshCw,
} from "lucide-react";
import MandateCard from "@/components/MandateCard";
import { getMandates, Mandate } from "@/lib/api";

export default function DashboardPage() {
  const [mandates, setMandates] = useState<Mandate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchMandates = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getMandates();
      setMandates(data);
    } catch (err: any) {
      setError(err.message || "Failed to load mandates");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchMandates();
  }, []);

  // Compute stats
  const activeCount = mandates.filter(
    (m) => ["active", "searching", "evaluating", "authorized"].includes(m.status)
  ).length;

  const completedCount = mandates.filter((m) => m.status === "completed").length;

  const totalSpent = mandates
    .filter((m) => m.status === "completed" && m.executed_amount)
    .reduce((sum, m) => sum + (m.executed_amount || 0), 0);

  return (
    <div className="space-y-8 max-w-7xl mx-auto">
      {/* Top Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl sm:text-3xl font-bold text-gray-100 flex items-center gap-2.5">
            Agent Dashboard
            <span className="inline-flex items-center gap-1 text-xs font-normal px-2.5 py-0.5 rounded-full bg-primary-500/10 text-primary-400 border border-primary-500/20">
              <Sparkles className="w-3 h-3" />
              Llama 3.3 70B + PayPal SDK
            </span>
          </h1>
          <p className="text-gray-400 text-sm mt-1">
            Autonomous spending policies guarded by two-step AUTHORIZE → CAPTURE rails.
          </p>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={fetchMandates}
            disabled={loading}
            className="p-2.5 rounded-lg border border-gray-800 bg-gray-900 text-gray-400 hover:text-gray-200 hover:bg-gray-800 transition-colors"
            title="Refresh"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
          </button>
          <Link
            href="/dashboard/mandates/new"
            className="flex items-center gap-2 px-4 py-2.5 rounded-lg bg-primary-600 hover:bg-primary-500 text-white font-medium text-sm shadow-md shadow-primary-600/20 transition-all"
          >
            <Plus className="w-4 h-4" />
            <span>New Mandate</span>
          </Link>
        </div>
      </div>

      {/* Stats Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="p-5 rounded-xl border border-gray-800 bg-gray-900/60 backdrop-blur-sm">
          <div className="flex items-center justify-between text-gray-400 text-xs font-medium uppercase tracking-wider mb-2">
            <span>Active Mandates</span>
            <FileText className="w-4 h-4 text-primary-400" />
          </div>
          <div className="text-3xl font-bold text-gray-100">{activeCount}</div>
          <div className="text-xs text-primary-400 mt-2 flex items-center gap-1">
            <span>Autonomous agent loops active</span>
          </div>
        </div>

        <div className="p-5 rounded-xl border border-gray-800 bg-gray-900/60 backdrop-blur-sm">
          <div className="flex items-center justify-between text-gray-400 text-xs font-medium uppercase tracking-wider mb-2">
            <span>Completed Purchases</span>
            <CheckCircle className="w-4 h-4 text-green-400" />
          </div>
          <div className="text-3xl font-bold text-gray-100">{completedCount}</div>
          <div className="text-xs text-green-400 mt-2">
            <span>Policy checks passed & captured</span>
          </div>
        </div>

        <div className="p-5 rounded-xl border border-gray-800 bg-gray-900/60 backdrop-blur-sm">
          <div className="flex items-center justify-between text-gray-400 text-xs font-medium uppercase tracking-wider mb-2">
            <span>Total Captured Spend</span>
            <CreditCard className="w-4 h-4 text-sky-400" />
          </div>
          <div className="text-3xl font-bold text-gray-100">
            ${totalSpent.toFixed(2)}
          </div>
          <div className="text-xs text-gray-400 mt-2">
            <span>PayPal Sandbox USD</span>
          </div>
        </div>

        <div className="p-5 rounded-xl border border-gray-800 bg-gray-900/60 backdrop-blur-sm">
          <div className="flex items-center justify-between text-gray-400 text-xs font-medium uppercase tracking-wider mb-2">
            <span>Guard Rail Status</span>
            <ShieldCheck className="w-4 h-4 text-emerald-400" />
          </div>
          <div className="text-lg font-bold text-emerald-400">AUTHORIZE-FIRST</div>
          <div className="text-xs text-gray-400 mt-2">
            <span>Pre-auth hold before capture</span>
          </div>
        </div>
      </div>

      {/* Main Grid: Recent Mandates + Info */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        {/* Left Column: Recent Mandates */}
        <div className="lg:col-span-2 space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold text-gray-100">Recent Mandates</h2>
            <Link
              href="/dashboard/mandates"
              className="text-xs font-medium text-primary-400 hover:text-primary-300 flex items-center gap-1"
            >
              <span>View all</span>
              <ArrowRight className="w-3.5 h-3.5" />
            </Link>
          </div>

          {loading ? (
            <div className="p-12 text-center text-gray-500 rounded-xl border border-gray-800 bg-gray-900/30">
              <RefreshCw className="w-6 h-6 animate-spin mx-auto mb-2 text-primary-400" />
              <span>Fetching mandates...</span>
            </div>
          ) : error ? (
            <div className="p-6 rounded-xl border border-red-500/20 bg-red-500/10 text-red-400 text-sm">
              {error}
            </div>
          ) : mandates.length === 0 ? (
            <div className="p-12 text-center rounded-xl border border-dashed border-gray-800 bg-gray-900/20 space-y-3">
              <div className="w-12 h-12 rounded-full bg-primary-500/10 text-primary-400 flex items-center justify-center mx-auto">
                <FileText className="w-6 h-6" />
              </div>
              <h3 className="text-base font-medium text-gray-200">No mandates created yet</h3>
              <p className="text-sm text-gray-400 max-w-sm mx-auto">
                Define your first spending mandate and let the AI agent autonomously find deals and execute payments.
              </p>
              <Link
                href="/dashboard/mandates/new"
                className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-primary-600 text-white text-sm font-medium hover:bg-primary-500 transition-colors"
              >
                <Plus className="w-4 h-4" />
                <span>Create Your First Mandate</span>
              </Link>
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-4">
              {mandates.slice(0, 5).map((mandate) => (
                <MandateCard key={mandate.id} mandate={mandate} />
              ))}
            </div>
          )}
        </div>

        {/* Right Column: How AgentPay Works */}
        <div className="space-y-6">
          <div className="p-6 rounded-xl border border-gray-800 bg-gradient-to-b from-gray-900/80 to-gray-900/40 space-y-4">
            <h3 className="text-base font-semibold text-gray-100 flex items-center gap-2">
              <ShieldCheck className="w-5 h-5 text-primary-400" />
              The Safe Payment Lifecycle
            </h3>

            <div className="space-y-3 text-xs text-gray-400">
              <div className="flex gap-3">
                <div className="w-6 h-6 rounded-full bg-primary-500/20 text-primary-400 flex items-center justify-center flex-shrink-0 font-bold">
                  1
                </div>
                <div>
                  <strong className="text-gray-200 block">User Sets Mandate</strong>
                  Natural-language budget ceiling and constraints are parsed by Cloudflare AI.
                </div>
              </div>

              <div className="flex gap-3">
                <div className="w-6 h-6 rounded-full bg-yellow-500/20 text-yellow-400 flex items-center justify-center flex-shrink-0 font-bold">
                  2
                </div>
                <div>
                  <strong className="text-gray-200 block">Autonomous Deal Search</strong>
                  Agent searches catalog and evaluates candidate items against policy.
                </div>
              </div>

              <div className="flex gap-3">
                <div className="w-6 h-6 rounded-full bg-purple-500/20 text-purple-400 flex items-center justify-center flex-shrink-0 font-bold">
                  3
                </div>
                <div>
                  <strong className="text-gray-200 block">PayPal Authorization Hold</strong>
                  PayPal Orders API v2 holds the exact fund amount without charging.
                </div>
              </div>

              <div className="flex gap-3">
                <div className="w-6 h-6 rounded-full bg-green-500/20 text-green-400 flex items-center justify-center flex-shrink-0 font-bold">
                  4
                </div>
                <div>
                  <strong className="text-gray-200 block">Capture or Void</strong>
                  If all policies pass, capture is triggered. If rejected, authorization is instantly voided.
                </div>
              </div>
            </div>

            <div className="pt-2 border-t border-gray-800">
              <Link
                href="/dashboard/vault"
                className="text-xs text-primary-400 hover:text-primary-300 font-medium flex items-center justify-between"
              >
                <span>Manage Vaulted Payment Methods</span>
                <ArrowRight className="w-3.5 h-3.5" />
              </Link>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
