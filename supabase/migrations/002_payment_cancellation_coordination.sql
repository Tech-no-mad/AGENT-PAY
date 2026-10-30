-- Apply after 001_initial_schema.sql, before deploying the corresponding backend.
-- Conditional row updates serialize payment operations and cancellation across workers.
alter table public.mandates
    add column cancel_requested boolean not null default false,
    add column payment_operation text check (payment_operation in ('authorize', 'capture', 'void')),
    add column payment_operation_id uuid,
    add column payment_started_at timestamptz,
    add constraint mandates_payment_operation_pair check (
        (payment_operation is null and payment_operation_id is null and payment_started_at is null)
        or
        (payment_operation is not null and payment_operation_id is not null and payment_started_at is not null)
    );

comment on column public.mandates.cancel_requested is
    'Durable stop flag; remains true on failed void so payment tools cannot resume.';
comment on column public.mandates.payment_operation_id is
    'Exclusive operation claim. Unknown authorization/capture outcomes require reconciliation; never clear by elapsed time alone.';
