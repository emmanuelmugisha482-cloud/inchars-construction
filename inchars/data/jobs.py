"""Services: customers, jobs, payments, history and notifications - read and write.

The rules (who may do what, and when) are in can() and are checked again by every function that changes a job, so the screens
only decide what to show. Money is in Frw. A job's balance is its fee minus the payments recorded."""

import os
import sys
import threading
from datetime import date, datetime

import pandas as pd
import streamlit as st

from inchars.accounts import has, send_email, smtp_conf
from inchars.config import (ACCOUNTANT, CEO, ENGINEER, JOB_STATUSES, OPEN_STATUSES, PAY_METHODS, PERMIT, PERMIT_STAGES,
                            RECEPTIONIST, SERVICES)
from inchars.db import IntegrityErrors, conn, df
from inchars.storage import DOCUMENT_TYPES, PDF_ONLY, delete_file, list_files, save_file


def now():
    return datetime.now().isoformat(timespec="seconds")


# ---------- who may do what ----------
def sees_all_jobs():
    """The CEO, Accountant and Receptionist see every job. An Engineer sees the jobs given to him or her."""
    return has(CEO, ACCOUNTANT, RECEPTIONIST)


def can(action, job, n_deliverables=0):
    """True when the signed-in person may do `action` on this job (a row of load_jobs)."""
    me = st.session_state.user
    s = job["status"]
    mine = job["assigned_to"] == me
    eng = has(ENGINEER) and mine
    if action == "assign":
        return has(CEO) and s in OPEN_STATUSES
    if action == "start":
        return eng and s == "Assigned"
    if action == "wait":
        return (eng or has(CEO)) and s == "In progress"
    if action == "resume":
        return (eng or has(CEO, RECEPTIONIST)) and s == "Waiting for customer"
    if action == "ready":
        return (eng or has(CEO)) and s in ("In progress", "Waiting for customer") and n_deliverables > 0
    if action == "collect":
        return has(CEO, RECEPTIONIST) and s == "Ready"
    if action == "cancel":
        return (has(CEO) and s in OPEN_STATUSES) or (has(RECEPTIONIST) and s == "New")
    if action == "reopen":
        return has(CEO) and s in ("Collected", "Cancelled")
    if action == "stage":
        return job["service"] == PERMIT and (eng or has(CEO, RECEPTIONIST)) and s in ("Assigned", "In progress", "Waiting for customer")
    if action == "edit":
        return has(CEO, RECEPTIONIST) and s in OPEN_STATUSES
    if action == "pay":
        return has(CEO, ACCOUNTANT, RECEPTIONIST) and s != "Cancelled"
    if action == "upload_customer":
        return (has(CEO, RECEPTIONIST) or eng) and s in OPEN_STATUSES
    if action == "upload_deliverable":
        return (eng or has(CEO)) and s in ("Assigned", "In progress", "Waiting for customer", "Ready")
    if action == "see_files":
        return has(CEO, RECEPTIONIST) or eng
    if action == "note":
        return has(CEO, RECEPTIONIST, ACCOUNTANT) or eng
    return False


def can_open_deliverable(job):
    """The finished document is handed out only when the job is paid in full. The CEO and the assigned engineer can always open it."""
    if has(CEO) or (has(ENGINEER) and job["assigned_to"] == st.session_state.user):
        return True
    return has(RECEPTIONIST) and job["status"] in ("Ready", "Collected") and float(job["balance"]) <= 0


# ---------- history and notices ----------
def usernames_with_role(c, role):
    rows = c.execute("SELECT DISTINCT u.username FROM users u JOIN user_roles r ON r.user_id=u.id WHERE r.role=?", (role,)).fetchall()
    return [r["username"] for r in rows]


def log_event(c, job_id, event, detail=""):
    c.execute("INSERT INTO job_events(job_id, happened_at, by_user, event, detail) VALUES(?,?,?,?,?)",
              (int(job_id), now(), st.session_state.user, event, detail))


