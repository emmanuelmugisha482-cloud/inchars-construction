"""Registering a job: tap and fill. Only the customer's NAME is required. The email (to keep the customer informed) and the phone are welcome but optional.
The job goes to the CEO, and the customer receives a thank-you email when there is an address. Documents are optional."""

from datetime import date, timedelta

import streamlit as st

from inchars.components.common import K, done
from inchars.services.config import PAY_METHODS, SERVICES
from inchars.accounts import smtp_conf
from inchars.services.core import load_customers, missing_info, save_customer, update_customer_info
from inchars.services.files import add_files
from inchars.services.ui import due_pick, pick
from inchars.services.workflow import create_job


def new_job_form():
    cust = load_customers()
    st.caption("Register the job when the customer comes. It goes to the CEO. Give the customer the job number: it is needed to collect the document.")
    mode = pick("Customer", ["Existing customer", "New customer"], K("nj_mode"), default="Existing customer" if len(cust) else "New customer") or "New customer"
    cid, new, extra, email = None, {}, {}, ""
    if mode == "Existing customer":
        labels = {f"{r.Name} · {r.Email or 'no email'}" + ("" if r.Phone else " · no phone"): int(r.id) for r in cust.itertuples()}
        chosen = st.selectbox("Search by name or phone", list(labels), index=None, placeholder="Type to search, then choose", key=K("nj_c"))
        cid = labels.get(chosen)
        if cid:
            row = cust[cust.id == cid].iloc[0]
            email = row.Email or ""
            lacking = missing_info(row.Phone, row.Email)
            if lacking:  # the 'information we do not have' bar: optional
                st.info("Information we do not have: " + " and ".join(l for _, l in lacking) + ". Not required: add it now if the customer is here.")
                cols = st.columns(len(lacking))
                for (col, label), box in zip(lacking, cols):
                    extra[col] = box.text_input(label.capitalize(), key=K(f"nj_x{col}"), placeholder="0788 123 456" if col == "phone" else "name@example.com")
    else:
        a, b = st.columns(2)
        new = {"name": a.text_input("Name", key=K("nj_n"), placeholder="Family name first, for example MUKAMWIZA Isabelle"),
               "email": b.text_input("Email (optional)", key=K("nj_e"), placeholder="name@example.com"),
               "phone": a.text_input("Phone number (optional)", key=K("nj_p"), placeholder="0788 123 456")}
        email = new["email"]
        st.caption("With an email address we keep the customer informed by email. Without it we can add it later.")
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
            cid, err = save_customer(new["name"], new["phone"], new["email"])
            if err:
                return st.error(err)
        if not cid:
            return st.warning("Choose the customer.")
        if any((v or "").strip() for v in extra.values()):  # the optional bar was filled in
            err = update_customer_info(cid, extra.get("phone", ""), extra.get("email", ""))
            if err:
                return st.error(err)
            email = extra.get("email") or email
        jid, job_no, err = create_job(cid, service, title, "", fee, due, deposit, method, "")
        if jid is None:
            return st.error(err)
        problem = add_files(jid, "Customer document", [(u.name, u.getvalue()) for u in (up or [])]) if up else None
        mail = ("The customer was emailed a thank-you." if email.strip() and smtp_conf()
                else "No email was sent: " + ("there is no email address yet (you can add it in the job)." if not email.strip() else "email is not set up yet."))
        done(f"Job {job_no} registered and sent to the CEO. Give the number {job_no} to the customer. {mail}" + (f" {problem}" if problem else ""))
