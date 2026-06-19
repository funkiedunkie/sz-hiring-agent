"""Stretch Zone — Hiring Dashboard"""
import subprocess
import sys
import streamlit as st
import pandas as pd
from datetime import datetime, timezone, timedelta

import re
import config
from db.supabase_logger import _client as db

@st.cache_resource(show_spinner="Installing browser…")
def _ensure_playwright_browser(_version: str = ""):
    subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        check=True, capture_output=True,
    )

# Pass the installed Playwright version so any upgrade invalidates the cache
# and re-installs the correct chromium + chromium_headless_shell revision.
try:
    import importlib.metadata
    _pw_version = importlib.metadata.version("playwright")
except Exception:
    _pw_version = ""

_ensure_playwright_browser(_pw_version)

def _strip_html(text: str) -> str:
    """Remove HTML tags and collapse whitespace for display."""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

def _s(val) -> str:
    """Pandas-safe string: returns '' for None, NaN, or 'nan'."""
    if val is None:
        return ""
    try:
        if pd.isna(val):
            return ""
    except (TypeError, ValueError):
        pass
    s = str(val).strip()
    return "" if s == "nan" else s
from db.messages_logger import get_messages_for_applicant, insert_message
from notifications.sms_sender import send_interview_invite, send_sms
from notifications.email_sender import send_outreach_email, send_email
from notifications.scheduling import send_availability_request
from sync.sms_sync import sync_all as sync_sms
from sync.email_sync import sync_all as sync_email
from scrapers.careerplug import deactivate_applicant, DEACTIVATE_REASONS

st.set_page_config(page_title="Stretch Zone Hiring", layout="wide", page_icon="💪")

# ── Data ──────────────────────────────────────────────────────────────────────

def load(show_archived: bool = False) -> pd.DataFrame:
    q = db.table("applicants").select("*").order("created_at", desc=True)
    if not show_archived:
        q = q.neq("archived", True)
    resp = q.execute()
    if not resp.data:
        return pd.DataFrame()
    df = pd.DataFrame(resp.data)
    df["created_at"] = pd.to_datetime(df["created_at"], utc=True).dt.tz_convert("US/Mountain")
    for col in ["score", "auto_disqualified", "manually_invited", "calendly_booked", "sms_sid", "invite_sent_at",
                "one_hr_invited", "one_hr_invite_sent_at", "archived",
                "scheduling_requested_at", "scheduled_block_at", "scheduling_fallback_sent_at",
                "interview_notes"]:
        if col not in df.columns:
            df[col] = None
    return df

def do_invite(row_id: str, name: str, phone: str, email: str):
    now = datetime.now(timezone.utc).isoformat()
    sms_sid = send_interview_invite(candidate_name=name, candidate_phone=phone)
    db.table("applicants").update({
        "manually_invited": True,
        "invite_sent_at": now,
        "sms_sid": sms_sid or "",
    }).eq("id", row_id).execute()
    first_name = name.split()[0] if name else "there"
    sms_body = (
        f"Hi {first_name}, this is Duncan with Stretch Zone. I'd love to set up a "
        f"quick 15-minute virtual interview — here's a link to grab a time: {config.CALENDLY_LINK}"
    )
    if sms_sid:
        insert_message(applicant_id=row_id, channel="sms", direction="outbound",
                       body=sms_body, external_id=sms_sid, sent_at=now)
    email_body = (
        f"{first_name}, thank you for your interest in the Stretch Practitioner position. "
        f"After reviewing your application, I'd like to schedule a 15-minute virtual interview. "
        f"You can grab a time here: {config.CALENDLY_LINK}. "
        f"Thanks, Duncan Richardson"
    )
    email_subj = "Next step — Stretch Practitioner interview"
    if email:
        email_id = send_email(email, email_subj, email_body)
        if email_id:
            insert_message(applicant_id=row_id, channel="email", direction="outbound",
                           body=email_body, subject=email_subj,
                           external_id=email_id, sent_at=now)

