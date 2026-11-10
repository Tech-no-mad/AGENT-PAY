"use client";

import { useEffect, useRef, useState } from "react";
import { CheckCircle, Clock, ShieldCheck } from "lucide-react";
import { decidePurchase, getPendingPurchaseConfirmations, Mandate } from "@/lib/api";

export default function PurchaseConfirmationDialog() {
  const dialog = useRef<HTMLDialogElement>(null);
  const [mandates, setMandates] = useState<Mandate[]>([]);
  const [now, setNow] = useState(Date.now());
  const [clockOffset, setClockOffset] = useState(0);
  const [busy, setBusy] = useState<Record<string, boolean>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [connectionError, setConnectionError] = useState(false);
  const dismissed = useRef(new Set<string>());

  useEffect(() => {
    let active = true;
    let fetching = false;
    const poll = async () => {
      if (fetching) return;
      fetching = true;
      try {
        const response = await getPendingPurchaseConfirmations();
        if (active) {
          const serverTime = Date.parse(response.server_time);
          if (Number.isFinite(serverTime)) setClockOffset(serverTime - Date.now());
          setMandates(response.mandates.filter((m) => !dismissed.current.has(m.purchase_confirmation_id || "")));
          setConnectionError(false);
        }
      } catch {
        if (active) setConnectionError(true);
      } finally {
        fetching = false;
      }
    };
    void poll();
    const pollTimer = setInterval(poll, 500);
    const clockTimer = setInterval(() => setNow(Date.now()), 100);
    return () => { active = false; clearInterval(pollTimer); clearInterval(clockTimer); };
  }, []);

  const isOpen = mandates.length > 0;
  useEffect(() => {
    const element = dialog.current;
    if (isOpen && element && !element.open) element.showModal();
    if (!isOpen && element?.open) element.close();
  }, [isOpen]);

  async function respond(mandate: Mandate, decision: "continue" | "cancel") {
    const id = mandate.purchase_confirmation_id;
    if (!id || busy[id]) return;
    setBusy((previous) => ({ ...previous, [id]: true }));
    setErrors((previous) => ({ ...previous, [id]: "" }));
    try {
      await decidePurchase(mandate.id, id, decision);
      dismissed.current.add(id);
      setMandates((previous) => previous.filter((m) => m.purchase_confirmation_id !== id));
      window.dispatchEvent(new Event("mandate-updated"));
    } catch (error) {
      setErrors((previous) => ({ ...previous, [id]: error instanceof Error ? error.message : "Could not save your decision. Please retry." }));
    } finally {
      setBusy((previous) => ({ ...previous, [id]: false }));
    }
  }

  return (
    <dialog
      ref={dialog}
      aria-labelledby="purchase-confirmation-title"
      onCancel={(event) => event.preventDefault()}
      className="fixed inset-0 m-auto w-[calc(100%_-_2rem)] max-w-lg max-h-[85vh] overflow-y-auto rounded-2xl border border-gray-700 bg-gray-900 p-6 text-gray-100 shadow-2xl backdrop:bg-black/70"
    >
      <h2 id="purchase-confirmation-title" className="text-xl font-semibold flex items-center gap-2">
        <ShieldCheck className="w-6 h-6 text-emerald-400" /> Confirm purchase
      </h2>
      <p className="mt-2 text-sm text-gray-400">Policy checks passed. Review the product before payment starts.</p>
      {connectionError && <p role="alert" className="mt-3 text-sm text-amber-300">Connection interrupted. The automatic purchase countdown is still running.</p>}
      <div className="mt-5 space-y-5">
        {mandates.map((mandate) => {
          const id = mandate.purchase_confirmation_id!;
          const product = mandate.purchase_confirmation_product;
          if (!product) return null;
          const expiry = Date.parse(mandate.purchase_confirmation_expires_at || "");
          const seconds = Number.isFinite(expiry) ? Math.max(0, Math.ceil((expiry - now - clockOffset) / 1000)) : 0;
          const disabled = busy[id] || seconds === 0;
          return (
            <section key={id} aria-label={`Confirm ${product.product_name}`} className="space-y-4 rounded-xl border border-gray-700 bg-gray-950 p-4">
              <div>
                <p className="text-xs text-gray-500 mb-1">Mandate #{mandate.id.slice(0, 8)}</p>
                <h3 className="font-semibold text-lg leading-snug">{product.product_name}</h3>
                <p className="mt-2 text-2xl font-bold text-primary-300">{product.currency} {product.price.toFixed(2)}</p>
              </div>
              <div className="flex items-center gap-2 text-sm text-amber-200">
                <Clock className="w-4 h-4 flex-shrink-0" />
                <span>{seconds > 0 ? `Continuing automatically in ${seconds}s` : "Confirmation window ended. Processing your purchase…"}</span>
              </div>
              <p className="text-xs text-gray-400">Select Cancel to stop this purchase. With no response, it continues automatically after 10 seconds.</p>
              {mandate.constraints?.auto_execute === false && <p className="text-xs text-gray-400">Your mandate is set to authorize funds only; automatic capture is disabled.</p>}
              {errors[id] && <p role="alert" className="text-sm text-red-300">{errors[id]}</p>}
              <div className="grid grid-cols-2 gap-3">
                <button type="button" autoFocus disabled={disabled} onClick={() => respond(mandate, "cancel")}
                  className="rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-2.5 text-sm font-medium text-red-300 hover:bg-red-500/20 disabled:opacity-50">Cancel</button>
                <button type="button" disabled={disabled} onClick={() => respond(mandate, "continue")}
                  className="flex items-center justify-center gap-2 rounded-lg bg-primary-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-primary-500 disabled:opacity-50">
                  <CheckCircle className="w-4 h-4" /> {busy[id] ? "Saving…" : "Continue"}
                </button>
              </div>
            </section>
          );
        })}
      </div>
    </dialog>
  );
}
