"""Who may do what on a job. The screens only decide what to show: every function that changes a job checks these rules again."""

import streamlit as st

from inchars.accounts import has
from inchars.config import ACCOUNTANT, CEO, ENGINEER, RECEPTIONIST
from inchars.services.config import OPEN_STATUSES, PERMIT


def fetch_job(c, job_id):
    """One job as a dict, with the customer, the money paid and the balance (None when it does not exist)."""
    r = c.execute("""SELECT j.*, COALESCE((SELECT SUM(p.amount) FROM job_payments p WHERE p.job_id=j.id), 0) AS paid,
                     cu.name AS customer, cu.email AS customer_email
                     FROM jobs j JOIN customers cu ON cu.id=j.customer_id WHERE j.id=?""", (int(job_id),)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["balance"] = float(d["fee"] or 0) - float(d["paid"] or 0)
    return d


def sees_all_jobs():
    """The CEO, Accountant and Receptionist see every job. An Engineer sees the jobs given to him or her."""
    return has(CEO, ACCOUNTANT, RECEPTIONIST)


def can(action, job):
    """True when the signed-in person may do `action` on this job (a row of load_jobs, or fetch_job)."""
    me = st.session_state.user
    s = job["status"]
    mine = job["assigned_to"] == me
    eng = has(ENGINEER) and mine
    if action == "assign":
        return has(CEO) and s in OPEN_STATUSES
    if action == "start":
        return eng and s == "Assigned"
    if action == "wait":
        return (eng or has(CEO)) and s in ("Assigned", "In progress")
    if action == "resume":
        return (eng or has(CEO, RECEPTIONIST)) and s == "Waiting for customer"
    if action == "ready":  # a finished PDF is NOT required
        return (eng or has(CEO)) and s in ("Assigned", "In progress", "Waiting for customer")
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
    if action == "set_email":
        return has(CEO, RECEPTIONIST) and s in OPEN_STATUSES
    if action == "pay":
        return has(CEO, ACCOUNTANT, RECEPTIONIST) and s != "Cancelled"
    if action == "upload_customer":
        return (has(CEO, RECEPTIONIST) or eng) and s in OPEN_STATUSES
    if action == "upload_deliverable":
        return (eng or has(CEO)) and s in ("Assigned", "In progress", "Waiting for customer", "Ready")
    if action == "see_files":
        return has(CEO, RECEPTIONIST) or eng
    if action in ("note", "message"):
        return has(CEO, RECEPTIONIST) or eng
    return False


def can_open_deliverable(job):
    """The finished PDF is handed out only when the job is paid in full. The CEO and the assigned engineer can always open it."""
    if has(CEO) or (has(ENGINEER) and job["assigned_to"] == st.session_state.user):
        return True
    return has(RECEPTIONIST) and job["status"] in ("Ready", "Collected") and float(job["balance"]) <= 0