def do_deactivate(row_id: str, profile_url: str, reason: str) -> tuple[bool, str]:
    """Archive in Supabase (always), then attempt CareerPlug deactivation.
    Returns (cp_ok, message). Supabase archive always runs first."""
    # Step 1: Supabase — must succeed
    try:
        resp = db.table("applicants").update({"archived": True}).eq("id", str(row_id)).execute()
    except Exception as e:
        return False, f"Supabase update failed: {e}"

    # Step 2: CareerPlug — best effort, catch everything including asyncio errors
    try:
        deactivate_applicant(profile_url, reason)
        return True, f"{reason} — archived and deactivated in CareerPlug."
    except BaseException as e:
        import traceback
        traceback.print_exc()
        return False, f"Archived. CareerPlug deactivation failed: {type(e).__name__}: {e}"

def do_mark_booked(row_id: str):
    db.table("applicants").update({"calendly_booked": True}).eq("id", row_id).execute()

def do_schedule_stretch(row_id: str, phone: str, email: str, sms_body: str, email_body: str,
                        pref_ch: str | None = None) -> int:
    from notifications.sms_sender import send_sms
    from notifications.email_sender import send_email
    from db.messages_logger import insert_message as _ins
    now = datetime.now(timezone.utc).isoformat()
    sent = 0
    if phone and sms_body.strip():
        sid = send_sms(phone, sms_body.strip())
        if sid:
            _ins(applicant_id=row_id, channel="sms", direction="outbound",
                 body=sms_body.strip(), external_id=sid, sent_at=now)
            sent += 1
    if email and email_body.strip():
        email_id = send_email(email, "Scheduling your stretch — Stretch Zone", email_body.strip())
        if email_id:
            _ins(applicant_id=row_id, channel="email", direction="outbound",
                 body=email_body.strip(), subject="Scheduling your stretch — Stretch Zone",
                 external_id=email_id, sent_at=now)
            sent += 1
    if sent:
        db.table("applicants").update({
            "scheduling_requested_at": now,
        }).eq("id", row_id).execute()
    return sent


def do_1hr_interview(row_id: str, phone: str, email: str, pref_ch: str | None = None) -> int:
    from notifications.sms_sender import send_sms
    from notifications.email_sender import send_email
    from db.messages_logger import insert_message as _ins
    now = datetime.now(timezone.utc).isoformat()
    link = config.CALENDLY_LINK_1HR
    sent = 0
    use_sms = (pref_ch == "sms") or (pref_ch is None and phone and not email)
    if use_sms and phone:
        body = f"Great news — I'd love to have you come in for a 1-hour interview! Grab a time here: {link} — Duncan"
        sid = send_sms(phone, body)
        if sid:
            _ins(applicant_id=row_id, channel="sms", direction="outbound",
                 body=body, external_id=sid, sent_at=now)
            sent += 1
    else:
        if email:
            subj = "1-hour interview — Stretch Zone"
            body_email = f"Grab a time for your 1-hour interview here: {link}\n\nThanks,\nDuncan Richardson"
            email_id = send_email(email, subj, body_email)
            if email_id:
                _ins(applicant_id=row_id, channel="email", direction="outbound",
                     body=body_email, subject=subj, external_id=email_id, sent_at=now)
                sent += 1
    if sent:
        db.table("applicants").update({
            "one_hr_invited": True,
            "one_hr_invite_sent_at": now,
        }).eq("id", row_id).execute()
    return sent

# ── Header ────────────────────────────────────────────────────────────────────

st.title("Stretch Zone — Hiring")

col_refresh, col_sync, col_archived, _ = st.columns([1, 2, 2, 3])
with col_refresh:
    if st.button("🔄 Refresh"):
        st.rerun()
with col_sync:
    if st.button("📥 Sync Messages"):
        with st.spinner("Syncing SMS and email..."):
            sms_counts = sync_sms()
            email_counts = sync_email()
        st.success(
            f"Synced — SMS: +{sms_counts['inbound']} in / +{sms_counts['outbound']} out  |  "
            f"Email: +{email_counts['inbound']} in / +{email_counts['outbound']} out"
        )
        st.rerun()
with col_archived:
    show_archived = st.toggle("Show archived", value=False)

df = load(show_archived=show_archived)

# ── Flash messages (persisted across st.rerun) ────────────────────────────────
if "flash" in st.session_state:
    _flash_level, _flash_msg = st.session_state.pop("flash")
    if _flash_level == "success":
        st.success(_flash_msg)
    elif _flash_level == "error":
        st.error(_flash_msg)
    elif _flash_level == "warning":
        st.warning(_flash_msg)

# ── Bulk archive ──────────────────────────────────────────────────────────────

