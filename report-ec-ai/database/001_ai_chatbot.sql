-- =============================================================================
-- Report EC – database support for the AI chatbot (Member 6)
-- Migration 001, 18 October 2026
--
-- SAFE TO RUN ON THE SHARED DATABASE:
--   * It only ADDS things (4 nullable columns, 1 index, 1 table, 1 view).
--   * It does not drop, rename or change any existing table, column, constraint
--     or row. The dashboard and the existing backend keep working unchanged.
--   * It can be run more than once (everything uses "if not exists").
--   * To undo it, see 001_ai_chatbot_rollback.sql.
--
-- Run it in Supabase: SQL Editor -> New query -> paste -> Run.
-- =============================================================================

begin;

-- -----------------------------------------------------------------------------
-- 1. reports: extra fields sent by the chatbot
-- -----------------------------------------------------------------------------
alter table public.reports
  add column if not exists idempotency_key text,
  add column if not exists location_text   text,
  add column if not exists municipality    text,
  add column if not exists ai_suggestion   jsonb;

comment on column public.reports.idempotency_key is
  'Set by the chatbot. The same key must never create a second report (stops double submissions).';
comment on column public.reports.location_text is
  'Location exactly as the resident typed it.';
comment on column public.reports.municipality is
  'Municipality named by the resident, recognised from the area, or the pilot default.';
comment on column public.reports.ai_suggestion is
  'AI suggestion (category, urgency, reasons, safety indicators, possible duplicates). ADVISORY ONLY: staff decide.';

-- One report per idempotency key. Old rows (key = null) are not affected.
create unique index if not exists reports_idempotency_key_key
  on public.reports (idempotency_key)
  where idempotency_key is not null;

-- -----------------------------------------------------------------------------
-- 2. report_verifications: residents confirming or disputing a "fixed" claim
--    Rows are never changed by the AI and never change a report's status.
-- -----------------------------------------------------------------------------
create table if not exists public.report_verifications (
  id              bigint generated always as identity primary key,
  report_id       bigint not null references public.reports (id),
  phone           text   not null,
  stance          text   not null check (stance in ('confirm', 'dispute', 'unsure')),
  statement       text,
  evidence        jsonb  not null default '[]'::jsonb,
  ai_advisory     jsonb,
  channel         text   check (channel in ('whatsapp', 'ussd')),
  language        text   not null default 'en',
  idempotency_key text   not null unique,
  created_at      timestamptz not null default now()
);

comment on table public.report_verifications is
  'Resident responses to a municipal "fixed" claim. ai_advisory is advisory only; the verification workflow sets the status.';

create index if not exists report_verifications_report_id_idx
  on public.report_verifications (report_id);

-- Only the backend (secret key) may read or write it.
alter table public.report_verifications enable row level security;

-- -----------------------------------------------------------------------------
-- 3. Summary per report, for the dashboard
--    security_invoker = the view follows the permissions of whoever reads it,
--    so it can't be used to get around row level security.
-- -----------------------------------------------------------------------------
create or replace view public.report_verification_summary
with (security_invoker = true) as
select
  v.report_id,
  r.ref,
  count(*) filter (where v.stance = 'confirm') as confirms,
  count(*) filter (where v.stance = 'dispute') as disputes,
  count(*) filter (where v.stance = 'unsure')  as unsure,
  bool_or(coalesce((v.ai_advisory ->> 'contradiction_detected')::boolean, false)) as contradiction_flagged,
  max(v.created_at) as last_response_at
from public.report_verifications v
join public.reports r on r.id = v.report_id
group by v.report_id, r.ref;

-- -----------------------------------------------------------------------------
-- 4. bot_sessions: structure unchanged. The AI chatbot stores its conversation
--    state here, with a HASHED session key in the "phone" column (never the
--    real number). An index helps the daily clean-up of old sessions.
-- -----------------------------------------------------------------------------
create index if not exists bot_sessions_updated_at_idx
  on public.bot_sessions (updated_at);

-- -----------------------------------------------------------------------------
-- 5. Keep the new objects away from the public API keys (Supabase roles)
-- -----------------------------------------------------------------------------
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    revoke all on public.report_verifications        from anon, authenticated;
    revoke all on public.report_verification_summary from anon, authenticated;
  end if;
end
$$;

commit;

-- -----------------------------------------------------------------------------
-- Check (run separately after the migration): should list the 4 new columns
-- -----------------------------------------------------------------------------
-- select column_name, data_type
-- from information_schema.columns
-- where table_schema = 'public' and table_name = 'reports'
--   and column_name in ('idempotency_key', 'location_text', 'municipality', 'ai_suggestion');
