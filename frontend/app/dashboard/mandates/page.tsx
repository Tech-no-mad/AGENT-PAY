"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { Plus, Search, Filter, RefreshCw } from "lucide-react";
import MandateCard from "@/components/MandateCard";
import { getMandates, Mandate } from "@/lib/api";

type TabType = "all" | "active" | "completed" | "cancelled";

export default function MandatesListPage() {
  const [mandates, setMandates] = useState<Mandate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<TabType>("all");
  const [searchQuery, setSearchQuery] = useState("");

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

  const filteredMandates = mandates.filter((m) => {
    const matchesSearch = m.description
      .toLowerCase()
      .includes(searchQuery.toLowerCase());

    if (!matchesSearch) return false;

    if (activeTab === "all") return true;
    if (activeTab === "active") {
      return ["active", "searching", "evaluating", "authorized"].includes(m.status);
    }
    if (activeTab === "completed") {
      return m.status === "completed";
    }
    if (activeTab === "cancelled") {
      return ["cancelled", "failed"].includes(m.status);
    }
    return true;
  });

  return (
    <div className="space-y-6 max-w-7xl mx-auto">
      {/* Page Title */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-100">Spending Mandates</h1>
          <p className="text-gray-400 text-sm mt-1">
            All autonomous purchase goals and policy rules assigned to the agent.
          </p>
        </div>

        <Link
          href="/dashboard/mandates/new"
          className="flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg bg-primary-600 hover:bg-primary-500 text-white font-medium text-sm shadow-md shadow-primary-600/20 transition-all self-start sm:self-auto"
        >
          <Plus className="w-4 h-4" />
          <span>New Mandate</span>
        </Link>
      </div>

      {/* Filters & Search Toolbar */}
      <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3 p-2 rounded-xl bg-gray-900/40 border border-gray-800">
        {/* Tab Buttons */}
        <div className="flex items-center gap-1 overflow-x-auto">
          {(["all", "active", "completed", "cancelled"] as TabType[]).map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`px-3.5 py-1.5 rounded-lg text-xs font-medium capitalize transition-colors whitespace-nowrap ${
                activeTab === tab
                  ? "bg-primary-600 text-white"
                  : "text-gray-400 hover:text-gray-200 hover:bg-gray-800/50"
              }`}
            >
              {tab}
            </button>
          ))}
        </div>

        {/* Search & Refresh */}
        <div className="flex items-center gap-2">
          <div className="relative flex-1 sm:w-64">
            <Search className="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
            <input
              type="text"
              placeholder="Search mandates..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full bg-gray-900 border border-gray-800 rounded-lg pl-9 pr-3 py-1.5 text-xs text-gray-200 placeholder-gray-500 focus:outline-none focus:border-primary-500"
            />
          </div>

          <button
            onClick={fetchMandates}
            disabled={loading}
            className="p-2 rounded-lg border border-gray-800 bg-gray-900 text-gray-400 hover:text-gray-200 transition-colors"
            title="Refresh"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
          </button>
        </div>
      </div>

      {/* Mandates Content */}
      {loading ? (
        <div className="p-16 text-center text-gray-500 rounded-xl border border-gray-800 bg-gray-900/30">
          <RefreshCw className="w-6 h-6 animate-spin mx-auto mb-2 text-primary-400" />
          <span>Loading mandates...</span>
        </div>
      ) : error ? (
        <div className="p-6 rounded-xl border border-red-500/20 bg-red-500/10 text-red-400 text-sm">
          {error}
        </div>
      ) : filteredMandates.length === 0 ? (
        <div className="p-16 text-center rounded-xl border border-dashed border-gray-800 bg-gray-900/20 space-y-3">
          <p className="text-gray-400 text-sm">No mandates match your criteria.</p>
          <Link
            href="/dashboard/mandates/new"
            className="inline-flex items-center gap-1.5 text-xs text-primary-400 hover:underline"
          >
            <Plus className="w-3.5 h-3.5" />
            <span>Create a new mandate</span>
          </Link>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {filteredMandates.map((mandate) => (
            <MandateCard key={mandate.id} mandate={mandate} />
          ))}
        </div>
      )}
    </div>
  );
}