if not df.empty and not show_archived:
    with st.expander("🗑️ Bulk Archive"):
        options = {
            f"{r['name']}  ({'AUTO-DQ' if r.get('auto_disqualified') else '⭐' * int(r.get('score') or 0)})": r["id"]
            for _, r in df.iterrows()
        }
        selected_labels = st.multiselect("Select applicants to archive", list(options.keys()), label_visibility="collapsed")
        if st.button(f"Archive {len(selected_labels)} selected", disabled=not selected_labels, type="primary"):
            ids_to_archive = [options[lbl] for lbl in selected_labels]
            db.table("applicants").update({"archived": True}).in_("id", ids_to_archive).execute()
            st.success(f"Archived {len(ids_to_archive)} applicant(s).")
            st.rerun()

if df.empty:
    st.info("No applicants yet — the agent will populate this when applications arrive.")
    st.stop()

# ── Metrics ───────────────────────────────────────────────────────────────────

total   = len(df)
qual    = len(df[~df["auto_disqualified"].fillna(False) & (df["score"].fillna(0) >= config.SCORE_NOTIFY_THRESHOLD)])
dq      = len(df[df["auto_disqualified"].fillna(False)])
invited = len(df[(df["sms_sid"].fillna("") != "") | df["manually_invited"].fillna(False)])
booked  = len(df[df["calendly_booked"].fillna(False)])

m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Total", total)
m2.metric("Qualified", qual)
m3.metric("Auto-DQ'd", dq)
m4.metric("Invited", invited)
m5.metric("Calendly Booked", booked)

st.divider()

# ── Helpers ───────────────────────────────────────────────────────────────────

def fmt_dt(ts) -> str:
    if ts is None or (isinstance(ts, float) and pd.isna(ts)):
        return ""
    try:
        dt = pd.to_datetime(ts, utc=True).tz_convert("US/Mountain")
        return dt.strftime("%b %d, %Y %I:%M %p MT")
    except Exception:
        return str(ts)


# ── Staleness helpers ─────────────────────────────────────────────────────────

_STALE_COLORS = {
    0: "#27ae60",  # green
    1: "#27ae60",  # green
    2: "#82e0aa",  # light green
    3: "#d4ac0d",  # dark yellow
    4: "#f9e79f",  # light yellow
    5: "#cb9b6e",  # light brown
    6: "#7d4e20",  # brown
    7: "#1a1a1a",  # black
}


def _business_days_since(ts_str) -> int:
    """Count business days (Mon-Fri) from ts_str to today."""
    if not ts_str:
        return 0
    try:
        ts = pd.to_datetime(ts_str, utc=True)
        days = 0
        current = ts.date()
        end = datetime.now(timezone.utc).date()
        while current < end:
            current += timedelta(days=1)
            if current.weekday() < 5:
                days += 1
        return days
    except Exception:
        return 0


def _staleness_days(applicant: dict, messages: list) -> int:
    """Business days the ball has been in the candidate's court (0 = green)."""
    if applicant.get("calendly_booked") or applicant.get("scheduled_block_at"):
        return 0
    if messages:
        most_recent = messages[-1]  # sorted oldest-first by get_messages_for_applicant
        if most_recent["direction"] == "inbound":
            inbound_ts = most_recent.get("sent_at") or most_recent.get("created_at")
            inbound_age = _business_days_since(inbound_ts)
            if inbound_age <= 5:
                return 0  # recently replied; ball is in our court
            # After 5 days with no reply from us, start counting from their message
            start = inbound_ts
        else:
            start = most_recent.get("sent_at") or most_recent.get("created_at")
    elif applicant.get("invite_sent_at"):
        start = applicant["invite_sent_at"]
    else:
        start = applicant.get("created_at")
    return _business_days_since(start)


def _stale_color(days: int) -> str:
    return _STALE_COLORS.get(min(days, 7), "#1a1a1a")


def _pipeline_progress(applicant: dict) -> int:
    """Return milestones completed (0–5). 0 = just applied, 5 = fully through pipeline."""
    if applicant.get("one_hr_invited"):
        return 5
    if applicant.get("scheduled_block_at") or applicant.get("scheduling_fallback_sent_at"):
        return 4
    if applicant.get("scheduling_requested_at"):
        return 3
    if applicant.get("calendly_booked"):
        return 2
    if applicant.get("invite_sent_at") or applicant.get("sms_sid") or applicant.get("manually_invited"):
        return 1
    return 0


