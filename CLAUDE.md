# Stretch Zone — Hiring Agent

Autonomous hiring agent for Stretch Zone franchise 1082 (Meridian, ID).
Watches for new CareerPlug applications, scores them with Claude, and sends
outreach to qualified candidates — all without manual intervention.

## Architecture

```
Email trigger (Graph API)
  └─▶ CareerPlug scraper (Playwright)
        └─▶ Resume scorer (Claude)
              ├─▶ Supabase logger          (always)
              └─▶ Outreach (SMS + Email)   (score >= threshold, not auto-DQ)

Calendly webhook (Supabase Edge Function)
  └─▶ invitee.created → match email → set calendly_booked = true

Twilio inbound webhook (Supabase Edge Function)
  └─▶ candidate SMS reply → match phone → insert messages row

sync/sms_sync.py  (cron / dashboard button)
  └─▶ Twilio API → backfill all inbound+outbound SMS for known applicants

sync/email_sync.py  (cron / dashboard button)
  └─▶ Graph API inbox + sentitems → backfill all emails for known applicants

follow_up.py  (cron, runs after main.py)
  └─▶ notifications/follow_up.py → send follow-up to non-responders after 2 business days

dashboard.py (Streamlit)
  └─▶ per-applicant conversation thread (SMS + email interleaved) + reply controls
```

### Module map

| Path | Role |
|------|------|
| `config.py` | Loads all env vars; raises on missing |
| `triggers/email_trigger.py` | Polls Outlook inbox via Microsoft Graph API |
| `scrapers/careerplug.py` | Playwright login → scrape application URL; `deactivate_applicant(profile_url, reason)` deactivates a candidate in CareerPlug |
| `agents/resume_scorer.py` | Claude `claude-sonnet-4-20250514` scorer |
| `db/supabase_logger.py` | Insert applicant rows into Supabase |
| `db/messages_logger.py` | Insert / fetch rows from the `messages` table |
| `notifications/sms_sender.py` | Twilio SMS: `send_interview_invite()` (templated) + `send_sms(phone, body)` (custom) → returns SID |
| `notifications/email_sender.py` | Graph API email: `send_outreach_email()` (auto-pipeline, returns bool) + `send_email(to, subject, body)` (dashboard, draft→send, returns Graph message ID for dedup) |
| `sync/sms_sync.py` | Backfill Twilio inbound+outbound SMS into `messages` for all known applicants |
| `sync/email_sync.py` | Backfill Graph inbox+sentitems emails into `messages` for all known applicants |
| `main.py` | Orchestrates the full pipeline |
| `follow_up.py` | Cron entry point: sends follow-ups to non-responders (calls `notifications/follow_up.py`) |
| `notifications/follow_up.py` | Follow-up logic: 2-business-day window, preferred-channel routing, dedup via `followup_sent_at` |
| `backfill.py` | One-shot backfill: scrapes all CareerPlug apps, skips ones already in Supabase, runs full pipeline for new ones |
| `dashboard.py` | Streamlit dashboard: applicant cards + per-applicant SMS/email conversation thread + reply UI |
| `supabase/functions/calendly-webhook/index.ts` | Edge Function: marks applicant as booked on Calendly `invitee.created` |
| `supabase/functions/twilio-webhook/index.ts` | Edge Function: receives Twilio inbound SMS, matches phone → applicant, inserts `messages` row |

## Email trigger — Microsoft Graph API

The trigger uses **Microsoft Graph API** (not IMAP, not Gmail, not `imapclient`).