def app_link():
    """The web address of the app, if it was set in the Secrets (app_url = "https://...") or the APP_URL setting."""
    try:
        url = st.secrets.get("app_url")
    except Exception:
        url = None
    return url or os.getenv("APP_URL") or ""


def _send_all(cfg, addresses, subject, body):
    """Runs in the background. A failed email is only written to the log: it never stops a job action."""
    for to in addresses:
        try:
            send_email(to, subject, body, cfg)
        except Exception as e:  # noqa: BLE001
            print(f"Job email to {to} failed: {e}", file=sys.stderr)


def email_users(c, users, message):
    """Also send the message by email to the people who have an email address (only when email is set up)."""
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
    """A message in the inbox of each person, and by email too (never to the person who did the action)."""
    people = sorted(set(users) - {st.session_state.user})
    for u in people:
        c.execute("INSERT INTO job_notices(job_id, to_user, message, created_at) VALUES(?,?,?,?)", (int(job_id), u, message, now()))
    email_users(c, people, message)


def email_state():
    """For the Jobs page: a short line telling this person whether the notifications also reach his or her email."""
    if not smtp_conf():
        return "Email is not set up yet, so notifications appear here only."
    r = df("SELECT email FROM users WHERE username=?", (st.session_state.user,))
    if r.empty or not r.email.iloc[0]:
        return "Add your email address to get these notifications by email too."
    return f"These notifications are also sent to {r.email.iloc[0]}."


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


def load_events(job_id):
    return df('SELECT happened_at AS "When", by_user AS "By", event AS "What", detail AS "Detail" FROM job_events WHERE job_id=? ORDER BY id DESC', (int(job_id),))


# ---------- customers ----------
def load_customers():
    return df("""SELECT c.id, c.name AS "Name", c.phone AS "Phone", c.email AS "Email", c.note AS "Note",
                 (SELECT COUNT(*) FROM jobs j WHERE j.customer_id=c.id) AS "Jobs" FROM customers c ORDER BY c.name""")


def _digits(v):
    return "".join(ch for ch in str(v or "") if ch.isdigit())


def save_customer(name, phone, email, note, customer_id=None):
    """Add or change a customer. Returns (customer_id, error)."""
    name, phone = (name or "").strip(), (phone or "").strip()
    if not name:
        return None, "Write the customer's name."
    if len(_digits(phone)) < 7:
        return None, "Write the customer's phone number (at least 7 digits)."
    with conn() as c:
        for r in c.execute("SELECT id, name, phone FROM customers").fetchall():
            if _digits(r["phone"]) == _digits(phone) and r["id"] != customer_id:
                return None, f"This phone number already belongs to {r['name']}. Choose that customer instead."
        if customer_id:
            c.execute("UPDATE customers SET name=?, phone=?, email=?, note=? WHERE id=?", (name, phone, (email or "").strip(), (note or "").strip(), int(customer_id)))
            return int(customer_id), None
        cid = c.execute("INSERT INTO customers(name, phone, email, note, created_at, created_by) VALUES(?,?,?,?,?,?) RETURNING id",
                        (name, phone, (email or "").strip(), (note or "").strip(), now(), st.session_state.user)).fetchone()["id"]
    return int(cid), None