def _progress_bar_html(applicant: dict) -> str:
    """Blue fill bar: white when new, solid blue when fully through pipeline; solid red for Auto-DQ'd."""
    if applicant.get("auto_disqualified"):
        return '<div style="height:8px;background:#e74c3c;border-radius:3px;margin-bottom:3px;"></div>'
    pct = int(_pipeline_progress(applicant) / 5 * 100)
    return (
        '<div style="height:8px;background:#e8e8e8;border-radius:3px;overflow:hidden;margin-bottom:3px;">'
        f'<div style="width:{pct}%;height:100%;background:#3498db;"></div>'
        '</div>'
    )


def _preferred_channel_from_messages(messages: list) -> str | None:
    """Return the channel of the first inbound message, or None."""
    for m in messages:
        if m["direction"] == "inbound":
            return m["channel"]
    return None


def render_conversation(applicant_id: str, phone: str, email: str, tab: str = "",
                        sms_sid: str = "", invite_sent_at: str = "",
                        preferred_channel: str | None = None,
                        prefetched_messages: list | None = None):
    """Render the threaded message history and reply controls for one applicant."""
    k = f"{tab}_{applicant_id}"  # unique key prefix per tab+applicant
    messages = prefetched_messages if prefetched_messages is not None else get_messages_for_applicant(applicant_id)

    # Synthetic entry for pre-messages-table invites (sms_sid set but no DB row)
    has_invite_msg = any(
        m["direction"] == "outbound" and m["channel"] == "sms" for m in messages
    )
    synthetic = []
    if sms_sid and not has_invite_msg:
        synthetic = [{
            "channel": "sms", "direction": "outbound",
            "body": f"[Initial 15-min invite sent by agent]",
            "subject": None,
            "sent_at": invite_sent_at or None,
            "created_at": invite_sent_at or None,
        }]

    all_messages = synthetic + messages

    if all_messages:
        st.markdown("**Messages**")
        for m in all_messages:
            ts = fmt_dt(m.get("sent_at") or m.get("created_at"))
            channel_badge = "📱" if m["channel"] == "sms" else "✉️"
            direction_label = "You" if m["direction"] == "outbound" else "Candidate"
            subject_line = f" — *{m['subject']}*" if m.get("subject") else ""

            if m["direction"] == "outbound":
                st.markdown(
                    f"<div style='text-align:right; color:#555 !important; font-size:0.85em'>"
                    f"{channel_badge} <b>{direction_label}</b>{subject_line} &nbsp;·&nbsp; {ts}"
                    f"</div>"
                    f"<div style='text-align:right; background:#DCF8C6; border-radius:8px;"
                    f" padding:8px 12px; margin:2px 0 6px auto; max-width:80%; display:inline-block; color:#111;'>"
                    f"{_strip_html(m.get('body') or '')}"
                    f"</div>",
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    f"<div style='color:#555 !important; font-size:0.85em'>"
                    f"{channel_badge} <b>{direction_label}</b>{subject_line} &nbsp;·&nbsp; {ts}"
                    f"</div>"
                    f"<div style='background:#F0F0F0; border-radius:8px;"
                    f" padding:8px 12px; margin:2px 0 6px 0; max-width:80%; color:#111;'>"
                    f"{_strip_html(m.get('body') or '')}"
                    f"</div>",
                    unsafe_allow_html=True,
                )
    else:
        st.caption("No messages yet — use 📥 Sync Messages to load history.")

    st.markdown("**Reply**")

    if preferred_channel == "email":
        reply_tab_email, reply_tab_sms = st.tabs(["✉️ Email", "📱 SMS"])
    else:
        reply_tab_sms, reply_tab_email = st.tabs(["📱 SMS", "✉️ Email"])

    with reply_tab_sms:
        sms_body = st.text_area(
            "Message", key=f"sms_body_{k}", label_visibility="collapsed",
            placeholder="Type an SMS...", height=80,
        )
        if st.button("Send SMS", key=f"send_sms_{k}", type="primary"):
            if not sms_body.strip():
                st.warning("Message is empty.")
            elif not phone:
                st.error("No phone number on file for this applicant.")
            else:
                with st.spinner("Sending..."):
                    sid = send_sms(phone, sms_body.strip())
                if sid:
                    insert_message(
                        applicant_id=applicant_id,
                        channel="sms",
                        direction="outbound",
                        body=sms_body.strip(),
                        external_id=sid,
                        sent_at=datetime.now(timezone.utc).isoformat(),
                    )
                    st.success("SMS sent!")
                    st.rerun()
                else:
                    st.error("SMS failed — check logs.")

    with reply_tab_email:
        email_subject = st.text_input(
            "Subject", key=f"email_subj_{k}",
            placeholder="Re: Stretch Practitioner interview",
        )
        email_body = st.text_area(
            "Message", key=f"email_body_{k}", label_visibility="collapsed",
            placeholder="Type an email...", height=80,
        )
        if st.button("Send Email", key=f"send_email_{k}", type="primary"):
            if not email_body.strip():
                st.warning("Message is empty.")
            elif not email:
                st.error("No email address on file for this applicant.")
            else:
                with st.spinner("Sending..."):
                    subj = email_subject.strip() or "Re: Stretch Practitioner"
                    email_id = send_email(email, subj, email_body.strip())
                if email_id:
                    insert_message(
                        applicant_id=applicant_id,
                        channel="email",
                        direction="outbound",
                        body=email_body.strip(),
                        subject=subj,
                        external_id=email_id,
                        sent_at=datetime.now(timezone.utc).isoformat(),
                    )
                    st.success("Email sent!")
                    st.rerun()
                else:
                    st.error("Email failed — check logs.")


