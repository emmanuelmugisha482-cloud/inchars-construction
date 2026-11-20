"""Jobs: register, assign, follow the status. Every step tells the people concerned (see notices.py).

Statuses: New, Assigned, In progress, Waiting for customer, Ready, Collected, Cancelled. A finished PDF is never required."""

from datetime import date

import pandas as pd
import streamlit as st

from inchars.accounts import has
from inchars.config import CEO, ENGINEER, RECEPTIONIST
from inchars.db import IntegrityErrors, conn, df
from inchars.services.config import OPEN_STATUSES, PAY_METHODS, PERMIT_STAGES, SERVICES
from inchars.services.history import log_event, now
from inchars.services.notices import notify, tell_customer, usernames_with_role
from inchars.services.rules import can, fetch_job, sees_all_jobs


def load_jobs():
    """Every job the signed-in person may see, with the customer and the money (fee, paid, balance)."""
    j = df("""SELECT j.id, j.job_no, j.created_at, j.service, j.title, j.description, j.status, j.stage, j.gov_ref, j.fee, j.due_on,
              j.assigned_to, j.created_by, j.ready_at, j.collected_at, j.collected_by, c.id AS customer_id, c.name AS customer, c.phone AS phone,
              c.email AS customer_email,
              COALESCE((SELECT SUM(p.amount) FROM job_payments p WHERE p.job_id=j.id), 0) AS paid
              FROM jobs j JOIN customers c ON c.id=j.customer_id ORDER BY j.id DESC""")
    if not sees_all_jobs():
        j = j[j.assigned_to == st.session_state.user]
    j = j.reset_index(drop=True)
    for col in ("description", "stage", "gov_ref", "due_on", "assigned_to", "ready_at", "collected_at", "collected_by", "customer_email"):
        j[col] = j[col].fillna("")  # empty text instead of None, so the screens can test it simply
    j["fee"] = pd.to_numeric(j.fee).fillna(0)
    j["paid"] = pd.to_numeric(j.paid).fillna(0)
    j["balance"] = j.fee - j.paid
    today = str(date.today())
    j["overdue"] = [bool(d) and d < today and s in OPEN_STATUSES and s != "Ready" for d, s in zip(j.due_on, j.status)]
    return j


def load_engineers():
    return df("SELECT DISTINCT u.username FROM users u JOIN user_roles r ON r.user_id=u.id WHERE r.role=? ORDER BY u.username", (ENGINEER,)).username.tolist()


def _next_job_no(c):
    year = date.today().year
    n = c.execute("SELECT COUNT(*) AS n FROM jobs WHERE job_no LIKE ?", (f"INC-{year}-%",)).fetchone()["n"] + 1
    return f"INC-{year}-{n:04d}"


def create_job(customer_id, service, title, description, fee, due_on, deposit=0, method="Cash", reference=""):
    """Register a job. Returns (job_id, job_no, error). The customer receives a thank-you email; the CEO is notified."""
    if not has(CEO, RECEPTIONIST):
        return None, None, "Only the Receptionist or the CEO can register a job."
    if service not in SERVICES:
        return None, None, "Choose the service."
    if not (title or "").strip():
        return None, None, "Write a short title for the job."
    fee, deposit = int(fee or 0), int(deposit or 0)
    if fee < 0 or deposit < 0:
        return None, None, "Amounts cannot be negative."
    if deposit > fee:
        return None, None, "The deposit is more than the fee."
    me = st.session_state.user
    for _ in range(4):
        try:
            with conn() as c:
                job_no = _next_job_no(c)
                cust = c.execute("SELECT name FROM customers WHERE id=?", (int(customer_id),)).fetchone()
                if not cust:
                    return None, None, "Choose the customer."
                jid = c.execute("""INSERT INTO jobs(job_no, customer_id, service, title, description, status, fee, due_on, created_by, created_at)
                                   VALUES(?,?,?,?,?,?,?,?,?,?) RETURNING id""",
                                (job_no, int(customer_id), service, title.strip(), (description or "").strip(), "New", fee, str(due_on) if due_on else None, me, now())).fetchone()["id"]
                log_event(c, jid, "Registered", f"{service} for {cust['name']}. Fee {fee:,} Frw.")
                if deposit:
                    c.execute("INSERT INTO job_payments(job_id, amount, method, reference, paid_on, received_by, note, created_at) VALUES(?,?,?,?,?,?,?,?)",
                              (jid, deposit, method if method in PAY_METHODS else "Cash", (reference or "").strip(), str(date.today()), me, "Deposit", now()))
                    log_event(c, jid, "Payment", f"Deposit {deposit:,} Frw ({method}).")
                notify(c, jid, usernames_with_role(c, CEO), f"New job {job_no}: {service} for {cust['name']}. It needs an engineer.")
                tell_customer(c, jid, "thank you for choosing us",
                              f"Thank you for choosing to work with Inchars PD Group.\n\nWe have registered your job:\n"
                              f"  Job number: {job_no}\n  Service: {service}\n  Title: {title.strip()}\n"
                              + (f"  Promised date: {due_on}\n" if due_on else "")
                              + (f"  Deposit received: {deposit:,} Frw\n" if deposit else "")
                              + "\nPlease keep your job number: you will need it when you come to collect your document.\n"
                              "We will keep you informed of our progress by email.")
            return int(jid), job_no, None
        except IntegrityErrors:
            continue  # the number was taken at the same moment: try the next one
    return None, None, "Could not create the job number. Please try again."


