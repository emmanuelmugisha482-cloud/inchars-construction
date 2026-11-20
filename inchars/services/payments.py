"""Payments for jobs: deposits and part payments. The customer receives a receipt by email."""

from datetime import date

import streamlit as st

from inchars.accounts import has
from inchars.config import CEO
from inchars.db import conn, df
from inchars.services.config import PAY_METHODS
from inchars.services.history import log_event, now
from inchars.services.jobs import load_jobs
from inchars.services.notices import notify, tell_customer, usernames_with_role
from inchars.services.rules import can, fetch_job, sees_all_jobs
from inchars.config import RECEPTIONIST


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
        j = fetch_job(c, job_id)
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
        log_event(c, job_id, "Payment", f"{amount:,} Frw ({method})" + (". Paid in full." if left <= 0 else f". Balance {left:,.0f} Frw."))
        tell_customer(c, job_id, "payment received", f"We have received your payment of {amount:,} Frw for job {j['job_no']} ({j['title']}).\n"
                      + ("Your job is paid in full. Thank you." if left <= 0 else f"The balance to pay is {left:,.0f} Frw."))
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
