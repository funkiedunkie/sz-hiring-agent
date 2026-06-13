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
  └─▶ notifications/follow_up.py → follow-ups, auto-archive, manager reply notifications

schedule_interviews.py  (cron, runs after follow_up.py)
  └─▶ notifications/scheduling.py → parse availability replies → ClubReady booking → confirm or fallback

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
| `notifications/sms_sender.py` | Twilio SMS: `send_interview_invite()` (templated) + `send_sms(phone, body)` (custom) → returns SID; routes via `messaging_service_sid` when `TWILIO_MESSAGING_SERVICE_SID` is set (A2P 10DLC compliant), falls back to `from_=TWILIO_FROM_NUMBER` |
| `notifications/email_sender.py` | Graph API email: `send_outreach_email()` (auto-pipeline, returns bool) + `send_email(to, subject, body)` (dashboard, draft→send, returns Graph message ID for dedup) |
| `sync/sms_sync.py` | Backfill Twilio inbound+outbound SMS into `messages` for all known applicants |
| `sync/email_sync.py` | Backfill Graph inbox+sentitems emails into `messages` for all known applicants |
| `main.py` | Orchestrates the full pipeline |
| `follow_up.py` | Cron entry point: follow-ups, auto-archive, manager notifications (calls `notifications/follow_up.py`) |
| `notifications/follow_up.py` | Follow-up logic + `run_reply_notifications()`: texts `MANAGER_PHONE` when a candidate replied and the agent needs direction; dedup via `reply_notified_at` |
| `schedule_interviews.py` | Cron entry point: processes availability replies and books ClubReady slots |
| `scrapers/clubready.py` | Playwright: `block_time(date_str, time_str, name, phone, email)` blocks 30-min slot in ClubReady (blue, detail note) |
| `agents/availability_parser.py` | Claude parser: `parse_availability(reply_text)` → `[{date, time}]` |
| `notifications/scheduling.py` | Availability request + ClubReady booking pipeline; `send_availability_request()` + `process_scheduling_replies()` |
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
- **Advance to 1-Hr Interview** button: reveals a form defaulting to the candidate's preferred channel (with opt-in checkbox for the other channel); pre-filled with Option D availability request ("share a couple days and times"); logs to `messages`, sets `one_hr_invited = true`, and sets `scheduling_requested_at = now` so the scheduling cron knows to watch for replies
- Cards show `✅` when `scheduled_block_at` is set (ClubReady block confirmed) and `📨` when `scheduling_fallback_sent_at` is set (fallback email sent to Boise staff)
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

## ClubReady scheduling automation — `scrapers/clubready.py`

Playwright automation that books a 30-minute block on the practitioner grid.

**Login URL:** `https://stretchzone.clubready.com/` (select Boise location)
**Grid URL:** `https://app.clubready.com/admin/schedulinggridviewall.asp`

**Grid cells:** each available slot is `onclick="selectfree(date, time, staffId, staffId)"`.  
Use `updategrid(dateStr)` (JS) to jump to a date. Cells exist in 5-min increments; `block_time` picks the first cell matching the requested on-the-hour/half-hour time (any practitioner).

**Block-out form fields** (after clicking "Block Out Some Time"):
- `#startHour`, `#startMinute`, `#startPeriod` — pre-filled from cell click
- `#endHour`, `#endMinute`, `#endPeriod` — set to start + 30 min
- `choosecolor(4)` — blue (colors: 0=white, 1=red, 2=purple, 3=yellow, 4=blue, 5=green)
- `#comm` (textarea) — `"Prospective Practitioner- {name}, {phone}, {email}"`
- `#make-unavailable-btn` — submit

**`block_time(date_str, time_str, candidate_name, phone, email)`** — returns True on success.

### Scheduling pipeline — `notifications/scheduling.py`

1. **`send_availability_request(applicant)`** — sends Option D message (ask for times), sets `scheduling_requested_at`
2. **`process_scheduling_replies()`** — cron target:
   - Queries candidates with `scheduling_requested_at` set, no booking or fallback yet
   - Reads their most recent inbound message after `scheduling_requested_at`
   - Claude parses the reply → `[{date, time}]`
   - Tries `block_time` for each slot; on first success: confirms to candidate, sets `scheduled_block_at + calendly_booked = true`
   - If no slot matches: emails `boise@stretchzone.com` ("Prospective Practitioner — {name}"), sets `scheduling_fallback_sent_at`

