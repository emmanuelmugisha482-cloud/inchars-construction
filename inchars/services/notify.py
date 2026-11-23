"""Telling people, by email (free) and in the app. Staff get a notice in their Inbox and an email (when they have an address on their account).
The customer gets an email only at three moments: the job is registered, we need something from him or her, and the work is ready.

An email is queued while the job is being saved and sent only AFTER the save succeeded (see after_commit), in the background.
Whatever happens is written in the job's History: "Email sent", "Email failed" (with the real reason), or "Email not sent" (no address, or email is not
set up). When an email to a customer fails, the Receptionist gets a notice to call the customer. An email problem never stops a job action."""

import sys
import threading
import traceback
from functools import wraps

import pandas as pd
import streamlit as st

from inchars.accounts import send_email, smtp_conf
from inchars.components.common import wide
from inchars.config import CEO, RECEPTIONIST
from inchars.db import conn, df
from inchars.services.config import EMAILS
from inchars.services.core import first_name, load_jobs, log_event, now
from inchars.services.ui import run_section


# ---------------------------------------------------------------- inside the app (staff)
def usernames_with_role(c, role):
    rows = c.execute("SELECT DISTINCT u.username FROM users u JOIN user_roles r ON r.user_id=u.id WHERE r.role=?", (role,)).fetchall()
    return [r["username"] for r in rows]


def notify(c, job_id, users, message):
    """A notice in the Inbox of each person, and an email to those who have an address (never to the person who did the action)."""
    people = sorted(set(u for u in users if u) - {st.session_state.user})
    for u in people:
        c.execute("INSERT INTO job_notices(job_id, to_user, message, created_at) VALUES(?,?,?,?)", (int(job_id), u, message, now()))
    if people and smtp_conf():
        job_no = c.execute("SELECT job_no FROM jobs WHERE id=?", (int(job_id),)).fetchone()["job_no"]
        marks = ",".join("?" * len(people))
        for r in c.execute(f"SELECT username, email FROM users WHERE username IN ({marks})", tuple(people)).fetchall():
            if (r["email"] or "").strip():
                st.session_state.setdefault("_mail_out", []).append(
                    {"job_id": int(job_id), "to": r["email"].strip(), "subject": f"Inchars PD Group - job {job_no}",
                     "body": f"Hello {r['username']},\n\n{message}\n\nOpen the app to continue.\n\nInchars PD Group", "customer": False, "label": ""})


def load_notices(limit=40):
    return df("""SELECT n.id, n.job_id, j.job_no AS "Job", n.message AS "Message", n.created_at AS "When", n.read_at
                 FROM job_notices n LEFT JOIN jobs j ON j.id=n.job_id WHERE n.to_user=? ORDER BY n.id DESC LIMIT ?""",
              (st.session_state.user, int(limit)))


def unread_count():
    r = df("SELECT COUNT(*) AS n FROM job_notices WHERE to_user=? AND read_at IS NULL", (st.session_state.user,))
    return int(r.n.iloc[0])


def mark_notices_read():
    with conn() as c:
        c.execute("UPDATE job_notices SET read_at=? WHERE to_user=? AND read_at IS NULL", (now(), st.session_state.user))


# ---------------------------------------------------------------- email to the customer
def tell_customer(c, job_id, key, **fields):
    """Queue the email called `key` (see EMAILS in config.py) to the customer of this job. Returns 'queued', 'no_email' or 'not_set_up'."""
    r = c.execute("SELECT cu.name, cu.email, j.job_no FROM jobs j JOIN customers cu ON cu.id=j.customer_id WHERE j.id=?", (int(job_id),)).fetchone()
    subject, text = EMAILS[key]
    label = subject.split(" (")[0]
    to = ((r["email"] if r else "") or "").strip()
    if not to:
        log_event(c, job_id, "Email not sent", f"{label}: the customer has no email address.")
        return "no_email"
    if not smtp_conf():
        log_event(c, job_id, "Email not sent", f"{label}: email is not set up yet.")
        return "not_set_up"
    fields = dict(fields, job_no=r["job_no"], name=first_name(r["name"]))
    st.session_state.setdefault("_mail_out", []).append(
        {"job_id": int(job_id), "to": to, "subject": subject.format(**fields), "body": text.format(**fields), "customer": True, "label": label})
    return "queued"


def _send_all(cfg, items):
    """Background: send each email, then write what happened in the history. A failure to reach a customer also tells the Receptionist."""
    for m in items:
        try:
            send_email(m["to"], m["subject"], m["body"], cfg)
            ok, why = True, ""
        except Exception as e:  # noqa: BLE001
            print(f"Email to {m['to']} failed: {e}", file=sys.stderr)
            ok, why = False, f"{type(e).__name__}: {e}"[:300]
        if not m["customer"]:
            continue
        try:
            with conn() as c:
                log_event(c, m["job_id"], "Email sent" if ok else "Email failed", f"{m['label']} (to {m['to']})" + ("" if ok else f": {why}"), by="system")
                if not ok:
                    no = c.execute("SELECT job_no FROM jobs WHERE id=?", (m["job_id"],)).fetchone()["job_no"]
                    for u in usernames_with_role(c, RECEPTIONIST):
                        c.execute("INSERT INTO job_notices(job_id, to_user, message, created_at) VALUES(?,?,?,?)",
                                  (m["job_id"], u, f"The email to the customer of job {no} could NOT be sent ({m['label']}). Please call the customer.", now()))
        except Exception:  # noqa: BLE001
            traceback.print_exc()


def flush_mail():
    items = st.session_state.pop("_mail_out", None) or []
    if items:
        threading.Thread(target=_send_all, args=(smtp_conf(), items), daemon=True).start()


def after_commit(fn):
    """Put on every function that saves a job and may write to people: the emails leave only after the save worked."""
    @wraps(fn)
    def run(*a, **k):
        st.session_state["_mail_out"] = []
        try:
            out = fn(*a, **k)
        except BaseException:
            st.session_state.pop("_mail_out", None)
            raise
        flush_mail()
        return out
    return run


def mail_state():
    """A short line for the Jobs page: do the emails work?"""
    return "Emails to customers are ON." if smtp_conf() else "Email is not set up yet: customers are NOT told. Ask the CEO to open System check."


# ---------------------------------------------------------------- the Inbox tab
def inbox_tab():
    st.caption(mail_state())
    n = load_notices()
    if n.empty:
        return st.caption("No notifications yet.")
    unread = n.read_at.isna()
    shown = n.assign(New=["new" if u else "" for u in unread])[["New", "When", "Job", "Message"]]
    shown["When"] = shown["When"].astype(str).str.replace("T", " ")
    st.caption("Tap a notification to open its job.")
    try:
        ev = wide(st.dataframe, shown, hide_index=True, on_select="rerun", selection_mode="single-row", key="inbox_tbl")
        rows = [r for r in (ev.selection.rows if ev is not None else []) if r < len(n)]
    except TypeError:
        wide(st.dataframe, shown, hide_index=True)
        rows = []
    tapped = int(n.job_id.iloc[rows[0]]) if rows and pd.notna(n.job_id.iloc[rows[0]]) else None
    if tapped != st.session_state.get("ib_last_tap"):  # only a NEW tap opens a job (Close must keep working)
        st.session_state.ib_last_tap = tapped
        if tapped is not None:
            st.session_state.ib_open = tapped
    if unread.any() and st.button("Mark all as read", key="mark_read"):
        mark_notices_read()
        st.rerun()
    jid = st.session_state.get("ib_open")
    if jid and jid in set(load_jobs().id.astype(int)):
        run_section("panel", "job_panel", jid, "ib")
