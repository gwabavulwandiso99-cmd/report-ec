-- =============================================================================
-- Undo migration 001 (AI chatbot).
--
-- WARNING: this DELETES every resident confirm/dispute response
-- (report_verifications) and the AI suggestions stored on reports.
-- Export them first if they matter. Only run this if the team agrees.
-- Reports themselves are not deleted.
-- =============================================================================

begin;

drop view  if exists public.report_verification_summary;
drop table if exists public.report_verifications;
drop index if exists public.reports_idempotency_key_key;
drop index if exists public.bot_sessions_updated_at_idx;

alter table public.reports
  drop column if exists ai_suggestion,
  drop column if exists municipality,
  drop column if exists location_text,
  drop column if exists idempotency_key;

-- AI chatbot sessions: "<channel>:" followed by 32 hex characters (a hash).
-- Rows keyed by a real number, e.g. 'whatsapp:+2782...', are left alone.
delete from public.bot_sessions
where phone ~ '^(whatsapp|ussd|community_app|test):[0-9a-f]{32}$';

commit;
