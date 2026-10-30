-- AgentPay — Supabase Database Schema
-- Run this in Supabase SQL Editor (Dashboard > SQL Editor > New Query)

-- Enable UUID extension
create extension if not exists "uuid-ossp";

-----------------------------------------------------------
-- 1. Profiles (extends Supabase auth.users)
-----------------------------------------------------------
create table public.profiles (
    id uuid references auth.users(id) on delete cascade primary key,
    full_name text,
    email text unique not null,
    created_at timestamptz default now()
);

-- Auto-create profile on signup
create or replace function public.handle_new_user()
returns trigger as $$
begin
    insert into public.profiles (id, full_name, email)
    values (
        new.id,
        coalesce(new.raw_user_meta_data->>'full_name', ''),
        new.email
    );
    return new;
end;
$$ language plpgsql security definer;

create trigger on_auth_user_created
    after insert on auth.users
    for each row execute procedure public.handle_new_user();

-----------------------------------------------------------
-- 2. Payment Tokens (vaulted PayPal payment methods)
-----------------------------------------------------------
create table public.payment_tokens (
    id uuid default uuid_generate_v4() primary key,
    user_id uuid references public.profiles(id) on delete cascade not null,
    paypal_token_id text not null,
    payment_source_type text default 'paypal'
        check (payment_source_type in ('paypal', 'card')),
    display_name text,
    is_active boolean default true,
    created_at timestamptz default now()
);

create index idx_payment_tokens_user on public.payment_tokens(user_id);

-----------------------------------------------------------
-- 3. Mandates (spending policies)
-----------------------------------------------------------
create table public.mandates (
    id uuid default uuid_generate_v4() primary key,
    user_id uuid references public.profiles(id) on delete cascade not null,
    payment_token_id uuid references public.payment_tokens(id) not null,

    -- Natural language instruction from the user
    description text not null,

    -- AI-parsed structured constraints (JSONB)
    constraints jsonb not null default '{}'::jsonb,
    -- Example:
    -- {
    --   "max_price": 350.00,
    --   "currency": "USD",
    --   "category": "flight",
    --   "deadline": "2026-11-01T00:00:00Z",
    --   "auto_execute": true,
    --   "additional_rules": ["economy only", "non-stop preferred"]
    -- }

    status text default 'active'
        check (status in (
            'active',       -- Mandate created, waiting for agent
            'searching',    -- Agent is searching for deals
            'evaluating',   -- Agent found options, checking policy
            'authorized',   -- PayPal funds held, awaiting capture
            'completed',    -- Payment captured successfully
            'cancelled',    -- User cancelled or policy rejected
            'failed'        -- Error during execution
        )),

    -- Execution tracking
    paypal_order_id text,
    paypal_authorization_id text,
    executed_amount numeric(10,2),
    agent_reasoning text,          -- AI's decision explanation

    created_at timestamptz default now(),
    updated_at timestamptz default now(),
    expires_at timestamptz
);

create index idx_mandates_user on public.mandates(user_id);
create index idx_mandates_status on public.mandates(status);

-- Auto-update updated_at timestamp
create or replace function public.update_updated_at()
returns trigger as $$
begin
    new.updated_at = now();
    return new;
end;
$$ language plpgsql;

create trigger mandates_updated_at
    before update on public.mandates
    for each row execute procedure public.update_updated_at();

-----------------------------------------------------------
-- 4. Transactions (audit trail)
-----------------------------------------------------------
create table public.transactions (
    id uuid default uuid_generate_v4() primary key,
    mandate_id uuid references public.mandates(id) on delete cascade not null,
    user_id uuid references public.profiles(id) on delete cascade not null,

    event_type text not null
        check (event_type in (
            'mandate_created',
            'search_started',
            'deal_found',
            'policy_approved',
            'policy_rejected',
            'order_created',
            'authorized',
            'captured',
            'voided',
            'failed',
            'refunded'
        )),

    paypal_resource_id text,
    amount numeric(10,2),
    currency text default 'USD',
    details jsonb default '{}'::jsonb,

    created_at timestamptz default now()
);

create index idx_transactions_mandate on public.transactions(mandate_id);
create index idx_transactions_user on public.transactions(user_id);
create index idx_transactions_event on public.transactions(event_type);

-----------------------------------------------------------
-- 5. Enable Realtime (live mandate status in dashboard)
-----------------------------------------------------------
alter publication supabase_realtime add table public.mandates;
alter publication supabase_realtime add table public.transactions;

-----------------------------------------------------------
-- 6. Row Level Security (RLS)
-----------------------------------------------------------

-- Profiles
alter table public.profiles enable row level security;

create policy "Users can view own profile"
    on public.profiles for select
    using (auth.uid() = id);

create policy "Users can update own profile"
    on public.profiles for update
    using (auth.uid() = id);

-- Payment Tokens
alter table public.payment_tokens enable row level security;

create policy "Users can view own tokens"
    on public.payment_tokens for select
    using (auth.uid() = user_id);

create policy "Users can insert own tokens"
    on public.payment_tokens for insert
    with check (auth.uid() = user_id);

create policy "Users can update own tokens"
    on public.payment_tokens for update
    using (auth.uid() = user_id);

create policy "Users can delete own tokens"
    on public.payment_tokens for delete
    using (auth.uid() = user_id);

-- Mandates
alter table public.mandates enable row level security;

create policy "Users can view own mandates"
    on public.mandates for select
    using (auth.uid() = user_id);

create policy "Users can insert own mandates"
    on public.mandates for insert
    with check (auth.uid() = user_id);

create policy "Users can update own mandates"
    on public.mandates for update
    using (auth.uid() = user_id);

create policy "Users can delete own mandates"
    on public.mandates for delete
    using (auth.uid() = user_id);

-- Transactions
alter table public.transactions enable row level security;

create policy "Users can view own transactions"
    on public.transactions for select
    using (auth.uid() = user_id);

-- Service role bypasses RLS for backend operations
-- (Backend uses SUPABASE_SERVICE_KEY which has full access)
