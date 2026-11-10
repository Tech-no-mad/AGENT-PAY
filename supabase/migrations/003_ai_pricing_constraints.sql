-- Allow the first AI stage to record its validated pricing/category decision.
alter table public.transactions
    drop constraint if exists transactions_event_type_check;

alter table public.transactions
    add constraint transactions_event_type_check check (event_type in (
        'mandate_created', 'constraints_defined', 'search_started', 'deal_found',
        'policy_approved', 'policy_rejected', 'order_created', 'authorized',
        'captured', 'voided', 'failed', 'refunded'
    ));
