"""Notifications: the heart of the services side.

Three kinds of message are sent when something happens on a job:
  - to the office workers: a notice in their inbox (the Jobs page) and an email;
  - to the customer: an email (only when the customer has an email address);
  - a free message written by a person (send_message).
Emails are sent in the background. A failed email is only written to the log: it never stops a job action."""

import os
import sys
import threading

import streamlit as st

from inchars.accounts import send_email, smtp_conf
from inchars.config import CEO, RECEPTIONIST
from inchars.db import conn, df
from inchars.services.history import log_event, now
from inchars.services.rules import can, fetch_job


def usernames_with_role(c, role):
    rows = c.execute("SELECT DISTINCT u.username FROM users u JOIN user_roles r ON r.user_id=u.id WHERE r.role=?", (role,)).fetchall()
    return [r["username"] for r in rows]


def app_link():
    """The web address of the app, if it was set in the Secrets (app_url = "https://...") or the APP_URL setting."""
    try:
        url = st.secrets.get("app_url")
    except Exception:
        url = None
    return url or os.getenv("APP_URL") or ""


def _send_all(cfg, addresses, subject, body):
    for to in addresses:
        try:
            send_email(to, subject, body, cfg)
        except Exception as e:  # noqa: BLE001
            print(f"Job email to {to} failed: {e}", file=sys.stderr)


def email_users(c, users, message):
    """Send the message by email to the staff who have an email address (only when email is set up)."""
    cfg = smtp_conf()
    if not cfg or not users:
        return
    marks = ",".join("?" for _ in users)
    rows = c.execute(f"SELECT email FROM users WHERE username IN ({marks})", tuple(users)).fetchall()
    addresses = sorted({r["email"] for r in rows if r["email"]})
    if not addresses:
        return
    link = app_link()
    body = f"{message}\n\nOpen the Jobs page to see the job." + (f"\n{link}" if link else "") + "\n\n- Inchars PD Group"
    threading.Thread(target=_send_all, args=(cfg, addresses, "Inchars: " + message[:90], body), daemon=True).start()


def notify(c, job_id, users, message):
    """A notice in the inbox of each person, and an email too (never to the person who did the action)."""
    people = sorted(set(users) - {st.session_state.user})
    for u in people:
        c.execute("INSERT INTO job_notices(job_id, to_user, message, created_at) VALUES(?,?,?,?)", (int(job_id), u, message, now()))
    email_users(c, people, message)


def tell_customer(c, job_id, subject, text):
    """An email to the customer about his or her job. Returns 'sent', 'no_email' (the customer has no address) or 'no_smtp'."""
    cfg = smtp_conf()
    if not cfg:
        return "no_smtp"
    r = c.execute("SELECT cu.name, cu.email, j.job_no FROM jobs j JOIN customers cu ON cu.id=j.customer_id WHERE j.id=?", (int(job_id),)).fetchone()
    if not r or not (r["email"] or "").strip():
        return "no_email"
    body = f"Dear {r['name']},\n\n{text}\n\nKind regards,\nInchars PD Group"
    threading.Thread(target=_send_all, args=(cfg, [r["email"].strip()], f"Inchars PD Group - job {r['job_no']}: {subject}", body), daemon=True).start()
    log_event(c, job_id, "Customer emailed", subject)
    return "sent"


def send_message(job_id, to, text):
    """A message written by a person. to = some of 'Customer', 'CEO', 'Receptionist', 'Engineer'; empty = only written in the history.
    Returns (error, info)."""
    text = (text or "").strip()
    if not text:
        return "Write the message.", ""
    with conn() as c:
        j = fetch_job(c, job_id)
        if not j or not can("message", j):
            return "You cannot send a message on this job.", ""
        who = st.session_state.user
        info = ""
        if not to:
            log_event(c, job_id, "Note", text)
            return None, "Saved in the history. Nobody was notified."
        staff = []
        if "CEO" in to:
            staff += usernames_with_role(c, CEO)
        if "Receptionist" in to:
            staff += usernames_with_role(c, RECEPTIONIST)
        if "Engineer" in to and j["assigned_to"]:
            staff.append(j["assigned_to"])
        if staff:
            notify(c, job_id, staff, f"Job {j['job_no']} ({j['customer']}), message from {who}: {text}")
        if "Customer" in to:
            res = tell_customer(c, job_id, "a message for you", text)
            if res == "no_email":
                info = "The customer has no email address, so he or she was NOT told. Add the email address first."
            elif res == "no_smtp":
                info = "Email is not set up, so the customer was NOT told."
        log_event(c, job_id, "Message to " + ", ".join(to), text)
    return None, info


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


def email_state():
    """A short line telling this person whether the notifications also reach his or her email."""
    if not smtp_conf():
        return "Email is not set up yet, so notifications appear here only."
    r = df("SELECT email FROM users WHERE username=?", (st.session_state.user,))
    if r.empty or not r.email.iloc[0]:
        return "Add your email address to get these notifications by email too."
    return f"These notifications are also sent to {r.email.iloc[0]}."