**Confirmation SMS:** "Great news — you're all set for {day}, {date} at {time}. Looking forward to seeing you! — Duncan"  
**Fallback subject:** "Prospective Practitioner — {name}"  
**Fallback body:** "Howdy. Could you please reach out to {name}...I wasn't able to find a time that worked for them."

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
TWILIO_MESSAGING_SERVICE_SID  # A2P 10DLC compliant routing; when set, all SMS routes through this service instead of from_ number

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

# ClubReady (scheduling automation)
CLUBREADY_USERNAME             # ClubReady login username
CLUBREADY_PASSWORD             # ClubReady login password
CLUBREADY_FALLBACK_EMAIL       # default: boise@stretchzone.com

# Manager notifications (optional)
MANAGER_PHONE                  # Duncan's E.164 phone number; if set, agent texts when a candidate reply needs direction

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
    followup_sent_at          timestamptz,
    scheduling_requested_at   timestamptz,   -- when availability request was sent
    scheduled_block_at        timestamptz,   -- when ClubReady block was successfully booked
    scheduling_fallback_sent_at timestamptz, -- when fallback email was sent to Boise staff
    archived                  boolean default false,
    reply_notified_at         timestamptz    -- when manager was last texted about a candidate reply
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

## Manager reply notifications — `run_reply_notifications()` in `notifications/follow_up.py`

Runs on every cron tick (inside `follow_up.py`). Texts `MANAGER_PHONE` whenever a candidate replied and the agent needs human direction.

**Triggers a text when:**
- Candidate has an inbound message newer than `reply_notified_at` (or `reply_notified_at` is null)
- Candidate is not already fully handled: `calendly_booked = false`, `scheduled_block_at` null, `scheduling_fallback_sent_at` null
- `MANAGER_PHONE` env var is set

**Text format:**
`"Hi Duncan — {first_name} replied: "{snippet}" — I need your direction. Check the dashboard. — SZ Agent"`

**Dedup:** `reply_notified_at` is set to now on send. Resets automatically on the next inbound (any new inbound with a timestamp newer than `reply_notified_at` triggers a fresh notification). No re-notification for the same message across cron ticks.

**Does not notify when:**
- `calendly_booked = true` or `scheduled_block_at` set (already handled)
- `scheduling_fallback_sent_at` set (already escalated to boise@)
- `MANAGER_PHONE` not set (graceful no-op)

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
- **Most recent message is inbound, ≤ 5 business days ago** → GREEN; ball is in our court
- **Most recent message is inbound, > 5 business days ago** → clock runs from that inbound's timestamp (unanswered reply escalates to amber/red/auto-archive)
- **No messages, was invited** → clock from `invite_sent_at`
- **DQ'd (no messages ever sent)** → clock from `created_at`
- **`calendly_booked = true` or `scheduled_block_at` is set** → always GREEN regardless of messages

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

## Dashboard — Streamlit Cloud deployment

The dashboard (`dashboard.py`) is deployed on Streamlit Cloud.

- **`packages.txt`** — lists apt packages Streamlit Cloud installs at build time (Chromium system-level deps: NSS, ATK, DRM, etc.) so Playwright's headless shell can run
- **Browser install at startup** — `dashboard.py` calls `playwright install chromium` once via a `@st.cache_resource` function on first container start; cached so it doesn't re-run on every page refresh; re-runs automatically when Streamlit Cloud spins up a fresh container

## Running locally

```bash
# One-shot run (processes any unread trigger emails right now)
python main.py

# Process a single applicant by URL directly (bypasses email trigger)
python main.py --url https://app.careerplug.com/manage/apps/<id>

# Re-scrape an existing applicant to patch missing email/phone, then send invite if not yet sent
python main.py --repatch --url https://app.careerplug.com/manage/apps/<id>

# Run follow-ups + auto-archive manually
python follow_up.py

# Process availability replies + book ClubReady slots manually
python schedule_interviews.py

# Install dependencies (first time)
pip install -r requirements.txt
playwright install chromium
```

## Skill routing

When the user's request matches an available skill, invoke it via the Skill tool. When in doubt, invoke the skill.

Key routing rules:
- Product ideas/brainstorming → invoke /office-hours
- Strategy/scope → invoke /plan-ceo-review
- Architecture → invoke /plan-eng-review
- Design system/plan review → invoke /design-consultation or /plan-design-review
- Full review pipeline → invoke /autoplan
- Bugs/errors → invoke /investigate
- QA/testing site behavior → invoke /qa or /qa-only
- Code review/diff check → invoke /review
- Visual polish → invoke /design-review
- Ship/deploy/PR → invoke /ship or /land-and-deploy
- Save progress → invoke /context-save
- Resume context → invoke /context-restore
- Author a backlog-ready spec/issue → invoke /spec
