"use client";

import React, { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import {
  Sparkles,
  CreditCard,
  AlertTriangle,
  ArrowRight,
  Shield,
  HelpCircle,
} from "lucide-react";
import { createMandate, getVaultTokens, VaultToken } from "@/lib/api";
import purchaseCategories from "@shared/purchase_categories.json";

const PROMPT_SUGGESTIONS = [
  "Find noise-cancelling wireless headphones under $150 with 4+ star rating and order them.",
  "Book a round-trip flight SFO to JFK under $350 departing this Friday economy class.",
  "Order an ergonomic mechanical keyboard with quiet switches under $120.",
  "Find a high-capacity portable power bank (20000mAh+) under $45 and purchase it.",
];

export default function NewMandatePage() {
  const router = useRouter();
  const [tokens, setTokens] = useState<VaultToken[]>([]);
  const [loadingTokens, setLoadingTokens] = useState(true);

  const [description, setDescription] = useState("");
  const [selectedTokenId, setSelectedTokenId] = useState("");
  const [maxPrice, setMaxPrice] = useState<string>("");
  const [autoExecute, setAutoExecute] = useState(true);
  const [category, setCategory] = useState("physical_goods");

  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function loadPaymentMethods() {
      setLoadingTokens(true);
      try {
        const methods = await getVaultTokens();
        setTokens(methods);
        if (methods.length > 0) {
          // Select first active token by default
          setSelectedTokenId(methods[0].id);
        }
      } catch (err: any) {
        console.error("Failed to load payment methods:", err);
      } finally {
        setLoadingTokens(false);
      }
    }
    loadPaymentMethods();
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    if (!description.trim()) {
      setError("Please describe the purchase mandate for the AI agent.");
      return;
    }

    if (!selectedTokenId) {
      setError("Please select or add a PayPal payment method.");
      return;
    }

    setSubmitting(true);
    try {
      const mandate = await createMandate({
        description: description.trim(),
        payment_token_id: selectedTokenId,
        constraints: {
          max_price: maxPrice ? parseFloat(maxPrice) : undefined,
          category,
          auto_execute: autoExecute,
        },
      });

      router.push(`/dashboard/mandates/${mandate.id}`);
    } catch (err: any) {
      setError(err.message || "Failed to create mandate");
      setSubmitting(false);
    }
  };

  return (
    <div className="max-w-3xl mx-auto space-y-8">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-gray-100 flex items-center gap-2">
          <Sparkles className="w-5 h-5 text-primary-400" />
          Create Autonomous Spending Mandate
        </h1>
        <p className="text-gray-400 text-sm mt-1">
          Instruct your AI shopping agent with natural language. The agent will parse your
          instructions, respect spending ceilings, hold pre-authorization funds, and only capture
          upon policy approval.
        </p>
      </div>

      {error && (
        <div className="p-4 rounded-xl border border-red-500/20 bg-red-500/10 text-red-400 text-sm flex items-start gap-2.5">
          <AlertTriangle className="w-5 h-5 flex-shrink-0 mt-0.5" />
          <span>{error}</span>
        </div>
      )}

      {/* No payment methods banner */}
      {!loadingTokens && tokens.length === 0 && (
        <div className="p-4 rounded-xl border border-amber-500/20 bg-amber-500/10 text-amber-300 text-sm flex items-center justify-between gap-4">
          <div className="flex items-center gap-2.5">
            <CreditCard className="w-5 h-5 flex-shrink-0" />
            <span>
              You haven't saved a PayPal payment method yet. You need at least one vaulted token.
            </span>
          </div>
          <Link
            href="/dashboard/vault"
            className="px-3 py-1.5 rounded-lg bg-amber-500 text-gray-950 font-medium text-xs hover:bg-amber-400 transition-colors whitespace-nowrap"
          >
            Add PayPal
          </Link>
        </div>
      )}

      <form onSubmit={handleSubmit} className="space-y-6">
        {/* Natural Language Prompt */}
        <div className="p-6 rounded-xl border border-gray-800 bg-gray-900/40 space-y-4">
          <label className="block text-sm font-medium text-gray-200">
            Mandate Instruction (Natural Language)
          </label>
          <textarea
            rows={4}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="e.g. Find noise-cancelling headphones under $150 and order them immediately if ratings are above 4 stars."
            className="w-full bg-gray-950 border border-gray-800 rounded-lg p-3 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-primary-500 transition-colors"
          />

          {/* Prompt Suggestions */}
          <div>
            <span className="text-xs text-gray-400 block mb-2 font-medium">
              Or pick an example prompt:
            </span>
            <div className="flex flex-wrap gap-2">
              {PROMPT_SUGGESTIONS.map((suggestion, idx) => (
                <button
                  key={idx}
                  type="button"
                  onClick={() => setDescription(suggestion)}
                  className="text-xs text-left px-3 py-1.5 rounded-lg bg-gray-800/60 hover:bg-gray-800 text-gray-300 border border-gray-700/50 transition-colors"
                >
                  {suggestion}
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Payment Method & Hard Constraints */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {/* Payment Method Selector */}
          <div className="p-6 rounded-xl border border-gray-800 bg-gray-900/40 space-y-4">
            <label className="block text-sm font-medium text-gray-200 flex items-center justify-between">
              <span>Vaulted Payment Rail</span>
              <Link
                href="/dashboard/vault"
                className="text-xs text-primary-400 hover:underline"
              >
                + Add new
              </Link>
            </label>

            {loadingTokens ? (
              <div className="text-xs text-gray-500 py-3">Loading payment methods...</div>
            ) : tokens.length === 0 ? (
              <div className="text-xs text-gray-400 py-2">
                No saved methods. Please add one in Vault.
              </div>
            ) : (
              <select
                value={selectedTokenId}
                onChange={(e) => setSelectedTokenId(e.target.value)}
                className="w-full bg-gray-950 border border-gray-800 rounded-lg px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-primary-500"
              >
                {tokens.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.display_name} ({t.payment_source_type.toUpperCase()})
                  </option>
                ))}
              </select>
            )}

            <p className="text-[11px] text-gray-500">
              Payments are executed via single-use vaulted token references. The AI agent never sees your actual card or password.
            </p>
          </div>

          {/* Explicit Constraints */}
          <div className="p-6 rounded-xl border border-gray-800 bg-gray-900/40 space-y-4">
            <label className="block text-sm font-medium text-gray-200">
              Explicit Policy Controls (Optional)
            </label>

            <div className="space-y-3">
              <div>
                <span className="text-xs text-gray-400 block mb-1">Max Spending Cap (USD)</span>
                <input
                  type="number"
                  step="0.01"
                  min="1"
                  placeholder="e.g. 150.00"
                  value={maxPrice}
                  onChange={(e) => setMaxPrice(e.target.value)}
                  className="w-full bg-gray-950 border border-gray-800 rounded-lg px-3 py-1.5 text-sm text-gray-200 placeholder-gray-500 focus:outline-none focus:border-primary-500"
                />
              </div>

              <div>
                <span className="text-xs text-gray-400 block mb-1">Category</span>
                <select
                  value={category}
                  onChange={(e) => setCategory(e.target.value)}
                  className="w-full bg-gray-950 border border-gray-800 rounded-lg px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-primary-500"
                >
                  {Object.entries(purchaseCategories).map(([value, definition]) => (
                    <option key={value} value={value}>{definition.label}</option>
                  ))}
                </select>
              </div>

              <div className="flex items-center gap-2 pt-1">
                <input
                  type="checkbox"
                  id="auto_exec"
                  checked={autoExecute}
                  onChange={(e) => setAutoExecute(e.target.checked)}
                  className="rounded border-gray-800 text-primary-600 focus:ring-0 bg-gray-950"
                />
                <label htmlFor="auto_exec" className="text-xs text-gray-300 cursor-pointer">
                  Auto-capture payment once authorization policy checks pass
                </label>
              </div>
            </div>
          </div>
        </div>

        {/* Safety Guarantee Box */}
        <div className="p-4 rounded-xl border border-primary-500/20 bg-primary-500/5 flex items-start gap-3 text-xs text-gray-300">
          <Shield className="w-5 h-5 text-primary-400 flex-shrink-0 mt-0.5" />
          <div>
            <strong className="text-gray-100 block mb-0.5">Dual-Lock Safety Guarantee</strong>
            Every transaction uses PayPal's two-step Orders API: first funds are pre-authorized (held
            in escrow). If the item exceeds constraints or validation fails, authorization is
            immediately voided with zero risk to your account balance.
          </div>
        </div>

        {/* Submit Button */}
        <div className="flex items-center justify-end gap-3 pt-2">
          <Link
            href="/dashboard/mandates"
            className="px-5 py-2.5 rounded-lg border border-gray-800 text-gray-400 hover:text-gray-200 text-sm font-medium transition-colors"
          >
            Cancel
          </Link>
          <button
            type="submit"
            disabled={submitting || tokens.length === 0}
            className="flex items-center gap-2 px-6 py-2.5 rounded-lg bg-primary-600 hover:bg-primary-500 disabled:opacity-50 text-white font-medium text-sm shadow-lg shadow-primary-600/20 transition-all"
          >
            {submitting ? (
              <span>Deploying Agent...</span>
            ) : (
              <>
                <span>Launch Autonomous Agent</span>
                <ArrowRight className="w-4 h-4" />
              </>
            )}
          </button>
        </div>
      </form>
    </div>
  );
}
