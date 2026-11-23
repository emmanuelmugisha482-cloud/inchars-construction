"""Core of the services side: who may do what, the customers, the list of jobs, the history. No screens here.

Everything else in services/ builds on this file, so it is kept small and plain. Customers need only a NAME: the phone number
(for contact) and the email are welcome but optional, and the screens simply remind the staff of what is still missing."""

from datetime import date, datetime

import pandas as pd
import streamlit as st

from inchars.accounts import has
from inchars.config import CEO, EMAIL_RE, ENGINEER, RECEPTIONIST
from inchars.db import conn, df
from inchars.services.config import MISSING_FIELDS, OPEN_STATUSES, PERMIT


# ---------------------------------------------------------------- history
def now():
    return datetime.now().isoformat(timespec="seconds")


def log_event(c, job_id, event, detail="", by=None):
    c.execute("INSERT INTO job_events(job_id, happened_at, by_user, event, detail) VALUES(?,?,?,?,?)",
              (int(job_id), now(), by or st.session_state.user, event, detail))


def load_events(job_id):
    return df('SELECT happened_at AS "When", by_user AS "By", event AS "What", detail AS "Detail" FROM job_events WHERE job_id=? ORDER BY id DESC', (int(job_id),))


# ---------------------------------------------------------------- phone numbers
def clean_phone(v):
    """A phone number in one form (+250788123456), or '' when it is empty or does not look like a phone number."""
    raw = str(v or "").strip()
    d = "".join(ch for ch in raw if ch.isdigit())
    if raw.startswith("+") and 8 <= len(d) <= 15:
        return "+" + d
    if d.startswith("250") and len(d) == 12:
        return "+" + d
    if d.startswith("0") and len(d) == 10:
        return "+250" + d[1:]
    if len(d) == 9 and d[0] == "7":
        return "+250" + d
    return ""


def missing_info(phone, email):
    """What we do not know yet about a customer: a list of (column, label). Empty when we know everything."""
    have = {"phone": bool(clean_phone(phone)), "email": bool((email or "").strip())}
    return [(col, label) for col, label in MISSING_FIELDS if not have.get(col)]


# ---------------------------------------------------------------- who may do what
def sees_all_jobs():
    """The CEO and the Receptionist see every job. An Engineer sees only the jobs given to him or her."""
    return has(CEO, RECEPTIONIST)


def sees_money():
    """Fees and payments of jobs are seen by the CEO and the Receptionist only. An Engineer never sees money."""
    return has(CEO, RECEPTIONIST)


def split_name(name):
    """'MUKAMWIZA Isabelle' -> ('MUKAMWIZA', 'Isabelle'): the first word is the family name. One word only: it is both."""
    parts = (name or "").split()
    return (parts[0], " ".join(parts[1:]) if len(parts) > 1 else "") if parts else ("", "")


def first_name(name):
    fam, first = split_name(name)
    return (first or fam).title() if (first or fam).isupper() else (first or fam)


def can(action, job):
    """True when the signed-in person may do `action` on this job (a row of load_jobs, or fetch_job).
    The screens only decide what to show: every function that changes a job asks again.

    CEO: assign, confirm or revert the finished work.  Engineer: do the work, send it to the CEO.  Receptionist: register, hand over."""
    me = st.session_state.user
    s = job["status"]
    eng = has(ENGINEER) and job["assigned_to"] == me
    working = s in ("With engineer", "Reverted")
    if action == "assign":
        return has(CEO) and s in OPEN_STATUSES
    if action == "submit":  # the engineer finished: send to the CEO
        return eng and working
    if action in ("confirm", "revert"):  # the CEO checks the finished work
        return has(CEO) and s == "In review"
    if action == "wait":  # ask the customer for something
        return (eng or has(CEO)) and s in ("With engineer", "Reverted", "In review")
    if action == "resume":
        return (eng or has(CEO, RECEPTIONIST)) and s == "Waiting for customer"
    if action == "collect":
        return has(CEO, RECEPTIONIST) and s == "Ready"
    if action == "cancel":
        return (has(CEO) and s in OPEN_STATUSES) or (has(RECEPTIONIST) and s == "With CEO")
    if action == "reopen":
        return has(CEO) and s in ("Collected", "Cancelled")
    if action == "stage":
        return job["service"] == PERMIT and (eng or has(CEO, RECEPTIONIST)) and s in ("With engineer", "Reverted", "Waiting for customer", "In review")
    if action == "edit":
        return has(CEO, RECEPTIONIST) and s in OPEN_STATUSES
    if action == "set_contact":  # add the email or phone of the customer, at any time, for example when he or she comes back
        return has(CEO, RECEPTIONIST)
    if action == "pay":
        return has(CEO, RECEPTIONIST) and s != "Cancelled"
    if action == "upload_customer":
        return (has(CEO, RECEPTIONIST) or eng) and s in OPEN_STATUSES
    if action == "upload_deliverable":
        return (eng or has(CEO)) and s in ("With engineer", "Reverted", "Waiting for customer", "In review", "Ready")
    if action in ("see_files", "note"):
        return has(CEO, RECEPTIONIST) or eng
    return False


def can_open_deliverable(job):
    """The finished PDF is handed out only when the job is paid in full. The CEO and the assigned engineer can always open it."""
    if has(CEO) or (has(ENGINEER) and job["assigned_to"] == st.session_state.user):
        return True
    return has(RECEPTIONIST) and job["status"] in ("Ready", "Collected") and float(job["balance"]) <= 0


# ---------------------------------------------------------------- customers
def load_customers():
    return df("""SELECT c.id, c.name AS "Name", c.phone AS "Phone", c.email AS "Email", c.note AS "Note",
                 (SELECT COUNT(*) FROM jobs j WHERE j.customer_id=c.id) AS "Jobs" FROM customers c ORDER BY c.name""")


