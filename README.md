# 🤖 AgentPay — AI-Powered Autonomous Payments

> **PayPal AI Hackathon 2026 Submission**
>
> Let AI agents handle your purchases — safely. Define a spending mandate, and AgentPay's AI autonomously finds the best deal, verifies it against your policy, and executes the payment via PayPal.

![Architecture](https://img.shields.io/badge/Architecture-FastAPI%20%2B%20Next.js-blue)
![AI](https://img.shields.io/badge/AI-Cloudflare%20Workers%20AI-orange)
![Payments](https://img.shields.io/badge/Payments-PayPal%20SDK-003087)
![Database](https://img.shields.io/badge/Database-Supabase-3ECF8E)

---

## 💡 The Problem

As AI agents become capable of finding products, booking services, and negotiating deals, they **cannot safely execute transactions** on behalf of users without:
- Exposing raw payment credentials
- Risking unbounded spending
- Lacking policy guardrails

## 🔑 The Solution

**AgentPay** introduces **policy-guarded autonomous payments**:

1. **User defines a mandate**: _"Find a flight SFO→JFK under $350, departing Friday, economy class. Book it instantly."_
2. **Pricing and category agent runs first**: AI estimates a price range and classifies the purchase, even when the user supplies no budget. The upper estimate becomes the spending ceiling; any stated budget is respected.
3. **Shopping agent searches** for matching deals using the saved constraints
4. **Policy engine validates** the deal against the spending ceiling, category, and other restrictions
5. **AUTHORIZE first**: PayPal holds funds without charging
6. **CAPTURE or VOID**: Money only moves if every policy check passes

The creation form only needs purchase instructions and a saved payment method,
with an optional auto-capture setting. The mandate detail page shows the AI's
estimated range, category, and explanation. Pricing uses the configured Cloudflare
model's knowledge; it is not a live market quote. Product search remains demo data.
Invalid pricing output stops the background execution before search or payment.

A rejected purchase policy ends the mandate immediately: the backend blocks all
further approval/payment attempts and cancels it, voiding any existing hold.
The AI cannot retry a rejected decision with corrected arguments or another item.

After policy approval, the dashboard displays the selected product and price in
a purchase confirmation popup. Continue releases the purchase immediately; Cancel
stops the mandate. With no response, the backend continues automatically after
10 seconds, even if the popup is closed by leaving the page. The countdown uses
the server deadline and refreshes do not reset it. Disabling auto-capture still
leaves the payment authorized for review rather than captured.

Apply `supabase/migrations/004_purchase_confirmation.sql` after migrations 002
and 003 before starting this backend. Restart all workers together so old workers
cannot skip the confirmation gate. The popup is available on every dashboard page.

> The agent **never** sees card numbers or PayPal passwords — it only holds a vault token.

---

## 🏗️ Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   Next.js UI    │────▶│  FastAPI Backend  │────▶│  PayPal Sandbox │
│   (Dashboard)   │     │  (Python)         │     │  Orders + Vault │
└────────┬────────┘     └────────┬─────────┘     └─────────────────┘
         │                       │
         │  Realtime             │  Function Calling
         ▼                       ▼
┌─────────────────┐     ┌──────────────────┐
│    Supabase     │     │ Cloudflare AI    │
│  DB + Auth +    │     │ Llama 3.3 70B    │
│  Realtime       │     │ (Free Tier)      │
└─────────────────┘     └──────────────────┘
```

---

## 🛠️ Tech Stack

| Layer | Technology |
|---|---|
| **Frontend** | Next.js 14 + React + Tailwind CSS |
| **Backend** | Python FastAPI |
| **Database** | Supabase (PostgreSQL + Auth + Realtime) |
| **AI Agent** | Cloudflare Workers AI — Llama 3.3 70B Instruct (`@cf/meta/llama-3.3-70b-instruct-fp8-fast`) |
| **Payments** | PayPal Python SDK (`paypal-server-sdk`) |

---

## 🚀 Quick Start

### Prerequisites
- Python 3.10+
- Node.js 18+
- [PayPal Developer Account](https://developer.paypal.com/) (sandbox)
- [Supabase Account](https://supabase.com/) (free tier)
- [Cloudflare Account](https://dash.cloudflare.com/) (free tier)

### 1. Clone & Setup Environment

```bash
git clone https://github.com/your-username/agentpay.git
cd agentpay
cp .env.example .env
# Edit .env with your credentials
```

### 2. Setup Supabase

1. Create a new Supabase project
2. Go to SQL Editor → paste contents of `supabase/migrations/001_initial_schema.sql` → Run
3. Copy your project URL, anon key, and service role key to `.env`

### 3. Setup PayPal Sandbox

Apply `supabase/migrations/002_payment_cancellation_coordination.sql` after the
initial schema and before starting the updated backend. Drain old background
workers before deployment; do not mix workers that ignore cancellation with the
new coordinator. See `backend/tests/README.md` for the cancellation/reconciliation
runbook. Ship the repository's `shared/` directory with the backend and frontend.

Also apply `supabase/migrations/003_ai_pricing_constraints.sql` before running this
version. It enables the `constraints_defined` audit event. The first orchestrator
stage saves the validated AI decision in the mandate's existing JSON constraints
and audit trail before the shopping model is called.

1. Go to [PayPal Developer Dashboard](https://developer.paypal.com/)
2. Create a sandbox app → copy Client ID and Secret to `.env`
3. Create sandbox Business + Personal test accounts

### 4. Setup Cloudflare Workers AI

1. Go to [Cloudflare Dashboard](https://dash.cloudflare.com/)
2. Navigate to AI → Workers AI
3. Copy your Account ID and create an API Token with Workers AI permissions
4. Add to `.env`

### 5. Start Backend

```bash
pip install -r backend/requirements.txt
python -m uvicorn backend.main:app --reload --port 8000
```

### 6. Start Frontend

```bash
cd frontend
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000) 🎉

---

## 📁 Project Structure

```
agentpay/
├── .env.example
├── .gitignore
├── README.md
│
├── backend/
│   ├── main.py                    # FastAPI entry point
│   ├── config.py                  # Environment config
│   ├── requirements.txt
│   ├── models/schemas.py          # Pydantic models
│   ├── services/
│   │   ├── supabase_client.py     # Supabase DB + Auth
│   │   ├── paypal_service.py      # PayPal SDK wrapper
│   │   ├── cloudflare_ai.py       # Cloudflare AI client
│   │   ├── mandate_service.py     # Mandate CRUD + lifecycle
│   │   └── agent_orchestrator.py  # AI agent loop
│   ├── routes/
│   │   ├── auth.py                # Auth endpoints
│   │   ├── vault.py               # PayPal Vault endpoints
│   │   ├── mandates.py            # Mandate CRUD endpoints
│   │   └── webhooks.py            # PayPal webhook handler
│   └── tools/agent_tools.py       # AI function definitions
│
├── frontend/
│   ├── package.json
│   ├── app/                       # Next.js App Router
│   │   ├── page.tsx               # Landing page
│   │   ├── login/page.tsx         # Auth page
│   │   └── dashboard/             # Protected dashboard
│   ├── components/                # Reusable UI components
│   └── lib/                       # Supabase + API clients
│
└── supabase/
    └── migrations/
        └── 001_initial_schema.sql # Database schema
```

---

## 🔒 Safety Innovation

The **AUTHORIZE → Policy Check → CAPTURE/VOID** pattern is the core safety mechanism:

| Step | What Happens | Money Moves? |
|---|---|---|
| Agent finds deal | Searches products/services | ❌ No |
| Policy check | Validates against mandate constraints | ❌ No |
| AUTHORIZE | PayPal holds funds temporarily | ❌ Held only |
| Policy APPROVED → CAPTURE | Funds transferred | ✅ Yes |
| Policy REJECTED → VOID | Hold released | ❌ Released |

The AI agent **cannot** overspend, charge the wrong amount, or execute unauthorized purchases.

---

## 🏆 Prize Categories Targeted

| Prize | Alignment |
|---|---|
| **1st Place ($12k)** | Novel concept + working demo + real-world impact |
| **Best Agentic Commerce ($5k)** | Core: AI agents autonomously transact via PayPal |
| **Best PayPal + AI ($5k)** | Deep Orders + Vault + Webhooks + AI function calling |
| **Most Creative ($5k)** | First policy-guarded autonomous payment framework |

---

## 📄 License

MIT License — see [LICENSE](LICENSE) for details.

---

Built with ❤️ for the PayPal AI Hackathon 2026
