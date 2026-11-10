-- Apply after 003, before starting workers with the purchase confirmation gate.
alter table public.mandates
    add column if not exists purchase_confirmation_id uuid,
    add column if not exists purchase_confirmation_status text
        check (purchase_confirmation_status in ('pending', 'approved', 'auto_approved', 'cancelled')),
    add column if not exists purchase_confirmation_product jsonb,
    add column if not exists purchase_confirmation_expires_at timestamptz,
    add column if not exists purchase_confirmation_ready boolean not null default false;

alter table public.transactions drop constraint if exists transactions_event_type_check;
alter table public.transactions add constraint transactions_event_type_check check (event_type in (
    'mandate_created', 'constraints_defined', 'search_started', 'deal_found',
    'policy_approved', 'policy_rejected', 'order_created', 'authorized', 'captured',
    'voided', 'failed', 'refunded', 'purchase_confirmation_requested',
    'purchase_confirmation_approved', 'purchase_confirmation_auto_approved'
));