def assign_job(job_id, engineer, due_on):
    with conn() as c:
        j = fetch_job(c, job_id)
        if not j or not can("assign", j):
            return "You cannot assign this job."
        if engineer not in [r["username"] for r in c.execute("SELECT DISTINCT u.username FROM users u JOIN user_roles r ON r.user_id=u.id WHERE r.role=?", (ENGINEER,)).fetchall()]:
            return "Choose an engineer."
        old = j["assigned_to"]
        due = str(due_on) if due_on else j["due_on"]
        c.execute("UPDATE jobs SET assigned_to=?, due_on=?, status=? WHERE id=?", (engineer, due, "Assigned" if j["status"] == "New" else j["status"], int(job_id)))
        log_event(c, job_id, "Assigned" if not old else "Reassigned", f"{engineer}" + (f" (was {old})" if old and old != engineer else "") + (f", due {due_on}" if due_on else ""))
        notify(c, job_id, [engineer], f"Job {j['job_no']} was given to you: {j['service']} for {j['customer']}" + (f". Due {due_on}." if due_on else "."))
        if old and old != engineer:
            notify(c, job_id, [old], f"Job {j['job_no']} was given to {engineer}. It is no longer yours.")
        if not old:
            tell_customer(c, job_id, "your job was given to our engineer", f"Your job {j['job_no']} ({j['title']}) was given to one of our engineers."
                          + (f"\nPromised date: {due}." if due else "") + "\nWe will keep you informed of our progress.")
    return None


def change_status(job_id, action, note="", override=False):
    """start, wait, resume, ready, collect, cancel, reopen. Returns an error text, or None when done."""
    me = st.session_state.user
    with conn() as c:
        j = fetch_job(c, job_id)
        if not j:
            return "This job no longer exists."
        if not can(action, j):
            return "You cannot do this on this job now."
        note = (note or "").strip()
        no, cust, title = j["job_no"], j["customer"], j["title"]
        ceos, recs = usernames_with_role(c, CEO), usernames_with_role(c, RECEPTIONIST)
        if action == "start":
            c.execute("UPDATE jobs SET status='In progress' WHERE id=?", (int(job_id),))
            log_event(c, job_id, "Started work")
            tell_customer(c, job_id, "work has started", f"Work has started on your job {no} ({title}). We will keep you informed of our progress.")
        elif action == "wait":
            if not note:
                return "Choose or write what you need from the customer."
            c.execute("UPDATE jobs SET status='Waiting for customer' WHERE id=?", (int(job_id),))
            log_event(c, job_id, "Waiting for customer", note)
            notify(c, job_id, recs + ceos, f"Job {no} ({cust}) is waiting for the customer: {note}")
            tell_customer(c, job_id, "we need something from you", f"To continue with your job {no} ({title}) we need the following from you:\n  {note}\n\nPlease bring it to our office or contact us. Thank you.")
        elif action == "resume":
            c.execute("UPDATE jobs SET status='In progress' WHERE id=?", (int(job_id),))
            log_event(c, job_id, "Work continues", note or "The customer answered.")
            if j["assigned_to"]:
                notify(c, job_id, [j["assigned_to"]], f"Job {no} ({cust}): work can continue." + (f" {note}" if note else ""))
        elif action == "ready":
            c.execute("UPDATE jobs SET status='Ready', ready_at=? WHERE id=?", (now(), int(job_id)))
            paid = "paid in full" if j["balance"] <= 0 else f"balance to pay: {j['balance']:,.0f} Frw"
            log_event(c, job_id, "Ready", note or "The job is finished.")
            notify(c, job_id, ceos + recs, f"Job {no} ({cust}) is READY for the customer - {paid}.")
            tell_customer(c, job_id, "your document is ready",
                          f"Good news: your document for job {no} ({title}) is ready.\nPlease come to our office with your job number {no}."
                          + (f"\nBalance to pay before we hand it over: {j['balance']:,.0f} Frw." if j["balance"] > 0 else "\nYour job is paid in full."))
        elif action == "collect":
            if j["balance"] > 0 and not (has(CEO) and override):
                return f"The job is not paid in full: {j['balance']:,.0f} Frw is still to pay."
            if j["balance"] > 0 and not note:
                return "Write the reason for releasing the document without full payment."
            c.execute("UPDATE jobs SET status='Collected', collected_at=?, collected_by=? WHERE id=?", (now(), me, int(job_id)))
            log_event(c, job_id, "Collected by the customer", ("Released with " + f"{j['balance']:,.0f} Frw unpaid: " + note) if j["balance"] > 0 else (note or "Paid in full."))
            notify(c, job_id, ceos, f"Job {no} ({cust}) was collected by the customer.")
            tell_customer(c, job_id, "thank you", f"Thank you for collecting your document for job {no} ({title}). It was a pleasure to work with you.")
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
    """Building permit jobs: where the file is at the government office. The customer is told."""
    with conn() as c:
        j = fetch_job(c, job_id)
        if not j or not can("stage", j):
            return "You cannot change the stage of this job."
        if stage not in PERMIT_STAGES:
            return "Choose the stage."
        ref = (gov_ref or "").strip() or j["gov_ref"]
        c.execute("UPDATE jobs SET stage=?, gov_ref=? WHERE id=?", (stage, ref, int(job_id)))
        log_event(c, job_id, f"Permit stage: {stage}", f"Reference {ref}" if ref else "")
        tell_customer(c, job_id, f"permit update: {stage}", f"Update on your building permit (job {j['job_no']}): {stage}." + (f"\nGovernment reference number: {ref}." if ref else ""))
        if stage in ("Approved", "Corrections requested"):
            notify(c, job_id, usernames_with_role(c, CEO) + usernames_with_role(c, RECEPTIONIST), f"Job {j['job_no']} ({j['customer']}): permit stage is now {stage}.")
    return None


def edit_job(job_id, title, description, fee, due_on):
    with conn() as c:
        j = fetch_job(c, job_id)
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