- Auth: client-credentials OAuth2 flow (app-only, no user sign-in required)
- Token endpoint: `https://login.microsoftonline.com/{GRAPH_TENANT_ID}/oauth2/v2.0/token`
- Mailbox polled: `GRAPH_USER_EMAIL` (the franchise owner's Outlook/M365 account)
- Watches for unread messages whose subject contains `EMAIL_TRIGGER_SUBJECT`
- Marks each processed email as read via `PATCH /users/{email}/messages/{id}`

`poll_once()` returns a list of `TriggerEmail(subject, app_url, email_id)` objects
and is the entry point used by `main.py`.

## Outreach notifications

Both channels fire only when `score >= SCORE_NOTIFY_THRESHOLD` **and** `auto_disqualified = false`.

### SMS — `notifications/sms_sender.py`
- Provider: Twilio
- Recipient: candidate's phone number scraped from CareerPlug
- Template: `"Hi {first_name}, this is Duncan with Stretch Zone. I'd love to set up a quick 15-minute virtual interview — here's a link to grab a time: {calendly_link}"`

### Email — `notifications/email_sender.py`
- Provider: **Microsoft Graph API**
- Uses the same Graph token approach as `triggers/email_trigger.py`
- **Not SMTP. Not Gmail. No `smtplib`.**
- `send_outreach_email()` — used by the auto-pipeline (`main.py`); uses `POST /sendMail`, returns bool
- `send_email(to, subject, body)` — used by the dashboard; uses draft→send (`POST /messages` then `POST /messages/{id}/send`) so the Graph message ID is available immediately for dedup logging in the `messages` table
- Subject: `Next step — Stretch Practitioner interview (Stretch Zone)`
- Body template:
  ```
  {first_name}, thank you for your interest in the Stretch Practitioner position.
  After reviewing your application, I'd like to schedule a 15-minute virtual interview.
  You can grab a time here: {calendly_link}.
  Thanks, Duncan Richardson
  ```

## Dashboard — `dashboard.py`

Streamlit app. Run with `streamlit run dashboard.py`.

- **📥 Sync Messages** button triggers `sync/sms_sync.py` + `sync/email_sync.py` on demand
- Each applicant card has a **staleness color bar** above it (green → black over 7 business days); card label shows `· Day N` when N > 0
- Each applicant card expands to show a threaded conversation (SMS 📱 and email ✉️ interleaved, oldest first)
- **Preferred channel** is detected from the candidate's first inbound message and shown as a badge (`📱 Prefers SMS` / `✉️ Prefers Email`); reply tabs are reordered so preferred channel appears first
- **Reply** section has two tabs — ordered by preferred channel — each with a compose area and Send button
- Outbound messages sent from the dashboard are logged to the `messages` table immediately
- Inbound SMS arrives in real-time via the `twilio-webhook` Edge Function; email replies arrive on next sync
- **Advance to 1-Hr Interview** button: reveals a form defaulting to the candidate's preferred channel (with opt-in checkbox for the other channel); pre-filled with the 1-hr Calendly link (`CALENDLY_LINK_1HR`); logs to `messages` and sets `one_hr_invited = true`
- **Archive / Unarchive** button per card: soft-deletes the applicant (`archived = true`); archived applicants are hidden by default and skipped by sync
- **Show archived** toggle in the header: reveals archived applicants; shows Unarchive button instead of Archive
- **🗑️ Bulk Archive** expander: multiselect any visible applicants and archive them in one click
- **❌ Deactivate in CareerPlug** button per card: choose a rejection reason, click Confirm — Playwright logs into CareerPlug, opens the deactivate drawer, selects the reason, and confirms; on success the applicant is also archived in Supabase

## Twilio inbound webhook — `supabase/functions/twilio-webhook/index.ts`

Supabase Edge Function. Twilio POSTs here when a candidate replies to an SMS.

**Deploy:**
```bash
supabase functions deploy twilio-webhook
```

**Set secrets in Supabase:**
```bash
supabase secrets set TWILIO_AUTH_TOKEN=<your token>
```

**Register in Twilio:**
- Twilio Console → Phone Numbers → your number → Messaging → "A message comes in"
- Set to Webhook, HTTP POST: `https://<your-project-ref>.supabase.co/functions/v1/twilio-webhook`

**Matching logic:** `From` phone (normalized to E.164) → `applicants.phone`. Unmatched numbers return 200 (no retry).

## Message sync — `sync/sms_sync.py` and `sync/email_sync.py`

Run standalone or triggered from the dashboard's **Sync Messages** button.

- `sync/sms_sync.py`: fetches up to 500 inbound + 500 outbound Twilio messages, matches by phone, dedupes by Twilio SID (`external_id`); skips archived applicants
- `sync/email_sync.py`: fetches inbox (inbound) and sentitems (outbound) from Graph API, filters by applicant email addresses, dedupes by Graph message ID (`external_id`); skips archived applicants

## Calendly webhook — `supabase/functions/calendly-webhook/index.ts`

Supabase Edge Function that auto-marks `calendly_booked = true` when an invitee books.

**Deploy:**
```bash
supabase functions deploy calendly-webhook
```

**Set secrets in Supabase:**
```bash
supabase secrets set CALENDLY_WEBHOOK_SIGNING_KEY=<from Calendly developer settings>
```
(`SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` are injected automatically.)

**Register the webhook in Calendly:**
- Calendly → Integrations → Webhooks → Create Webhook
- URL: `https://<your-project-ref>.supabase.co/functions/v1/calendly-webhook`
- Event: `invitee.created`
- Copy the signing key into `CALENDLY_WEBHOOK_SIGNING_KEY`

**Matching logic:** invitee email → `applicants.email`. If no row matches, returns 200 (no retry).

## Scoring rubric (claude-sonnet-4-20250514)

| Stars | Criteria |
|-------|----------|
| ⭐⭐⭐⭐ | 2+ recent relevant jobs **or** exercise science / kinesiology degree |
| ⭐⭐⭐ | 2+ recent relevant jobs, no degree |
| ⭐⭐ | Unrelated but ≥1 yr/role; healthcare-adjacent; single long-tenure employer |
| ⭐ | No relevance, no notable tenure |
| AUTO-DQ | High school student; job hopper (≤7-month stints); unrelated + <2 yr total |

Borderline tiebreaker: sparse resume → round down; sports/GPA/achievements → round up.

## Environment variables

```
# Anthropic
ANTHROPIC_API_KEY

# CareerPlug
CAREERPLUG_EMAIL
CAREERPLUG_PASSWORD
CAREERPLUG_COMPANY_SLUG       # appears in CareerPlug URLs

# Twilio
TWILIO_ACCOUNT_SID
TWILIO_AUTH_TOKEN
TWILIO_FROM_NUMBER             # E.164, e.g. +12085550000

# Supabase
SUPABASE_URL
SUPABASE_SERVICE_ROLE_KEY      # use service role, not anon key

# Microsoft Graph API (email trigger + outreach email)
GRAPH_TENANT_ID
GRAPH_CLIENT_ID
GRAPH_CLIENT_SECRET
GRAPH_USER_EMAIL               # the Outlook mailbox to poll and send from

# Calendly
CALENDLY_LINK                  # 15-min virtual interview link
CALENDLY_LINK_1HR              # 1-hour in-person interview link (default: https://calendly.com/duncan-bodiesinmotionidaho/interview)
CALENDLY_WEBHOOK_SIGNING_KEY   # from Calendly Developer → Webhooks → Signing Key (used by Edge Function)

# Optional
EMAIL_TRIGGER_SUBJECT          # default: "New Application"
SCORE_NOTIFY_THRESHOLD         # default: 2  (1–4 star scale)
```

## Supabase — table schemas

### `applicants`

```sql
create table if not exists applicants (
    id                uuid primary key default gen_random_uuid(),
    created_at        timestamptz default now(),
    name              text not null,
    email             text,
    phone             text,
    profile_url       text,
    application_text  text,
    score                 int,           -- 1–4 stars; 0 = auto-disqualified
    auto_disqualified     boolean default false,
    reasoning             text,
    score_model           text,
    sms_sid               text,
    trigger_subject       text,
    manually_invited      boolean default false,
    invite_sent_at        timestamptz,
    calendly_booked       boolean default false,
    one_hr_invited        boolean default false,
    one_hr_invite_sent_at timestamptz,
    followup_sent_at      timestamptz,
    archived              boolean default false
);
```

### `messages`

```sql
create table if not exists messages (
    id           uuid primary key default gen_random_uuid(),
    created_at   timestamptz default now(),
    applicant_id uuid not null references applicants(id) on delete cascade,
    channel      text not null check (channel in ('sms', 'email')),
    direction    text not null check (direction in ('inbound', 'outbound')),
    body         text,
    subject      text,           -- email only
    external_id  text unique,    -- Twilio SID or Graph message ID (dedup key)
    sent_at      timestamptz
);
```

## Follow-up outreach — `notifications/follow_up.py`

Runs automatically via `follow_up.py` after `main.py` on every cron tick.

**Eligibility criteria (all must be true):**
- `invite_sent_at` is not null (was invited)
- `followup_sent_at` is null (not yet followed up)
- No inbound messages exist for this applicant (they haven't replied)
- `archived = false`, `auto_disqualified = false`
- 2 business days (Mon–Fri) have passed since `invite_sent_at`

**Channel routing:** uses `preferred_channel` (first inbound channel) if known; otherwise defaults to SMS if phone is available, email otherwise.

**Messages:**
- SMS: short — "Hi {first_name}, still interested in Stretch Zone? Grab a time here: {link} or just reply to let me know either way. Thanks, Duncan"
- Email: longer — check in, calendar link, no-pressure opt-out

**Dedup:** `followup_sent_at` is set on success; subsequent cron runs skip the applicant.

## Staleness system — dashboard + auto-archive

### Color chart (business days the ball is in the candidate's court)

| Days | Color | Hex |
|------|-------|-----|
| 0 (booked or they replied) | Green | `#27ae60` |
| 1 | Green | `#27ae60` |
| 2 | Light green | `#82e0aa` |
| 3 | Dark yellow | `#d4ac0d` |
| 4 | Light yellow | `#f9e79f` |
| 5 | Light brown | `#cb9b6e` |
| 6 | Brown | `#7d4e20` |
| 7 | Black | `#1a1a1a` |
| 8+ | Auto-archived | — |

### Clock rules
- **Most recent message is outbound** → clock runs from that message's `sent_at`
- **Most recent message is inbound** → clock paused (GREEN); ball is in our court
- **No messages, was invited** → clock from `invite_sent_at`
- **DQ'd (no messages ever sent)** → clock from `created_at`
- **`calendly_booked = true`** → always GREEN regardless of messages

A colored bar renders above each card in the dashboard. The expander label also shows `· Day N` when N > 0.

### Day 8 auto-archive (runs in `follow_up.py` via `run_auto_archive()`)
- **Auto-DQ'd candidates:** deactivate in CareerPlug ("Did not meet desired qualifications") + archive in Supabase
- **Invited, unresponsive candidates:** deactivate in CareerPlug ("Unresponsive") + archive in Supabase
- **Calendly-booked candidates:** exempt (never auto-archived by this logic)

## Deployment — GitHub Actions (primary)

The agent runs autonomously via `.github/workflows/hiring-agent.yml` on a cron schedule
(**every 10 minutes**, 24/7) — no laptop required.

- Secrets are stored in GitHub → Settings → Secrets and variables → Actions
- Logs: github.com/funkiedunkie/sz-hiring-agent/actions
- Manual trigger: Actions → Hiring Agent → Run workflow
- The Playwright browser binary is cached between runs to keep cold starts fast

To update secrets: `gh secret set SECRET_NAME --body "value" --repo funkiedunkie/sz-hiring-agent`

## Running locally

```bash
# One-shot run (processes any unread trigger emails right now)
python main.py

# Run follow-ups + auto-archive manually
python follow_up.py

# Install dependencies (first time)
pip install -r requirements.txt
playwright install chromium
```
