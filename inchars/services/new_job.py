"""Registering a job: tap and fill, the customer's email is asked for (so he or she can be informed). Documents are optional."""

from datetime import date, timedelta

import streamlit as st

from inchars.components.common import K, done
from inchars.services.config import PAY_METHODS, SERVICES
from inchars.services.customers import load_customers, save_customer
from inchars.services.files import add_files
from inchars.services.jobs import create_job
from inchars.services.ui import due_pick, pick


def new_job_form():
    cust = load_customers()
    st.caption("Register the job when the customer comes. Give the customer the job number: it is needed to collect the document.")
    mode = pick("Customer", ["Existing customer", "New customer"], K("nj_mode"), default="Existing customer" if len(cust) else "New customer") or "New customer"
    cid, new, no_email = None, {}, False
    if mode == "Existing customer":
        labels = {f"{r.Name} · {r.Phone}" + ("" if r.Email else " · no email"): int(r.id) for r in cust.itertuples()}
        chosen = st.selectbox("Search by name or phone", list(labels), index=None, placeholder="Type to search, then choose", key=K("nj_c"))
        cid = labels.get(chosen)
        if chosen and "no email" in chosen:
            st.warning("This customer has no email address. Add it in the job after registering, so he or she receives our updates.")
    else:
        a, b = st.columns(2)
        new = {"name": a.text_input("Name", key=K("nj_n")), "phone": b.text_input("Phone", key=K("nj_p")),
               "email": a.text_input("Email (the customer gets our updates here)", key=K("nj_e"), placeholder="name@example.com")}
        no_email = b.checkbox("This customer has no email", key=K("nj_ne"))
    service = pick("Service", SERVICES, K("nj_s"), default=SERVICES[0])
    a, b = st.columns([3, 2])
    title = a.text_input("Title", placeholder="For example: 3-bedroom house design, Kicukiro", key=K("nj_t"))
    fee = b.number_input("Fee (Frw)", min_value=0, step=1000, value=None, placeholder="Agreed fee", key=K("nj_f"))
    due = due_pick(K("nj_due"), date.today() + timedelta(days=7))
    a, b = st.columns(2)
    deposit = a.number_input("Deposit paid now (Frw, optional)", min_value=0, step=1000, value=None, placeholder="0", key=K("nj_dep"))
    method = pick("Deposit paid by", PAY_METHODS, K("nj_m"), default="Cash") or "Cash"
    with st.expander("Add the customer's documents (optional)"):
        up = st.file_uploader("PDF, JPG or PNG", type=["pdf", "jpg", "jpeg", "png"], accept_multiple_files=True, key=K("nj_u"))
    if st.button("Register the job", type="primary", key=K("nj_go")):
        if mode == "New customer":
            cid, err = save_customer(new["name"], new["phone"], new["email"], "", None, no_email)
            if err:
                return st.error(err)
        if not cid:
            return st.warning("Choose the customer.")
        jid, job_no, err = create_job(cid, service, title, "", fee, due, deposit, method, "")
        if jid is None:
            return st.error(err)
        problem = add_files(jid, "Customer document", [(u.name, u.getvalue()) for u in (up or [])]) if up else None
        done(f"Job {job_no} registered. Give the number {job_no} to the customer. The CEO was told and the customer got a thank-you email." + (f" {problem}" if problem else ""))
