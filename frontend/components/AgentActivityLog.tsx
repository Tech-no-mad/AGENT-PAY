"use client";

import React, { useEffect, useRef } from "react";
import {
  Activity, AlertCircle, CheckCircle, ChevronRight, Clock, CreditCard,
  Package, Search, ShieldCheck, Sparkles, XCircle,
} from "lucide-react";
import type { Transaction } from "@/lib/api";
import { formatTimestamp } from "@/lib/time";

interface AgentActivityLogProps {
  transactions: Transaction[];
}

const tones = {
  green: "border-emerald-400/25 bg-emerald-400/10 text-emerald-300",
  red: "border-rose-400/25 bg-rose-400/10 text-rose-300",
  blue: "border-sky-400/25 bg-sky-400/10 text-sky-300",
  purple: "border-violet-400/25 bg-violet-400/10 text-violet-300",
  amber: "border-amber-400/25 bg-amber-400/10 text-amber-300",
  gray: "border-gray-600/50 bg-gray-800 text-gray-300",
};

type EventStyle = { title: string; badge: string; tone: keyof typeof tones; icon: typeof Activity; description?: string };

const events: Record<string, EventStyle> = {
  mandate_created: { title: "Mandate received", badge: "Started", tone: "blue", icon: Activity, description: "Your purchase instructions have been recorded." },
  constraints_defined: { title: "Price range & category", badge: "AI estimate", tone: "purple", icon: Sparkles },
  search_started: { title: "Product search", badge: "Search", tone: "blue", icon: Search },
  deal_found: { title: "Product match found", badge: "Match", tone: "blue", icon: Package },
  policy_approved: { title: "Policy check", badge: "Approved", tone: "green", icon: ShieldCheck },
  policy_rejected: { title: "Policy check", badge: "Rejected", tone: "red", icon: XCircle },
  purchase_confirmation_requested: { title: "Purchase confirmation requested", badge: "Review", tone: "amber", icon: Clock, description: "A 10-second window was opened to continue or cancel this purchase." },
  purchase_confirmation_approved: { title: "Purchase confirmed", badge: "Confirmed by you", tone: "green", icon: CheckCircle, description: "You chose to continue with this purchase." },
  purchase_confirmation_auto_approved: { title: "Purchase confirmed", badge: "Auto-confirmed", tone: "green", icon: Clock, description: "The 10-second review window ended without a response. The purchase was allowed to continue." },
  order_created: { title: "Order created", badge: "Created", tone: "purple", icon: CreditCard },
  authorized: { title: "Payment authorized", badge: "On hold", tone: "purple", icon: CreditCard, description: "PayPal authorized the payment. Funds are on hold until capture." },
  captured: { title: "Payment captured", badge: "Paid", tone: "green", icon: CheckCircle, description: "PayPal confirmed that the payment was captured." },
  voided: { title: "Authorization voided", badge: "Voided", tone: "gray", icon: XCircle, description: "The payment authorization was released." },
  cancelled: { title: "Mandate cancelled", badge: "Cancelled", tone: "gray", icon: XCircle },
  failed: { title: "Execution stopped", badge: "Failed", tone: "red", icon: AlertCircle },
  refunded: { title: "Payment refunded", badge: "Refunded", tone: "amber", icon: CreditCard },
};

function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function money(value: unknown, currency: string): string | null {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  try {
    return new Intl.NumberFormat("en-US", { style: "currency", currency, currencyDisplay: "code" }).format(value);
  } catch {
    return `${currency} ${value.toFixed(2)}`;
  }
}

function label(value: string): string {
  return value.replace(/_/g, " ").replace(/^./, (letter) => letter.toUpperCase());
}