# ── Card renderer ─────────────────────────────────────────────────────────────

def render(subset: pd.DataFrame, tab: str = ""):
    if subset.empty:
        st.info("Nothing here yet.")
        return

    for _, r in subset.iterrows():
        score         = int(r.get("score") or 0)
        is_dq         = bool(r.get("auto_disqualified"))
        is_invited    = bool(r.get("sms_sid") or "") or bool(r.get("manually_invited"))
        is_booked     = bool(r.get("calendly_booked"))
        is_1hr        = bool(r.get("one_hr_invited"))
        is_cr_booked  = bool(r.get("scheduled_block_at"))

        star_str = ("AUTO-DQ" if is_dq else "⭐" * score)
        label    = f"{star_str}  {r['name']}  —  {r.get('job_title', '')}"
        if is_invited:  label += "  ✉️"
        if is_1hr:      label += "  🎯"
        if is_booked:   label += "  📅"
        if is_cr_booked: label += "  ✅"

        messages = get_messages_for_applicant(str(r["id"]))
        pref_ch = _preferred_channel_from_messages(messages)
        stale_days = _staleness_days(dict(r), messages)
        if stale_days > 0:
            label += f"  · Day {stale_days}"

        st.markdown(_progress_bar_html(dict(r)), unsafe_allow_html=True)
        with st.expander(label):
            left, right = st.columns([3, 1])

            with left:
                pref_badge = ("  📱 Prefers SMS" if pref_ch == "sms"
                              else "  ✉️ Prefers Email" if pref_ch == "email"
                              else "")
                st.markdown(
                    f"**Email:** {_s(r.get('email')) or '—'}  \n"
                    f"**Phone:** {_s(r.get('phone')) or '—'}{pref_badge}"
                )
                applied = fmt_dt(r.get("created_at"))
                if applied:
                    st.caption(f"Applied {applied}")

                if r.get("reasoning"):
                    st.info(r["reasoning"])

                if r.get("application_text"):
                    with st.expander("Application answers"):
                        st.text(r["application_text"])

                st.markdown("**Interview Notes** *(internal)*")
                notes_val = _s(r.get("interview_notes"))
                new_notes = st.text_area(
                    "notes", value=notes_val,
                    key=f"notes_{tab}_{r['id']}",
                    label_visibility="collapsed",
                    placeholder="Add notes from the 1-hr interview…",
                    height=90,
                )
                if st.button("Save Notes", key=f"save_notes_{tab}_{r['id']}"):
                    db.table("applicants").update({"interview_notes": new_notes}).eq("id", str(r["id"])).execute()
                    st.success("Notes saved.")

            with right:
                if not is_invited:
                    _missing_inv = [c for c, v in [("phone", _s(r.get("phone"))), ("email", _s(r.get("email")))] if not v]
                    if _missing_inv:
                        st.warning(f"Missing contact info: {', '.join(_missing_inv)}")
                    if st.button("Send Invite", key=f"inv_{tab}_{r['id']}", type="primary",
                                 help="Sends SMS + email with Calendly link",
                                 disabled=bool(_missing_inv)):
                        with st.spinner("Sending..."):
                            do_invite(r["id"], r["name"],
                                      _s(r.get("phone")), _s(r.get("email")))
                        st.success("Invite sent!")
                        st.rerun()
                else:
                    st.success("✓ Invited")
                    sent = fmt_dt(r.get("invite_sent_at"))
                    if sent:
                        st.caption(sent)

                st.write("")

                if not is_booked:
                    if st.button("Mark Calendly Booked", key=f"book_{tab}_{r['id']}"):
                        do_mark_booked(r["id"])
                        st.rerun()
                else:
                    st.info("📅 Booked")

                st.write("")

                if not r.get("scheduling_requested_at"):
                    # Step 2: ask candidate to come in for a stretch
                    _missing_str = [c for c, v in [("phone", _s(r.get("phone"))), ("email", _s(r.get("email")))] if not v]
                    if _missing_str:
                        st.warning(f"Missing contact info: {', '.join(_missing_str)}")
                    if st.button("📍 Schedule Stretch", key=f"1hr_btn_{tab}_{r['id']}",
                                 disabled=bool(_missing_str)):
                        st.session_state[f"show_1hr_{r['id']}"] = True
                    if st.session_state.get(f"show_1hr_{r['id']}"):
                        first_name = str(r["name"]).split()[0]
                        default_sms = (
                            "Thanks for taking the time to meet with me. To get you scheduled, "
                            "could you share a couple days and times that work for you? "
                            "Mon-Fri mornings or afternoons work best. Thanks, Duncan"
                        )
                        default_email = (
                            f"{first_name}, thanks for taking the time to meet with me.\n\n"
                            "To get you scheduled for a stretch, would you mind sharing some times "
                            "that work for you? We have morning and afternoon openings Monday through "
                            "Friday. Two or three options works great — I can usually find something "
                            "that lines up.\n\nThanks,\nDuncan Richardson"
                        )
                        if pref_ch == "sms":
                            st.caption("📱 Sending via preferred channel (SMS)")
                            sms_msg = st.text_area(
                                "SMS message", value=default_sms,
                                key=f"1hr_sms_{tab}_{r['id']}", height=100,
                            )
                            also_email = st.checkbox(
                                "Also send email", value=False,
                                key=f"1hr_also_email_{tab}_{r['id']}",
                            )
                            email_msg = st.text_area(
                                "Email message", value=default_email,
                                key=f"1hr_email_{tab}_{r['id']}", height=130,
                            ) if also_email else ""
                            btn_label = "Send Both" if also_email else "Send SMS"
                        elif pref_ch == "email":
                            st.caption("✉️ Sending via preferred channel (Email)")
                            email_msg = st.text_area(
                                "Email message", value=default_email,
                                key=f"1hr_email_{tab}_{r['id']}", height=130,
                            )
                            also_sms = st.checkbox(
                                "Also send SMS", value=False,
                                key=f"1hr_also_sms_{tab}_{r['id']}",
                            )
                            sms_msg = st.text_area(
                                "SMS message", value=default_sms,
                                key=f"1hr_sms_{tab}_{r['id']}", height=100,
                            ) if also_sms else ""
                            btn_label = "Send Both" if also_sms else "Send Email"
                        else:
                            sms_msg = st.text_area(
                                "SMS message", value=default_sms,
                                key=f"1hr_sms_{tab}_{r['id']}", height=100,
                            )
                            email_msg = st.text_area(
                                "Email message", value=default_email,
                                key=f"1hr_email_{tab}_{r['id']}", height=130,
                            )
                            btn_label = "Send Both"
                        c1, c2 = st.columns(2)
                        with c1:
                            if st.button(btn_label, key=f"1hr_send_{tab}_{r['id']}", type="primary"):
                                with st.spinner("Sending..."):
                                    sent_count = do_schedule_stretch(r["id"], _s(r.get("phone")),
                                                                     _s(r.get("email")), sms_msg, email_msg,
                                                                     pref_ch=pref_ch)
                                st.session_state.pop(f"show_1hr_{r['id']}", None)
                                if sent_count:
                                    st.success(f"Availability request sent ({sent_count} channel{'s' if sent_count > 1 else ''})!")
                                else:
                                    st.error("Nothing sent — no valid contact info.")
                                st.rerun()
                        with c2:
                            if st.button("Cancel", key=f"1hr_cancel_{tab}_{r['id']}"):
                                st.session_state.pop(f"show_1hr_{r['id']}", None)
                                st.rerun()
                else:
                    # Stretch scheduling is in progress or done
                    if is_cr_booked:
                        st.success("✅ Stretch booked")
                    elif r.get("scheduling_fallback_sent_at"):
                        st.warning("📨 Scheduling fallback sent")
                    else:
                        st.info("🕐 Waiting for stretch reply")

                st.write("")

                # 1-hr interview — always available
                if not is_1hr:
                    if st.button("🎯 Advance to 1-Hr Interview", key=f"advance_1hr_btn_{tab}_{r['id']}",
                                 type="primary"):
                        with st.spinner("Sending..."):
                            sent_count = do_1hr_interview(r["id"], _s(r.get("phone")),
                                                          _s(r.get("email")), pref_ch=pref_ch)
                        if sent_count:
                            st.success("1-hr interview invite sent!")
                        else:
                            st.error("Nothing sent — no valid contact info.")
                        st.rerun()
                else:
                    st.success("🎯 1-Hr interview invited")
                    sent_1hr = fmt_dt(r.get("one_hr_invite_sent_at"))
                    if sent_1hr:
                        st.caption(sent_1hr)

                st.write("")
                if not r.get("archived"):
                    if st.button("🗑️ Archive", key=f"arch_{tab}_{r['id']}", help="Hide from dashboard; won't re-appear on sync"):
                        db.table("applicants").update({"archived": True}).eq("id", r["id"]).execute()
                        st.rerun()
                else:
                    if st.button("↩️ Unarchive", key=f"unarch_{tab}_{r['id']}"):
                        db.table("applicants").update({"archived": False}).eq("id", r["id"]).execute()
                        st.rerun()

                st.write("")
                if not r.get("archived") and _s(r.get("profile_url")):
                    if st.button("❌ Deactivate in CareerPlug", key=f"dq_btn_{tab}_{r['id']}"):
                        st.session_state[f"show_dq_{r['id']}"] = True
                    if st.session_state.get(f"show_dq_{r['id']}"):
                        dq_reason = st.selectbox(
                            "Rejection reason",
                            DEACTIVATE_REASONS,
                            key=f"dq_reason_{tab}_{r['id']}",
                        )
                        c_dq1, c_dq2 = st.columns(2)
                        with c_dq1:
                            if st.button("Confirm & Archive", key=f"dq_confirm_{tab}_{r['id']}", type="primary"):
                                with st.spinner("Deactivating in CareerPlug..."):
                                    cp_ok, msg = do_deactivate(r["id"], _s(r.get("profile_url")), dq_reason)
                                st.session_state.pop(f"show_dq_{r['id']}", None)
                                level = "success" if cp_ok else "error"
                                st.session_state["flash"] = (level, f"{r['name']}: {msg}")
                                st.rerun()
                        with c_dq2:
                            if st.button("Cancel", key=f"dq_cancel_{tab}_{r['id']}"):
                                st.session_state.pop(f"show_dq_{r['id']}", None)
                                st.rerun()

            st.divider()
            render_conversation(
                applicant_id=str(r["id"]),
                phone=_s(r.get("phone")),
                email=_s(r.get("email")),
                tab=tab,
                sms_sid=_s(r.get("sms_sid")),
                invite_sent_at=_s(r.get("invite_sent_at")),
                preferred_channel=pref_ch,
                prefetched_messages=messages,
            )


# ── Tabs ──────────────────────────────────────────────────────────────────────

tab_all, tab_qual, tab_inv, tab_dq = st.tabs(["All", "Qualified", "Invited", "Auto-DQ"])

with tab_all:
    render(df, "all")

with tab_qual:
    render(df[~df["auto_disqualified"].fillna(False) & (df["score"].fillna(0) >= config.SCORE_NOTIFY_THRESHOLD)], "qual")

with tab_inv:
    render(df[(df["sms_sid"].fillna("") != "") | df["manually_invited"].fillna(False)], "inv")

with tab_dq:
    render(df[df["auto_disqualified"].fillna(False)], "dq")