def save_customer(name, phone="", email="", note="", customer_id=None):
    """Add or change a customer. Only the name is required. Returns (customer_id, error)."""
    name, phone, email = (name or "").strip(), (phone or "").strip(), (email or "").strip()
    if not name:
        return None, "Write the customer's name."
    if phone and not clean_phone(phone):
        return None, "The phone number does not look right (for example 0788 123 456). You can also leave it empty."
    if email and not EMAIL_RE.match(email):
        return None, "The email address is not valid. You can also leave it empty."
    phone = clean_phone(phone)
    with conn() as c:
        if phone:
            for r in c.execute("SELECT id, name, phone FROM customers").fetchall():
                if clean_phone(r["phone"]) == phone and r["id"] != customer_id:
                    return None, f"This phone number already belongs to {r['name']}. Choose that customer instead."
        if customer_id:
            c.execute("UPDATE customers SET name=?, phone=?, email=?, note=? WHERE id=?", (name, phone, email, (note or "").strip(), int(customer_id)))
            return int(customer_id), None
        cid = c.execute("INSERT INTO customers(name, phone, email, note, created_at, created_by) VALUES(?,?,?,?,?,?) RETURNING id",
                        (name, phone, email, (note or "").strip(), now(), st.session_state.user)).fetchone()["id"]
    return int(cid), None


def update_customer_info(customer_id, phone="", email=""):
    """Fill in what was missing (the 'information we do not have' bar). A blank box changes nothing. Returns an error text, or None."""
    if not has(CEO, RECEPTIONIST):
        return "You cannot change the customer's details."
    phone, email = (phone or "").strip(), (email or "").strip()
    if not phone and not email:
        return "Write the phone number or the email address first."
    if phone and not clean_phone(phone):
        return "The phone number does not look right (for example 0788 123 456)."
    if email and not EMAIL_RE.match(email):
        return "The email address is not valid."
    with conn() as c:
        if phone:
            for r in c.execute("SELECT id, name, phone FROM customers").fetchall():
                if clean_phone(r["phone"]) == clean_phone(phone) and r["id"] != int(customer_id):
                    return f"This phone number already belongs to {r['name']}."
            c.execute("UPDATE customers SET phone=? WHERE id=?", (clean_phone(phone), int(customer_id)))
        if email:
            c.execute("UPDATE customers SET email=? WHERE id=?", (email, int(customer_id)))
        for (jid,) in [(r["id"],) for r in c.execute("SELECT id FROM jobs WHERE customer_id=? AND status IN (%s)" % ",".join("?" * len(OPEN_STATUSES)),
                                                      (int(customer_id),) + tuple(OPEN_STATUSES)).fetchall()]:
            log_event(c, jid, "Customer details added", ", ".join(x for x, v in (("phone number", phone), ("email address", email)) if v))
    return None


# ---------------------------------------------------------------- jobs
def fetch_job(c, job_id):
    """One job as a dict, with the customer, the money paid and the balance (None when it does not exist)."""
    r = c.execute("""SELECT j.*, COALESCE((SELECT SUM(p.amount) FROM job_payments p WHERE p.job_id=j.id), 0) AS paid,
                     cu.name AS customer, cu.email AS customer_email, cu.phone AS customer_phone
                     FROM jobs j JOIN customers cu ON cu.id=j.customer_id WHERE j.id=?""", (int(job_id),)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["balance"] = float(d["fee"] or 0) - float(d["paid"] or 0)
    return d


def load_jobs():
    """Every job the signed-in person may see, with the customer, the money (fee, paid, balance) and what we still do not know."""
    j = df("""SELECT j.id, j.job_no, j.created_at, j.service, j.title, j.description, j.status, j.stage, j.gov_ref, j.fee, j.due_on,
              j.assigned_to, j.created_by, j.ready_at, j.collected_at, j.collected_by, j.returned_reason, c.id AS customer_id, c.name AS customer,
              c.phone AS phone, c.email AS customer_email,
              COALESCE((SELECT SUM(p.amount) FROM job_payments p WHERE p.job_id=j.id), 0) AS paid
              FROM jobs j JOIN customers c ON c.id=j.customer_id ORDER BY j.id DESC""")
    if not sees_all_jobs():
        j = j[j.assigned_to == st.session_state.user]
    j = j.reset_index(drop=True)
    for col in ("description", "stage", "gov_ref", "due_on", "assigned_to", "ready_at", "collected_at", "collected_by", "customer_email", "phone", "returned_reason"):
        j[col] = j[col].fillna("")  # empty text instead of None, so the screens can test it simply
    j["fee"] = pd.to_numeric(j.fee).fillna(0)
    j["paid"] = pd.to_numeric(j.paid).fillna(0)
    j["balance"] = j.fee - j.paid
    if not sees_money():  # an Engineer never receives the money of a job (not even hidden in a column)
        j["fee"], j["paid"], j["balance"] = 0.0, 0.0, 0.0
    j["surname"] = [split_name(n)[0] for n in j.customer]
    j["firstname"] = [split_name(n)[1] for n in j.customer]
    today = str(date.today())
    j["overdue"] = [bool(d) and d < today and s in OPEN_STATUSES and s != "Ready" for d, s in zip(j.due_on, j.status)]
    j["missing"] = [", ".join(label for _, label in missing_info(p, e)) for p, e in zip(j.phone, j.customer_email)]
    return j


def load_engineers():
    return df("SELECT DISTINCT u.username FROM users u JOIN user_roles r ON r.user_id=u.id WHERE r.role=? ORDER BY u.username", (ENGINEER,)).username.tolist()
