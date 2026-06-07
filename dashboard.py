"""Stretch Zone 1082 — Hiring Dashboard"""
import streamlit as st
import pandas as pd
from datetime import datetime, timezone

import config
from db.supabase_logger import _client as db
from notifications.sms_sender import send_interview_invite
from notifications.email_sender import send_outreach_email

st.set_page_config(page_title="SZ 1082 Hiring", layout="wide", page_icon="💪")

# ── Data ──────────────────────────────────────────────────────────────────────

def load() -> pd.DataFrame:
    resp = db.table("applicants").select("*").order("created_at", desc=True).execute()
    if not resp.data:
        return pd.DataFrame()
    df = pd.DataFrame(resp.data)
    df["created_at"] = pd.to_datetime(df["created_at"], utc=True).dt.tz_convert("US/Mountain")
    for col in ["score", "auto_disqualified", "manually_invited", "calendly_booked", "sms_sid", "invite_sent_at"]:
        if col not in df.columns:
            df[col] = None
    return df

def do_invite(row_id: str, name: str, phone: str, email: str):
    sms_sid = send_interview_invite(candidate_name=name, candidate_phone=phone)
    send_outreach_email(candidate_name=name, candidate_email=email)
    db.table("applicants").update({
        "manually_invited": True,
        "invite_sent_at": datetime.now(timezone.utc).isoformat(),
        "sms_sid": sms_sid or "",
    }).eq("id", row_id).execute()

def do_mark_booked(row_id: str):
    db.table("applicants").update({"calendly_booked": True}).eq("id", row_id).execute()

# ── Header ────────────────────────────────────────────────────────────────────

st.title("Stretch Zone 1082 — Hiring")

col_refresh, _ = st.columns([1, 8])
with col_refresh:
    if st.button("🔄 Refresh"):
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

# ── Card renderer ─────────────────────────────────────────────────────────────

def fmt_dt(ts) -> str:
    if ts is None or (isinstance(ts, float) and pd.isna(ts)):
        return ""
    try:
        dt = pd.to_datetime(ts, utc=True).tz_convert("US/Mountain")
        return dt.strftime("%b %d, %Y %I:%M %p MT")
    except Exception:
        return str(ts)

def render(subset: pd.DataFrame, tab: str = ""):
    if subset.empty:
        st.info("Nothing here yet.")
        return

    for _, r in subset.iterrows():
        score      = int(r.get("score") or 0)
        is_dq      = bool(r.get("auto_disqualified"))
        is_invited = bool(r.get("sms_sid") or "") or bool(r.get("manually_invited"))
        is_booked  = bool(r.get("calendly_booked"))

        star_str = ("AUTO-DQ" if is_dq else "⭐" * score)
        label    = f"{star_str}  {r['name']}  —  {r.get('job_title', '')}"
        if is_invited: label += "  ✉️"
        if is_booked:  label += "  📅"

        with st.expander(label):
            left, right = st.columns([3, 1])

            with left:
                st.markdown(
                    f"**Email:** {r.get('email') or '—'}  \n"
                    f"**Phone:** {r.get('phone') or '—'}"
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
                    if st.button("Send Invite", key=f"inv_{tab}_{r['id']}", type="primary",
                                 help="Sends SMS + email with Calendly link"):
                        with st.spinner("Sending..."):
                            do_invite(r["id"], r["name"],
                                      r.get("phone") or "", r.get("email") or "")
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
