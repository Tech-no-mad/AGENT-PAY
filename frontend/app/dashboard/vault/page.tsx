"use client";

import React, { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import {
  CreditCard,
  Plus,
  Trash2,
  ShieldCheck,
  CheckCircle,
  ExternalLink,
  RefreshCw,
  AlertCircle,
} from "lucide-react";
import {
  confirmVault,
  deleteVaultToken,
  getVaultTokens,
  setupVault,
  VaultToken,
} from "@/lib/api";

export default function VaultPage() {
  const searchParams = useSearchParams();
  const [tokens, setTokens] = useState<VaultToken[]>([]);
  const [loading, setLoading] = useState(true);
  const [settingUp, setSettingUp] = useState(false);
  const [statusMessage, setStatusMessage] = useState<{
    type: "success" | "error" | "info";
    text: string;
  } | null>(null);

  const fetchTokens = async () => {
    setLoading(true);
    try {
      const data = await getVaultTokens();
      setTokens(data);
    } catch (err: any) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchTokens();

    // Check if redirected back from PayPal approval
    const isSuccess = searchParams.get("success");
    const setupTokenId =
      searchParams.get("setup_token") ||
      searchParams.get("token") ||
      searchParams.get("setup_token_id");

    if (isSuccess && setupTokenId) {
      handleConfirmReturn(setupTokenId);
    }
  }, [searchParams]);

  const handleConfirmReturn = async (tokenParam: string) => {
    setStatusMessage({ type: "info", text: "Exchanging setup token with PayPal Vault..." });
    try {
      await confirmVault(tokenParam);
      setStatusMessage({
        type: "success",
        text: "PayPal payment method successfully vaulted!",
      });
      fetchTokens();
    } catch (err: any) {
      setStatusMessage({
        type: "error",
        text: err.message || "Failed to confirm payment method with PayPal",
      });
    }
  };

  const handleSetupVault = async () => {
    setSettingUp(true);
    setStatusMessage(null);
    try {
      const res = await setupVault();
      if (res.approval_url) {
        // Redirect user to PayPal approval experience
        window.location.href = res.approval_url;
      } else {
        setStatusMessage({
          type: "error",
          text: "PayPal did not return an approval link. Check sandbox credentials.",
        });
        setSettingUp(false);
      }
    } catch (err: any) {
      setStatusMessage({
        type: "error",
        text: err.message || "Failed to initialize PayPal Vault setup",
      });
      setSettingUp(false);
    }
  };

  const handleDelete = async (id: string) => {
    if (!confirm("Are you sure you want to remove this payment method?")) return;
    try {
      await deleteVaultToken(id);
      setTokens((prev) => prev.filter((t) => t.id !== id));
      setStatusMessage({ type: "success", text: "Payment method removed." });
    } catch (err: any) {
      alert(err.message || "Failed to remove payment method");
    }
  };

  return (
    <div className="max-w-5xl mx-auto space-y-8">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-100 flex items-center gap-2">
            <CreditCard className="w-6 h-6 text-primary-400" />
            Payment Rails & Vault Tokens
          </h1>
          <p className="text-gray-400 text-sm mt-1">
            PayPal Vault v3 allows autonomous AI agents to transact on your behalf without
            exposing passwords or raw card numbers.
          </p>
        </div>

        <button
          onClick={handleSetupVault}
          disabled={settingUp}
          className="flex items-center gap-2 px-5 py-2.5 rounded-lg bg-primary-600 hover:bg-primary-500 disabled:opacity-50 text-white font-medium text-sm shadow-md shadow-primary-600/20 transition-all self-start sm:self-auto"
        >
          {settingUp ? (
            <span>Connecting PayPal...</span>
          ) : (
            <>
              <Plus className="w-4 h-4" />
              <span>Connect PayPal Wallet</span>
            </>
          )}
        </button>
      </div>

      {/* Status Notice */}
      {statusMessage && (
        <div
          className={`p-4 rounded-xl text-sm flex items-center gap-2.5 ${
            statusMessage.type === "success"
              ? "bg-green-500/10 border border-green-500/20 text-green-400"
              : statusMessage.type === "error"
              ? "bg-red-500/10 border border-red-500/20 text-red-400"
              : "bg-primary-500/10 border border-primary-500/20 text-primary-300"
          }`}
        >
          {statusMessage.type === "success" ? (
            <CheckCircle className="w-5 h-5 flex-shrink-0" />
          ) : (
            <AlertCircle className="w-5 h-5 flex-shrink-0" />
          )}
          <span>{statusMessage.text}</span>
        </div>
      )}

      {/* How Vault Works Card */}
      <div className="p-6 rounded-xl border border-gray-800 bg-gray-900/40 space-y-3">
        <h3 className="text-sm font-semibold text-gray-200 flex items-center gap-2">
          <ShieldCheck className="w-4 h-4 text-primary-400" />
          How Vault Tokens Safeguard Autonomous Commerce
        </h3>
        <p className="text-xs text-gray-400 leading-relaxed">
          When you link your PayPal account, PayPal issues a cryptographically-scoped{" "}
          <code className="text-primary-300 bg-gray-950 px-1 py-0.5 rounded">
            payment_token_id
          </code>
          . The agent utilizes this token to construct pre-authorized escrow orders. You can revoke
          access at any moment without needing to update credit card credentials or bank links.
        </p>
      </div>

      {/* Token List */}
      <div className="space-y-4">
        <h2 className="text-lg font-semibold text-gray-100">Saved Payment Methods</h2>

        {loading ? (
          <div className="p-16 text-center text-gray-500 rounded-xl border border-gray-800 bg-gray-900/30">
            <RefreshCw className="w-6 h-6 animate-spin mx-auto mb-2 text-primary-400" />
            <span>Loading payment methods...</span>
          </div>
        ) : tokens.length === 0 ? (
          <div className="p-16 text-center rounded-xl border border-dashed border-gray-800 bg-gray-900/20 space-y-3">
            <CreditCard className="w-10 h-10 text-gray-500 mx-auto" />
            <h3 className="text-base font-medium text-gray-200">No payment methods linked yet</h3>
            <p className="text-sm text-gray-400 max-w-sm mx-auto">
              Save your PayPal account once to enable the autonomous agent to execute purchases
              strictly matching your mandates.
            </p>
            <button
              onClick={handleSetupVault}
              className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-primary-600 text-white text-xs font-medium hover:bg-primary-500 transition-colors"
            >
              <Plus className="w-4 h-4" />
              <span>Add PayPal Sandbox Method</span>
            </button>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {tokens.map((token) => (
              <div
                key={token.id}
                className="p-5 rounded-xl border border-gray-800 bg-gray-900/60 flex items-center justify-between gap-4"
              >
                <div className="flex items-center gap-3.5">
                  <div className="w-11 h-11 rounded-xl bg-gradient-to-tr from-primary-600 to-sky-400 flex items-center justify-center text-white font-bold shadow-md shadow-primary-500/20">
                    PP
                  </div>
                  <div>
                    <h4 className="text-sm font-semibold text-gray-100">
                      {token.display_name}
                    </h4>
                    <div className="text-[11px] text-gray-400 flex items-center gap-2 mt-0.5">
                      <span>Token: {token.paypal_token_id.slice(0, 16)}...</span>
                      <span className="w-1.5 h-1.5 rounded-full bg-emerald-400" />
                      <span className="text-emerald-400 font-medium">Ready</span>
                    </div>
                  </div>
                </div>

                <button
                  onClick={() => handleDelete(token.id)}
                  className="p-2 text-gray-500 hover:text-red-400 hover:bg-red-500/10 rounded-lg transition-colors"
                  title="Remove payment method"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
