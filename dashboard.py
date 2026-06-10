"""Stretch Zone 1082 — Hiring Dashboard"""
import streamlit as st
import pandas as pd
from datetime import datetime, timezone

import re
import config
from db.supabase_logger import _client as db

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
from sync.sms_sync import sync_all as sync_sms
from sync.email_sync import sync_all as sync_email

st.set_page_config(page_title="SZ 1082 Hiring", layout="wide", page_icon="💪")

# ── Data ──────────────────────────────────────────────────────────────────────

def load() -> pd.DataFrame:
    resp = db.table("applicants").select("*").order("created_at", desc=True).execute()
    if not resp.data:
        return pd.DataFrame()
    df = pd.DataFrame(resp.data)
    df["created_at"] = pd.to_datetime(df["created_at"], utc=True).dt.tz_convert("US/Mountain")
    for col in ["score", "auto_disqualified", "manually_invited", "calendly_booked", "sms_sid", "invite_sent_at",
                "one_hr_invited", "one_hr_invite_sent_at"]:
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
    email_subj = "Next step — Stretch Practitioner interview (Stretch Zone 1082)"
    if email:
        email_id = send_email(email, email_subj, email_body)
        if email_id:
            insert_message(applicant_id=row_id, channel="email", direction="outbound",
                           body=email_body, subject=email_subj,
                           external_id=email_id, sent_at=now)

def do_mark_booked(row_id: str):
    db.table("applicants").update({"calendly_booked": True}).eq("id", row_id).execute()

def do_1hr_invite(row_id: str, phone: str, email: str, sms_body: str, email_body: str) -> int:
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
        email_id = send_email(email, "1-Hour Interview — Stretch Zone 1082", email_body.strip())
        if email_id:
            _ins(applicant_id=row_id, channel="email", direction="outbound",
                 body=email_body.strip(), subject="1-Hour Interview — Stretch Zone 1082",
                 external_id=email_id, sent_at=now)
            sent += 1
    if sent:
        db.table("applicants").update({
            "one_hr_invited": True,
            "one_hr_invite_sent_at": now,
        }).eq("id", row_id).execute()
    return sent

# ── Header ────────────────────────────────────────────────────────────────────

st.title("Stretch Zone 1082 — Hiring")

col_refresh, col_sync, _ = st.columns([1, 2, 6])
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

df = load()

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


def render_conversation(applicant_id: str, phone: str, email: str, tab: str = "",
                        sms_sid: str = "", invite_sent_at: str = ""):
    """Render the threaded message history and reply controls for one applicant."""
    k = f"{tab}_{applicant_id}"  # unique key prefix per tab+applicant
    messages = get_messages_for_applicant(applicant_id)

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

        star_str = ("AUTO-DQ" if is_dq else "⭐" * score)
        label    = f"{star_str}  {r['name']}  —  {r.get('job_title', '')}"
        if is_invited: label += "  ✉️"
        if is_1hr:     label += "  🎯"
        if is_booked:  label += "  📅"

        with st.expander(label):
            left, right = st.columns([3, 1])

            with left:
                st.markdown(
                    f"**Email:** {_s(r.get('email')) or '—'}  \n"
                    f"**Phone:** {_s(r.get('phone')) or '—'}"
                )
                applied = fmt_dt(r.get("created_at"))
                if applied:
                    st.caption(f"Applied {applied}")

                if r.get("reasoning"):
                    st.info(r["reasoning"])

                if r.get("application_text"):
                    with st.expander("Application answers"):
                        st.text(r["application_text"])

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

                if not is_1hr:
                    _missing_1hr = [c for c, v in [("phone", _s(r.get("phone"))), ("email", _s(r.get("email")))] if not v]
                    if _missing_1hr:
                        st.warning(f"Missing contact info: {', '.join(_missing_1hr)}")
                    if st.button("🎯 Advance to 1-Hr Interview", key=f"1hr_btn_{tab}_{r['id']}",
                                 disabled=bool(_missing_1hr)):
                        st.session_state[f"show_1hr_{r['id']}"] = True
                    if st.session_state.get(f"show_1hr_{r['id']}"):
                        first_name = str(r["name"]).split()[0]
                        default_sms = (
                            f"Hi {first_name}, I'd love to have you in for a 1-hour interview! "
                            f"Here's a link to grab a time: {config.CALENDLY_LINK_1HR}"
                        )
                        default_email = (
                            f"{first_name}, thank you for the initial conversation — "
                            f"I'd like to move forward with a 1-hour in-person interview.\n\n"
                            f"You can book a time here: {config.CALENDLY_LINK_1HR}\n\n"
                            f"Thanks,\nDuncan Richardson"
                        )
                        sms_msg = st.text_area(
                            "SMS message", value=default_sms,
                            key=f"1hr_sms_{tab}_{r['id']}", height=100,
                        )
                        email_msg = st.text_area(
                            "Email message", value=default_email,
                            key=f"1hr_email_{tab}_{r['id']}", height=130,
                        )
                        c1, c2 = st.columns(2)
                        with c1:
                            if st.button("Send Both", key=f"1hr_send_{tab}_{r['id']}", type="primary"):
                                with st.spinner("Sending..."):
                                    sent_count = do_1hr_invite(r["id"], _s(r.get("phone")),
                                                               _s(r.get("email")), sms_msg, email_msg)
                                st.session_state.pop(f"show_1hr_{r['id']}", None)
                                if sent_count:
                                    st.success(f"1-hr invite sent ({sent_count} channel{'s' if sent_count > 1 else ''})!")
                                else:
                                    st.error("Nothing sent — no valid contact info.")
                                st.rerun()
                        with c2:
                            if st.button("Cancel", key=f"1hr_cancel_{tab}_{r['id']}"):
                                st.session_state.pop(f"show_1hr_{r['id']}", None)
                                st.rerun()
                else:
                    st.success("🎯 1-Hr Invited")
                    sent_1hr = fmt_dt(r.get("one_hr_invite_sent_at"))
                    if sent_1hr:
                        st.caption(sent_1hr)

            st.divider()
            render_conversation(
                applicant_id=str(r["id"]),
                phone=_s(r.get("phone")),
                email=_s(r.get("email")),
                tab=tab,
                sms_sid=_s(r.get("sms_sid")),
                invite_sent_at=_s(r.get("invite_sent_at")),
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
