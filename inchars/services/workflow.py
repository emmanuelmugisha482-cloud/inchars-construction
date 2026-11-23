"""The steps of a job. No screens here.

  Receptionist registers  ->  With CEO  ->  the CEO gives it to an engineer  ->  With engineer
  ->  the engineer finishes and sends it to the CEO  ->  In review
  ->  the CEO confirms  ->  Ready (the Receptionist hands it over; the customer is emailed "your work is ready")  ->  Collected
      or the CEO reverts it, with a reason  ->  Reverted (back to the engineer, who corrects it and sends it again).

Every step writes the History and puts a notice in the Inbox (and an email) of the people concerned. The customer is emailed three times:
registered, we need something from you, your work is ready (see notify.py)."""

from datetime import date

from inchars.accounts import has
from inchars.config import CEO, ENGINEER, RECEPTIONIST
from inchars.db import IntegrityErrors, conn
from inchars.services.config import PAY_METHODS, PERMIT_STAGES, SERVICES
from inchars.services.core import can, fetch_job, log_event, now
from inchars.services.notify import after_commit, notify, tell_customer, usernames_with_role
import streamlit as st


def _next_job_no(c):
    year = date.today().year
    n = c.execute("SELECT COUNT(*) AS n FROM jobs WHERE job_no LIKE ?", (f"INC-{year}-%",)).fetchone()["n"] + 1
    return f"INC-{year}-{n:04d}"


@after_commit
def create_job(customer_id, service, title, description, fee, due_on, deposit=0, method="Cash", reference=""):
    """Register a job. It goes straight to the CEO. Returns (job_id, job_no, error). The customer gets a thank-you email (if there is an address)."""
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
                                (job_no, int(customer_id), service, title.strip(), (description or "").strip(), "With CEO", fee, str(due_on) if due_on else None, me, now())).fetchone()["id"]
                log_event(c, jid, "Registered", f"{service} for {cust['name']}. Fee {fee:,} Frw. Sent to the CEO.")
                if deposit:
                    c.execute("INSERT INTO job_payments(job_id, amount, method, reference, paid_on, received_by, note, created_at) VALUES(?,?,?,?,?,?,?,?)",
                              (jid, deposit, method if method in PAY_METHODS else "Cash", (reference or "").strip(), str(date.today()), me, "Deposit", now()))
                    log_event(c, jid, "Payment", f"Deposit {deposit:,} Frw ({method}).")
                notify(c, jid, usernames_with_role(c, CEO), f"New job {job_no}: {service} for {cust['name']}. Waiting for your decision: give it to an engineer, or send it back.")
                tell_customer(c, jid, "registered")
            return int(jid), job_no, None
        except IntegrityErrors:
            continue  # the number was taken at the same moment: try the next one
    return None, None, "Could not create the job number. Please try again."


@after_commit
def assign_job(job_id, engineer, due_on):
    """The CEO gives the job to an engineer (or changes the engineer)."""
    with conn() as c:
        j = fetch_job(c, job_id)
        if not j or not can("assign", j):
            return "You cannot assign this job."
        if engineer not in [r["username"] for r in c.execute("SELECT DISTINCT u.username FROM users u JOIN user_roles r ON r.user_id=u.id WHERE r.role=?", (ENGINEER,)).fetchall()]:
            return "Choose an engineer."
        old = j["assigned_to"]
        due = str(due_on) if due_on else j["due_on"]
        c.execute("UPDATE jobs SET assigned_to=?, due_on=?, status=? WHERE id=?", (engineer, due, "With engineer" if j["status"] == "With CEO" else j["status"], int(job_id)))
        log_event(c, job_id, "Given to an engineer" if not old else "Engineer changed", f"{engineer}" + (f" (was {old})" if old and old != engineer else "") + (f", due {due_on}" if due_on else ""))
        notify(c, job_id, [engineer], f"Job {j['job_no']} was given to you: {j['service']} for {j['customer']}" + (f". Due {due_on}." if due_on else "."))
        if old and old != engineer:
            notify(c, job_id, [old], f"Job {j['job_no']} was given to {engineer}. It is no longer yours.")
    return None