# ---------- jobs ----------
def load_jobs():
    """Every job the signed-in person may see, with the customer and the money (fee, paid, balance)."""
    j = df("""SELECT j.id, j.job_no, j.created_at, j.service, j.title, j.description, j.status, j.stage, j.gov_ref, j.fee, j.due_on,
              j.assigned_to, j.created_by, j.ready_at, j.collected_at, j.collected_by, c.id AS customer_id, c.name AS customer, c.phone AS phone,
              COALESCE((SELECT SUM(p.amount) FROM job_payments p WHERE p.job_id=j.id), 0) AS paid
              FROM jobs j JOIN customers c ON c.id=j.customer_id ORDER BY j.id DESC""")
    if not sees_all_jobs():
        j = j[j.assigned_to == st.session_state.user]
    j = j.reset_index(drop=True)
    for col in ("description", "stage", "gov_ref", "due_on", "assigned_to", "ready_at", "collected_at", "collected_by"):
        j[col] = j[col].fillna("")  # empty text instead of None, so the screens can test it simply
    j["fee"] = pd.to_numeric(j.fee).fillna(0)
    j["paid"] = pd.to_numeric(j.paid).fillna(0)
    j["balance"] = j.fee - j.paid
    today = str(date.today())
    j["overdue"] = [bool(d) and d < today and s in OPEN_STATUSES and s != "Ready" for d, s in zip(j.due_on.fillna(""), j.status)]
    return j


def load_engineers():
    return df("SELECT DISTINCT u.username FROM users u JOIN user_roles r ON r.user_id=u.id WHERE r.role=? ORDER BY u.username", (ENGINEER,)).username.tolist()


def _next_job_no(c):
    year = date.today().year
    n = c.execute("SELECT COUNT(*) AS n FROM jobs WHERE job_no LIKE ?", (f"INC-{year}-%",)).fetchone()["n"] + 1
    return f"INC-{year}-{n:04d}"


def create_job(customer_id, service, title, description, fee, due_on, deposit=0, method="Cash", reference="", files=()):
    """Register a job. files = [(filename, bytes)] received from the customer. Returns (job_no, error)."""
    if not has(CEO, RECEPTIONIST):
        return None, "Only the Receptionist or the CEO can register a job."
    if service not in SERVICES:
        return None, "Choose the service."
    if not (title or "").strip():
        return None, "Write a short title for the job."
    fee, deposit = int(fee or 0), int(deposit or 0)
    if fee < 0 or deposit < 0:
        return None, "Amounts cannot be negative."
    if deposit > fee:
        return None, "The deposit is more than the fee."
    me = st.session_state.user
    job_no = None
    for _ in range(4):
        try:
            with conn() as c:
                job_no = _next_job_no(c)
                cust = c.execute("SELECT name FROM customers WHERE id=?", (int(customer_id),)).fetchone()
                if not cust:
                    return None, "Choose the customer."
                jid = c.execute("""INSERT INTO jobs(job_no, customer_id, service, title, description, status, fee, due_on, created_by, created_at)
                                   VALUES(?,?,?,?,?,?,?,?,?,?) RETURNING id""",
                                (job_no, int(customer_id), service, title.strip(), (description or "").strip(), "New", fee, str(due_on) if due_on else None, me, now())).fetchone()["id"]
                log_event(c, jid, "Registered", f"{service} for {cust['name']}. Fee {fee:,} Frw.")
                if deposit:
                    c.execute("INSERT INTO job_payments(job_id, amount, method, reference, paid_on, received_by, note, created_at) VALUES(?,?,?,?,?,?,?,?)",
                              (jid, deposit, method, (reference or "").strip(), str(date.today()), me, "Deposit", now()))
                    log_event(c, jid, "Payment", f"Deposit {deposit:,} Frw ({method}).")
                notify(c, jid, usernames_with_role(c, CEO), f"New job {job_no}: {service} for {cust['name']}. It needs an engineer.")
            break
        except IntegrityErrors:
            job_no = None  # the number was taken at the same moment: try the next one
    if not job_no:
        return None, "Could not create the job number. Please try again."
    jid = int(df("SELECT id FROM jobs WHERE job_no=?", (job_no,)).id.iloc[0])
    msgs = [m for m in (save_file(jid, "Customer document", n, b, me) for n, b in files) if m]
    return job_no, ("The job was saved, but: " + " ".join(msgs)) if msgs else None


