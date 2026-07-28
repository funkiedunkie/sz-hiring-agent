-- Fix: Supabase security advisor "rls_disabled_in_public" (critical).
--
-- Both public tables were readable/writable by anyone holding the project URL +
-- anon key, which is embedded in every Supabase project and is not a secret.
--
-- Every consumer of these tables authenticates with the SERVICE ROLE key, which
-- bypasses RLS entirely:
--   - db/supabase_logger.py  (main.py, backfill.py, follow_up.py,
--                             schedule_interviews.py, sync/*, dashboard.py)
--   - supabase/functions/calendly-webhook/index.ts
--   - supabase/functions/twilio-webhook/index.ts
-- Nothing uses the anon key, so enabling RLS with ZERO policies is the correct
-- fix: anon + authenticated get no access at all, the agent keeps full access.
--
-- Do NOT add permissive policies here. If a browser-side client is ever added,
-- give it its own narrowly-scoped policy at that time.

alter table public.applicants enable row level security;
alter table public.messages   enable row level security;

-- Belt and braces: revoke the default grants Postgres/PostgREST hand to the
-- anon and authenticated roles, so a future accidental permissive policy still
-- can't expose candidate PII.
revoke all on public.applicants from anon, authenticated;
revoke all on public.messages   from anon, authenticated;