@after_commit
def change_status(job_id, action, note="", override=False):
    """submit, confirm, revert, wait, resume, collect, cancel, reopen. Returns an error text, or None when done."""
    me = st.session_state.user
    with conn() as c:
        j = fetch_job(c, job_id)
        if not j:
            return "This job no longer exists."
        if not can(action, j):
            return "You cannot do this on this job now."
        note = (note or "").strip()
        no, cust = j["job_no"], j["customer"]
        ceos, recs = usernames_with_role(c, CEO), usernames_with_role(c, RECEPTIONIST)
        eng = [j["assigned_to"]] if j["assigned_to"] else []
        jid = int(job_id)
        if action == "submit":
            c.execute("UPDATE jobs SET status='In review' WHERE id=?", (jid,))
            log_event(c, job_id, "Sent to the CEO", note or "The work is finished.")
            notify(c, job_id, ceos, f"Job {no} ({cust}) is finished and waits for your check." + (f" Note: {note}" if note else ""))
        elif action == "confirm":
            c.execute("UPDATE jobs SET status='Ready', ready_at=? WHERE id=?", (now(), jid))
            log_event(c, job_id, "Confirmed by the CEO", note or "The work is approved.")
            notify(c, job_id, recs + eng, f"Job {no} ({cust}) was CONFIRMED by the CEO. It is ready for the customer." + (" Fully paid." if j["balance"] <= 0 else f" Balance to pay: {j['balance']:,.0f} Frw."))
            tell_customer(c, job_id, "ready", balance=f"\n\nBefore we hand over the documents, the balance to pay is {j['balance']:,.0f} Frw." if j["balance"] > 0 else "")
        elif action == "revert":
            if not note:
                return "Write the reason for sending the work back."
            c.execute("UPDATE jobs SET status='Reverted', returned_reason=? WHERE id=?", (note, jid))
            log_event(c, job_id, "Reverted by the CEO", note)
            notify(c, job_id, eng, f"Job {no} ({cust}) was SENT BACK by the CEO: {note}")
        elif action == "wait":
            if not note:
                return "Choose or write what you need from the customer."
            c.execute("UPDATE jobs SET status='Waiting for customer' WHERE id=?", (jid,))
            log_event(c, job_id, "Waiting for customer", note)
            notify(c, job_id, recs + ceos, f"Job {no} ({cust}) is waiting for the customer: {note}")
            tell_customer(c, job_id, "need", what=note)
        elif action == "resume":
            c.execute("UPDATE jobs SET status='With engineer' WHERE id=?", (jid,))
            log_event(c, job_id, "Work continues", note or "The customer answered.")
            notify(c, job_id, eng, f"Job {no} ({cust}): the customer answered, work can continue." + (f" {note}" if note else ""))
        elif action == "collect":
            if j["balance"] > 0 and not (has(CEO) and override):
                return f"The job is not paid in full: {j['balance']:,.0f} Frw is still to pay."
            if j["balance"] > 0 and not note:
                return "Write the reason for releasing the document without full payment."
            c.execute("UPDATE jobs SET status='Collected', collected_at=?, collected_by=? WHERE id=?", (now(), me, jid))
            log_event(c, job_id, "Collected by the customer", (f"Released with {j['balance']:,.0f} Frw unpaid: " + note) if j["balance"] > 0 else (note or "Paid in full."))
            notify(c, job_id, ceos, f"Job {no} ({cust}) was collected by the customer.")
        elif action == "cancel":
            if not note:
                return "Write the reason for cancelling."
            c.execute("UPDATE jobs SET status='Cancelled' WHERE id=?", (jid,))
            log_event(c, job_id, "Cancelled", note)
            notify(c, job_id, ceos + eng, f"Job {no} ({cust}) was cancelled: {note}")
        elif action == "reopen":
            if not note:
                return "Write why the job is reopened."
            c.execute("UPDATE jobs SET status=?, collected_at=NULL, collected_by=NULL WHERE id=?", ("With engineer" if j["assigned_to"] else "With CEO", jid))
            log_event(c, job_id, "Reopened", note)
            notify(c, job_id, eng or ceos, f"Job {no} ({cust}) was reopened: {note}")
    return None


@after_commit
def set_stage(job_id, stage, gov_ref=""):
    """Building permit jobs: where the file is at the government office."""
    with conn() as c:
        j = fetch_job(c, job_id)
        if not j or not can("stage", j):
            return "You cannot change the stage of this job."
        if stage not in PERMIT_STAGES:
            return "Choose the stage."
        ref = (gov_ref or "").strip() or j["gov_ref"]
        c.execute("UPDATE jobs SET stage=?, gov_ref=? WHERE id=?", (stage, ref, int(job_id)))
        log_event(c, job_id, f"Permit stage: {stage}", f"Reference {ref}" if ref else "")
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
