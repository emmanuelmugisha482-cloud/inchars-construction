"""Customers: the people who bring jobs. The email address matters: it is how the customer receives the progress updates."""

import streamlit as st

from inchars.accounts import has
from inchars.config import CEO, EMAIL_RE, RECEPTIONIST
from inchars.db import conn, df
from inchars.services.history import now


def load_customers():
    return df("""SELECT c.id, c.name AS "Name", c.phone AS "Phone", c.email AS "Email", c.note AS "Note",
                 (SELECT COUNT(*) FROM jobs j WHERE j.customer_id=c.id) AS "Jobs" FROM customers c ORDER BY c.name""")


def _digits(v):
    return "".join(ch for ch in str(v or "") if ch.isdigit())


def save_customer(name, phone, email, note="", customer_id=None, no_email=False):
    """Add or change a customer. The email address is required (so the customer can be informed) unless no_email is True.
    Returns (customer_id, error)."""
    name, phone, email = (name or "").strip(), (phone or "").strip(), (email or "").strip()
    if not name:
        return None, "Write the customer's name."
    if len(_digits(phone)) < 7:
        return None, "Write the customer's phone number (at least 7 digits)."
    if not email and not no_email:
        return None, "Write the customer's email address: it is how he or she receives our updates. If the customer has none, tick 'No email'."
    if email and not EMAIL_RE.match(email):
        return None, "The customer's email address is not valid."
    with conn() as c:
        for r in c.execute("SELECT id, name, phone FROM customers").fetchall():
            if _digits(r["phone"]) == _digits(phone) and r["id"] != customer_id:
                return None, f"This phone number already belongs to {r['name']}. Choose that customer instead."
        if customer_id:
            c.execute("UPDATE customers SET name=?, phone=?, email=?, note=? WHERE id=?", (name, phone, email, (note or "").strip(), int(customer_id)))
            return int(customer_id), None
        cid = c.execute("INSERT INTO customers(name, phone, email, note, created_at, created_by) VALUES(?,?,?,?,?,?) RETURNING id",
                        (name, phone, email, (note or "").strip(), now(), st.session_state.user)).fetchone()["id"]
    return int(cid), None


def save_customer_email(customer_id, email):
    """Add or change the email address of a customer (the CEO or the Receptionist)."""
    if not has(CEO, RECEPTIONIST):
        return "You cannot change the customer's email."
    email = (email or "").strip()
    if not email or not EMAIL_RE.match(email):
        return "The email address is not valid."
    with conn() as c:
        c.execute("UPDATE customers SET email=? WHERE id=?", (email, int(customer_id)))
    return None