def _job(c, job_id):
    r = c.execute("""SELECT j.*, COALESCE((SELECT SUM(p.amount) FROM job_payments p WHERE p.job_id=j.id), 0) AS paid, cu.name AS customer
                     FROM jobs j JOIN customers cu ON cu.id=j.customer_id WHERE j.id=?""", (int(job_id),)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["balance"] = float(d["fee"] or 0) - float(d["paid"] or 0)
    return d


def _n_deliverables(job_id):
    f = list_files(job_id)
    return int((f.kind == "Deliverable").sum()) if len(f) else 0


def assign_job(job_id, engineer, due_on):
    me = st.session_state.user
    with conn() as c:
        j = _job(c, job_id)
        if not j or not can("assign", j):
            return "You cannot assign this job."
        if engineer not in [r["username"] for r in c.execute("SELECT DISTINCT u.username FROM users u JOIN user_roles r ON r.user_id=u.id WHERE r.role=?", (ENGINEER,)).fetchall()]:
            return "Choose an engineer."
        old = j["assigned_to"]
        status = "Assigned" if j["status"] == "New" else j["status"]
        c.execute("UPDATE jobs SET assigned_to=?, due_on=?, status=? WHERE id=?", (engineer, str(due_on) if due_on else j["due_on"], status, int(job_id)))
        log_event(c, job_id, "Assigned" if not old else "Reassigned", f"{engineer}" + (f" (was {old})" if old and old != engineer else "") + (f", due {due_on}" if due_on else ""))
        notify(c, job_id, [engineer], f"Job {j['job_no']} was given to you: {j['service']} for {j['customer']}" + (f". Due {due_on}." if due_on else "."))
        if old and old != engineer:
            notify(c, job_id, [old], f"Job {j['job_no']} was given to {engineer}. It is no longer yours.")
    return None


def change_status(job_id, action, note="", override=False):
    """start, wait, resume, ready, collect, cancel, reopen. Returns an error text, or None when done."""
    me = st.session_state.user
    n_del = _n_deliverables(job_id) if action == "ready" else 0
    with conn() as c:
        j = _job(c, job_id)
        if not j:
            return "This job no longer exists."
        if not can(action, j, n_del if action != "ready" else 1):
            return "You cannot do this on this job now."
        if action == "ready" and n_del == 0:
            return "Upload the finished PDF first, then mark the job ready."
        note = (note or "").strip()
        no, cust = j["job_no"], j["customer"]
        ceos, recs = usernames_with_role(c, CEO), usernames_with_role(c, RECEPTIONIST)
        if action == "start":
            c.execute("UPDATE jobs SET status='In progress' WHERE id=?", (int(job_id),))
            log_event(c, job_id, "Started work")
        elif action == "wait":
            if not note:
                return "Write what you need from the customer."
            c.execute("UPDATE jobs SET status='Waiting for customer' WHERE id=?", (int(job_id),))
            log_event(c, job_id, "Waiting for customer", note)
            notify(c, job_id, recs + ceos, f"Job {no} ({cust}) is waiting for the customer: {note}")
        elif action == "resume":
            c.execute("UPDATE jobs SET status='In progress' WHERE id=?", (int(job_id),))
            log_event(c, job_id, "Work continues", note or "The customer answered.")
            if j["assigned_to"]:
                notify(c, job_id, [j["assigned_to"]], f"Job {no} ({cust}): work can continue." + (f" {note}" if note else ""))
        elif action == "ready":
            c.execute("UPDATE jobs SET status='Ready', ready_at=? WHERE id=?", (now(), int(job_id)))
            paid = "paid in full" if j["balance"] <= 0 else f"balance to pay: {j['balance']:,.0f} Frw"
            log_event(c, job_id, "Ready", note or "The finished document was uploaded.")
            notify(c, job_id, ceos + recs, f"Job {no} ({cust}) is READY for the customer - {paid}.")
        elif action == "collect":
            if j["balance"] > 0 and not (has(CEO) and override):
                return f"The job is not paid in full: {j['balance']:,.0f} Frw is still to pay."
            if j["balance"] > 0 and not note:
                return "Write the reason for releasing the document without full payment."
            c.execute("UPDATE jobs SET status='Collected', collected_at=?, collected_by=? WHERE id=?", (now(), me, int(job_id)))
            log_event(c, job_id, "Collected by the customer", ("Released with " + f"{j['balance']:,.0f} Frw unpaid: " + note) if j["balance"] > 0 else (note or "Paid in full."))
            notify(c, job_id, ceos, f"Job {no} ({cust}) was collected by the customer.")
        elif action == "cancel":
            if not note:
                return "Write the reason for cancelling."
            c.execute("UPDATE jobs SET status='Cancelled' WHERE id=?", (int(job_id),))
            log_event(c, job_id, "Cancelled", note)
            notify(c, job_id, ceos + ([j["assigned_to"]] if j["assigned_to"] else []), f"Job {no} ({cust}) was cancelled: {note}")
        elif action == "reopen":
            if not note:
                return "Write why the job is reopened."
            c.execute("UPDATE jobs SET status=?, collected_at=NULL, collected_by=NULL WHERE id=?", ("In progress" if j["assigned_to"] else "New", int(job_id)))
            log_event(c, job_id, "Reopened", note)
            if j["assigned_to"]:
                notify(c, job_id, [j["assigned_to"]], f"Job {no} ({cust}) was reopened: {note}")
    return None


def set_stage(job_id, stage, gov_ref=""):
    """Building permit jobs: where the file is at the government office."""
    with conn() as c:
        j = _job(c, job_id)
        if not j or not can("stage", j):
            return "You cannot change the stage of this job."
        if stage not in PERMIT_STAGES:
            return "Choose the stage."
        c.execute("UPDATE jobs SET stage=?, gov_ref=? WHERE id=?", (stage, (gov_ref or "").strip() or j["gov_ref"], int(job_id)))
        log_event(c, job_id, f"Permit stage: {stage}", (f"Reference {gov_ref.strip()}" if (gov_ref or "").strip() else ""))
        if stage in ("Approved", "Corrections requested"):
            notify(c, job_id, usernames_with_role(c, CEO) + usernames_with_role(c, RECEPTIONIST), f"Job {j['job_no']} ({j['customer']}): permit stage is now {stage}.")
    return None


def edit_job(job_id, title, description, fee, due_on):
    with conn() as c:
        j = _job(c, job_id)
        if not j or not can("edit", j):
            return "You cannot change this job."
        fee = int(fee or 0)
        if not (title or "").strip():
            return "Write a short title for the job."
        if fee < 0:
            return "The fee cannot be negative."
        if fee < float(j["paid"] or 0):
            return f"The fee cannot be less than what the customer already paid ({float(j['paid']):,.0f} Frw)."
        c.execute("UPDATE jobs SET title=?, description=?, fee=?, due_on=? WHERE id=?",
                  (title.strip(), (description or "").strip(), fee, str(due_on) if due_on else None, int(job_id)))
        if fee != int(j["fee"] or 0):
            log_event(c, job_id, "Fee changed", f"{int(j['fee'] or 0):,} to {fee:,} Frw")
        else:
            log_event(c, job_id, "Details changed")
    return None


def add_note(job_id, text):
    with conn() as c:
        j = _job(c, job_id)
        if not j or not can("note", j):
            return "You cannot write on this job."
        if not (text or "").strip():
            return "Write the note."
        log_event(c, job_id, "Note", text.strip())
    return None


# ---------- payments ----------
def load_payments(job_id=None):
    sql = """SELECT p.id, p.job_id, j.job_no, c.name AS customer, p.paid_on AS date, p.amount, p.method, p.reference, p.received_by, p.note
             FROM job_payments p JOIN jobs j ON j.id=p.job_id JOIN customers c ON c.id=j.customer_id"""
    out = df(sql + (" WHERE p.job_id=?" if job_id else "") + " ORDER BY p.paid_on DESC, p.id DESC", (int(job_id),) if job_id else ())
    if job_id is None and not sees_all_jobs():
        mine = set(load_jobs().id.astype(int))
        out = out[out.job_id.astype(int).isin(mine)]
    return out.reset_index(drop=True)


def add_payment(job_id, amount, method, reference, paid_on):
    with conn() as c:
        j = _job(c, job_id)
        if not j or not can("pay", j):
            return "You cannot record a payment on this job."
        amount = int(amount or 0)
        if amount <= 0:
            return "Enter the amount paid."
        if method not in PAY_METHODS:
            return "Choose how it was paid."
        if amount > j["balance"]:
            return f"The amount is more than the balance ({j['balance']:,.0f} Frw)."
        if paid_on and str(paid_on) > str(date.today()):
            return "The date is in the future."
        c.execute("INSERT INTO job_payments(job_id, amount, method, reference, paid_on, received_by, note, created_at) VALUES(?,?,?,?,?,?,?,?)",
                  (int(job_id), amount, method, (reference or "").strip(), str(paid_on or date.today()), st.session_state.user, "", now()))
        left = j["balance"] - amount
        log_event(c, job_id, "Payment", f"{amount:,} Frw ({method})" + (f". Paid in full." if left <= 0 else f". Balance {left:,.0f} Frw."))
        if left <= 0 and j["status"] == "Ready":
            notify(c, job_id, usernames_with_role(c, RECEPTIONIST) + usernames_with_role(c, CEO), f"Job {j['job_no']} ({j['customer']}) is paid in full and ready to hand over.")
    return None


def delete_payment(payment_id):
    if not has(CEO):
        return "Only the CEO can delete a payment."
    with conn() as c:
        p = c.execute("SELECT job_id, amount FROM job_payments WHERE id=?", (int(payment_id),)).fetchone()
        if p:
            c.execute("DELETE FROM job_payments WHERE id=?", (int(payment_id),))
            log_event(c, p["job_id"], "Payment deleted", f"{int(p['amount']):,} Frw")
    return None


# ---------- files ----------
def add_files(job_id, kind, files):
    """files = [(filename, bytes)]. Deliverables must be PDF. Returns an error text, or None."""
    with conn() as c:
        j = _job(c, job_id)
    action = "upload_deliverable" if kind == "Deliverable" else "upload_customer"
    if not j or not can(action, j):
        return "You cannot add files to this job now."
    msgs, saved = [], 0
    for name, data in files:
        err = save_file(job_id, kind, name, data, st.session_state.user, PDF_ONLY if kind == "Deliverable" else DOCUMENT_TYPES)
        if err:
            msgs.append(err)
        else:
            saved += 1
    if saved:
        with conn() as c:
            log_event(c, job_id, "Document added" if kind == "Deliverable" else "Customer document added", f"{saved} file(s)")
            if kind == "Deliverable" and j["status"] == "Ready":
                notify(c, job_id, usernames_with_role(c, CEO) + usernames_with_role(c, RECEPTIONIST), f"Job {j['job_no']}: a new version of the document was uploaded.")
            if kind != "Deliverable" and j["assigned_to"]:
                notify(c, job_id, [j["assigned_to"]], f"Job {j['job_no']}: the customer's documents were updated.")
    return " ".join(msgs) or None


def remove_file(job_id, file_id):
    """The CEO, or the person who uploaded the file while the job is still open, can remove it."""
    with conn() as c:
        j = _job(c, job_id)
        f = c.execute("SELECT filename, uploaded_by, kind FROM job_files WHERE id=? AND job_id=?", (int(file_id), int(job_id))).fetchone()
        if not j or not f:
            return "This file no longer exists."
        if not (has(CEO) or (f["uploaded_by"] == st.session_state.user and j["status"] in OPEN_STATUSES)):
            return "You cannot remove this file."
        log_event(c, job_id, "File removed", f["filename"])
    delete_file(file_id)
    return None
