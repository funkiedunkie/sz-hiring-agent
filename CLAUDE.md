# Stretch Zone 1082 — Hiring Agent

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
```

### Module map

| Path | Role |
|------|------|
| `config.py` | Loads all env vars; raises on missing |
| `triggers/email_trigger.py` | Polls Outlook inbox via Microsoft Graph API |
| `scrapers/careerplug.py` | Playwright login → scrape specific application URL |
| `agents/resume_scorer.py` | Claude `claude-sonnet-4-20250514` scorer |
| `db/supabase_logger.py` | Insert applicant rows into Supabase |
| `notifications/sms_sender.py` | Twilio SMS interview invite → candidate |
| `notifications/email_sender.py` | Graph API outreach email → candidate |
| `main.py` | Orchestrates the full pipeline |
| `backfill.py` | One-shot backfill: scrapes all CareerPlug apps, skips ones already in Supabase, runs full pipeline for new ones |
| `supabase/functions/calendly-webhook/index.ts` | Edge Function: marks applicant as booked on Calendly `invitee.created` |

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
- Provider: **Microsoft Graph API** (`POST /users/{GRAPH_USER_EMAIL}/sendMail`)
- Uses the same Graph token approach as `triggers/email_trigger.py`
- **Not SMTP. Not Gmail. No `smtplib`.**
- Subject: `Next step — Stretch Practitioner interview (Stretch Zone 1082)`
- Body template:
  ```
  {first_name}, thank you for your interest in the Stretch Practitioner position.
  After reviewing your application, I'd like to schedule a 15-minute virtual interview.
  You can grab a time here: {calendly_link}.
  Thanks, Duncan Richardson
  ```

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
CALENDLY_LINK                  # full URL, e.g. https://calendly.com/duncan/15min
CALENDLY_WEBHOOK_SIGNING_KEY   # from Calendly Developer → Webhooks → Signing Key (used by Edge Function)

# Optional
EMAIL_TRIGGER_SUBJECT          # default: "New Application"
SCORE_NOTIFY_THRESHOLD         # default: 2  (1–4 star scale)
```

## Supabase — `applicants` table schema

```sql
create table if not exists applicants (
    id                uuid primary key default gen_random_uuid(),
    created_at        timestamptz default now(),
    name              text not null,
    email             text,
    phone             text,
    profile_url       text,
    application_text  text,
    score             int,           -- 1–4 stars; 0 = auto-disqualified
    auto_disqualified boolean default false,
    reasoning         text,
    score_model       text,
    sms_sid           text,
    trigger_subject   text
);
```

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

# Install dependencies (first time)
pip install -r requirements.txt
playwright install chromium
```
