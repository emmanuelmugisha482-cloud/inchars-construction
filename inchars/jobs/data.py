"""The jobs side without screens: settings and texts, rules, customers, the steps of a job, the notices and the emails."""

import pandas as pd
import streamlit as st
import sys
import threading
import traceback
from datetime import date, datetime
from functools import wraps

from inchars.accounts import has, send_email, smtp_conf
from inchars.database import IntegrityErrors, conn, df
from inchars.settings import CEO, EMAIL_RE, ENGINEER, RECEPTIONIST


# ======================================================================
# from services/config.py
# ======================================================================

SERVICES = ["Consultancy", "House design", "Building permit", "Other"]
PERMIT = "Building permit"
# The way of a job: registered -> With CEO -> (CEO gives it to an engineer) With engineer -> (engineer finishes) In review ->
# the CEO confirms -> Ready (with the Receptionist, the customer is emailed) -> Collected.   Or the CEO reverts it -> Reverted (back to the engineer).
JOB_STATUSES = ["With CEO", "With engineer", "Waiting for customer", "In review", "Reverted", "Ready", "Collected", "Cancelled"]
OPEN_STATUSES = ["With CEO", "With engineer", "Waiting for customer", "In review", "Reverted", "Ready"]
PERMIT_STAGES = ["Documents collected", "Submitted to government", "Under review", "Corrections requested", "Approved"]
PAY_METHODS = ["Cash", "Mobile money", "Bank transfer", "Other"]
MAX_FILE_MB = 15  # per file; files are kept in the database (see files.py to move them to an online file store)

NEED_REASONS = ["Copy of ID card", "Land title / UPI", "Site plan or map", "Payment", "Other documents"]  # what the customer must bring: tap
REVERT_REASONS = ["Needs correction", "Information is missing", "Not clear enough", "Wrong measurements", "Other"]  # the CEO reverts the work: tap

# What the customer is told when the CEO or the Receptionist presses the email button of a job.
STATUS_LINES = {
    "With CEO": "Your request is with our management, who will give it to the right engineer.",
    "With engineer": "An engineer is working on your request.",
    "Waiting for customer": "We are waiting for something from you before we can continue. Please contact our office.",
    "In review": "The work is finished and is being checked before we give it to you.",
    "Reverted": "We are making some final improvements to the work.",
    "Ready": "Your work is ready. Please come to our office and tell us your job number.",
    "Collected": "Your work was handed over to you. Thank you for working with us.",
}

# What we may not know about a customer. Nothing here is required: the screens only remind, they never block.
# (column in the customers table, how it is written to the staff). The email comes first: it is how we keep the customer informed.
MISSING_FIELDS = [("email", "email address"), ("phone", "phone number")]

# The emails the customer receives, written like a person would write them. {name} is the customer's first name.
# Each is (subject, text). Change a word here and the emails change.
EMAILS = {
    "registered": ("We have received your request ({job_no})",
                   "Hello {name},\n\nThank you for working with us. We have registered your request ({job_no}) and our team is on it.\n\n"
                   "We will keep you informed by email as it moves forward, and we will write to you whenever we need something else from you.\n\n"
                   "Warm regards,\nInchars PD Group"),
    "need": ("We need something from you ({job_no})",
             "Hello {name},\n\nTo continue with your request ({job_no}), we need the following from you:\n\n{what}\n\n"
             "Please bring it to our office, or reply to this email.\n\nThank you,\nInchars PD Group"),
    "status": ("An update on your request ({job_no})",
               "Hello {name},\n\nHere is the latest news about your request ({job_no}, {title}):\n\n{line}\n\n"
               "If you have a question, simply reply to this email or call our office.\n\nWarm regards,\nInchars PD Group"),
    "ready": ("Your work is ready ({job_no})",
              "Hello {name},\n\nGood news: your work ({job_no}) is ready. Please come to our office and tell us your job number.{balance}\n\n"
              "Thank you for working with us.\n\nWarm regards,\nInchars PD Group"),
}


# ======================================================================
# from services/core.py
# ======================================================================

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
    if action == "notify":  # email the customer the status of the job, by hand
        return has(CEO, RECEPTIONIST) and s != "Cancelled"
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


# ======================================================================
# from services/notify.py
# ======================================================================

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


# ======================================================================
# from services/workflow.py
# ======================================================================

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


# ======================================================================
# the table of jobs: updates (the old Inbox), what a person can do, the status email
# ======================================================================

ACTIONS = ("assign", "submit", "confirm", "revert", "wait", "resume", "collect", "cancel", "reopen", "stage", "edit", "set_contact")


def can_act(job):
    """True when this person has something to do or to change on the job (it decides who gets the pencil)."""
    return any(can(a, job) for a in ACTIONS)


def unread_by_job():
    """{job_id: number of unread updates for the signed-in person}."""
    r = df("SELECT job_id, COUNT(*) AS n FROM job_notices WHERE to_user=? AND read_at IS NULL GROUP BY job_id", (st.session_state.user,))
    return {int(a): int(b) for a, b in zip(r.job_id, r.n) if a == a}


def job_updates(job_id, limit=20):
    """The updates written for the signed-in person about one job (newest first): columns When, Update, read_at."""
    return df('SELECT created_at AS "When", message AS "Update", read_at FROM job_notices WHERE job_id=? AND to_user=? ORDER BY id DESC LIMIT ?',
              (int(job_id), st.session_state.user, int(limit)))


def mark_job_read(job_id):
    with conn() as c:
        c.execute("UPDATE job_notices SET read_at=? WHERE job_id=? AND to_user=? AND read_at IS NULL", (now(), int(job_id), st.session_state.user))


def mark_all_read():
    with conn() as c:
        c.execute("UPDATE job_notices SET read_at=? WHERE to_user=? AND read_at IS NULL", (now(), st.session_state.user))


@after_commit
def email_status(job_id):
    """The email button: tell the customer, in words, where the job is now. Returns (level, text): level is 'ok', 'warn' or 'error'."""
    with conn() as c:
        j = fetch_job(c, job_id)
        if not j or not can("notify", j):
            return "error", "You cannot email the customer about this job."
        line = STATUS_LINES.get(j["status"], "Your request is being handled.")
        if j["status"] == "Ready" and j["balance"] > 0:
            line += f" The balance to pay is {j['balance']:,.0f} Frw."
        res = tell_customer(c, job_id, "status", line=line, title=j["title"])
        if res == "queued":
            log_event(c, job_id, "Status email asked", f"{st.session_state.user} sent the status: {j['status']}")
    if res == "no_email":
        return "warn", "This customer has no email address yet. Open the job with the pencil and add it first."
    if res == "not_set_up":
        return "warn", "Email is not set up yet. Ask the CEO to open System check."
    return "ok", f"The customer was emailed the status of {j['job_no']}: {j['status']}."