export default function AgentActivityLog({ transactions }: AgentActivityLogProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const followLatest = useRef(true);

  useEffect(() => {
    if (scrollRef.current && followLatest.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [transactions.length]);

  if (transactions.length === 0) {
    return (
      <div className="rounded-2xl border border-dashed border-gray-800 bg-gray-900/30 px-6 py-12 text-center">
        <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-2xl border border-sky-400/20 bg-sky-400/10 text-sky-300">
          <Sparkles className="h-5 w-5" aria-hidden="true" />
        </div>
        <p className="text-sm font-medium text-gray-200">Waiting for the first step</p>
        <p className="mt-1 text-sm text-gray-400">Searches, decisions, and payment updates will appear here.</p>
      </div>
    );
  }

  return (
    <div
      ref={scrollRef}
      role="region"
      aria-label="Agent activity timeline"
      tabIndex={0}
      onScroll={(event) => {
        const el = event.currentTarget;
        followLatest.current = el.scrollHeight - el.scrollTop - el.clientHeight < 64;
      }}
      className="max-h-[640px] overflow-y-auto rounded-xl pr-1 outline-none focus-visible:ring-2 focus-visible:ring-primary-400 sm:pr-3"
    >
      <ol className="space-y-4">
        {transactions.map((tx, index) => {
          const details = tx.details && typeof tx.details === "object" ? tx.details : {};
          let event = tx.event_type.toLowerCase();
          if (details.original_event === "MANDATE_CANCELLED") event = "cancelled";
          if (event === "policy_check" && text(details.status).toUpperCase() === "APPROVED") event = "policy_approved";
          if (event === "policy_check" && text(details.status).toUpperCase() === "REJECTED") event = "policy_rejected";
          const style: EventStyle = events[event] || { title: label(event), badge: "Update", tone: "gray", icon: Activity };
          const Icon = style.icon;
          const currency = tx.currency || text(details.currency) || "USD";
          const amount = money(tx.amount ?? details.price, currency);
          const product = text(details.product_name);
          const products: Record<string, unknown>[] | null = Array.isArray(details.products)
            ? details.products.filter((item: unknown) => item && typeof item === "object") : null;
          const reason = event === "policy_approved"
            ? text(details.justification) || text(details.reason)
            : text(details.reason) || text(details.error) || text(details.pricing_reasoning) || text(details.justification);
          const description = reason || (typeof tx.details === "string" ? tx.details : "") || style.description;
          const minimum = money(details.min_price, currency);
          const maximum = money(details.max_price, currency);

          return (
            <li key={tx.id} className="relative flex gap-3 sm:gap-4">
              {index < transactions.length - 1 && <div aria-hidden="true" className="absolute bottom-[-16px] left-[17px] top-10 w-px bg-gray-800 sm:left-[19px]" />}
              <div aria-hidden="true" className={`relative mt-1 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border sm:h-10 sm:w-10 ${tones[style.tone]}`}>
                <Icon className="h-4 w-4 sm:h-[18px] sm:w-[18px]" />
              </div>

              <article className="min-w-0 flex-1 rounded-2xl border border-gray-800 bg-gradient-to-br from-gray-900 to-gray-950 p-4 shadow-sm sm:p-5">
                <header className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
                  <div className="flex flex-wrap items-center gap-2.5">
                    <h4 className="text-sm font-semibold text-gray-100">{style.title}</h4>
                    <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium ${tones[style.tone]}`}>{style.badge}</span>
                  </div>
                  <time dateTime={tx.created_at} title={formatTimestamp(tx.created_at)} className="text-xs tabular-nums text-gray-400">
                    {formatTimestamp(tx.created_at)}
                  </time>
                </header>

                {(product || amount) && (
                  <div className="mt-4 flex flex-col justify-between gap-3 rounded-xl border border-gray-800/80 bg-gray-950/60 p-3.5 sm:flex-row sm:items-center">
                    <div className="min-w-0">
                      <p className="mb-1 text-[10px] font-medium uppercase tracking-widest text-gray-500">{product ? "Selected product" : "Payment amount"}</p>
                      {product && <p className="break-words text-sm font-medium leading-relaxed text-gray-100">{product}</p>}
                      {text(details.category) && <p className="mt-1 text-xs text-gray-400">{label(details.category)}</p>}
                    </div>
                    {amount && <p className="shrink-0 text-lg font-semibold tabular-nums tracking-tight text-gray-100">{amount}</p>}
                  </div>
                )}

                {event === "constraints_defined" && (
                  <div className="mt-4 flex flex-wrap gap-2">
                    {maximum && <span className="rounded-lg border border-violet-400/20 bg-violet-400/5 px-3 py-2 text-sm font-medium text-violet-200">{minimum ? `${minimum} – ${maximum}` : `Up to ${maximum}`}</span>}
                    {text(details.category) && <span className="rounded-lg border border-gray-700 bg-gray-800/50 px-3 py-2 text-sm text-gray-300">{label(details.category)}</span>}
                  </div>
                )}

                {description && (
                  <div className="mt-4">
                    {(event.startsWith("policy_") || event === "constraints_defined") && <p className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-gray-400"><Sparkles aria-hidden="true" className="h-3 w-3" />{event === "policy_rejected" ? "Decision reason" : "AI reasoning"}</p>}
                    <p className={`whitespace-pre-line break-words text-sm leading-relaxed ${style.tone === "red" ? "text-rose-200" : "text-gray-300"}`}>{description}</p>
                  </div>
                )}

                {text(details.query) && (
                  <p className="mt-3 break-words text-sm leading-relaxed text-gray-300">
                    <span className="text-gray-500">Searched for </span>“{details.query}”
                  </p>
                )}
                {products && (
                  <div className="mt-3">
                    <p className="mb-2 text-xs text-gray-400">{products.length} {products.length === 1 ? "match" : "matches"}{details.source === "demo_catalog" ? " · Demo catalog" : ""}</p>
                    <ul className="space-y-2">
                      {products.map((item, productIndex) => (
                        <li key={productIndex} className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-gray-800 bg-gray-950/50 p-3 text-sm">
                          <div className="min-w-0 flex-1 basis-40">
                            <p className="break-words text-gray-200">{text(item.name) || "Product"}</p>
                            {typeof item.capacity_mah === "number" && <p className="mt-1 text-xs text-gray-400">{item.capacity_mah.toLocaleString()} mAh</p>}
                          </div>
                          <span className="font-medium tabular-nums text-sky-200">{money(item.price, text(item.currency) || currency) || "Price unavailable"}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {Object.keys(details).length > 0 && (
                  <details className="group mt-4 border-t border-gray-800/70 pt-3">
                    <summary className="flex w-fit cursor-pointer list-none items-center gap-1 text-xs text-gray-400 outline-none transition-colors hover:text-gray-200 focus-visible:rounded focus-visible:ring-2 focus-visible:ring-primary-400 [&::-webkit-details-marker]:hidden">
                      <ChevronRight aria-hidden="true" className="h-3.5 w-3.5 transition-transform group-open:rotate-90" />
                      Event details
                    </summary>
                    <pre className="mt-3 max-h-64 overflow-auto whitespace-pre-wrap break-all rounded-lg border border-gray-800 bg-gray-950 p-3 text-xs leading-relaxed text-gray-400">{JSON.stringify(details, null, 2)}</pre>
                  </details>
                )}
              </article>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
