"""Jobs page: notifications, the list of jobs with the open job below it, registering a job, customers and payments."""

from datetime import date, timedelta

import streamlit as st

from inchars.accounts import has
from inchars.components.common import K, done, match, pretty, search_box, to_excel, wide
from inchars.components.kpi import kpi, kpi_grid
from inchars.config import ACCOUNTANT, CEO, JOB_STATUSES, OPEN_STATUSES, PAY_METHODS, RECEPTIONIST, SERVICES, XLSX
from inchars.data.jobs import (create_job, email_state, load_customers, load_jobs, load_notices, mark_notices_read, save_customer,
                               sees_all_jobs, unread_count)
from inchars.pages.customers import customers_tab
from inchars.pages.job_detail import job_detail
from inchars.pages.job_payments import payments_tab
from inchars.styles import DASH_CSS


def attention(jobs):
    """The jobs that need this person's action now."""
    if has(CEO):
        return (jobs.status == "New") | jobs.overdue
    if has(RECEPTIONIST):
        return jobs.status.isin(["Ready", "Waiting for customer"])
    if has(ACCOUNTANT):
        return (jobs.balance > 0) & jobs.status.isin(["Ready", "Collected"])
    return jobs.status.isin(["Assigned", "In progress", "Waiting for customer"])  # the Engineer


def notices_panel():
    st.caption(email_state())
    n = load_notices()
    if n.empty:
        return st.caption("No notifications yet.")
    unread = n.read_at.isna()
    shown = n.assign(New=["new" if u else "" for u in unread])[["New", "When", "Job", "Message"]]
    shown["When"] = shown["When"].astype(str).str.replace("T", " ")
    wide(st.dataframe, shown, hide_index=True)
    if unread.any() and st.button("Mark all as read", key="mark_read"):
        mark_notices_read()
        st.rerun()


def jobs_tab():
    jobs = load_jobs()
    if jobs.empty:
        return st.info("There are no jobs yet." if sees_all_jobs() else "No job has been given to you yet.")
    money = has(CEO, ACCOUNTANT, RECEPTIONIST)
    counts = {s: int((jobs.status == s).sum()) for s in ("New", "Assigned", "In progress", "Waiting for customer", "Ready")}
    late = int(jobs.overdue.sum())
    kpi_grid([kpi("New", counts["New"], "waiting for an engineer", "amber" if counts["New"] else "grey"),
              kpi("Assigned", counts["Assigned"], "given, not started", "blue"),
              kpi("In progress", counts["In progress"], "being worked on", "blue"),
              kpi("Waiting for customer", counts["Waiting for customer"], "needs something from the customer", "amber" if counts["Waiting for customer"] else "grey"),
              kpi("Ready", counts["Ready"], "finished, to hand over", "green"),
              kpi("Overdue", late, "past the promised date", "red" if late else "grey")])
    a, b, c, r = st.columns([2, 2, 3, 1])
    show = a.selectbox("Show", ["Open jobs", "Needs my attention", "All jobs"] + JOB_STATUSES, key="jb_show")
    service = b.selectbox("Service", ["All services"] + SERVICES, key="jb_service")
    with c:
        q = search_box("jb_q", "Search job number, customer, phone or title", "Type part of a word…")
    r.markdown("<div style='height:1.9rem'></div>", unsafe_allow_html=True)
    r.button(":material/refresh: Refresh", key="jb_refresh")  # the page shows the latest statuses every time it is drawn
    n_att = int(attention(jobs).sum())
    if n_att:
        st.caption(f"{n_att} job(s) need your attention now (choose 'Needs my attention' in Show).")
    view = jobs
    if show == "Needs my attention":
        view = view[attention(view)]
    elif show == "Open jobs":
        view = view[view.status.isin(OPEN_STATUSES)]
    elif show != "All jobs":
        view = view[view.status == show]
    if service != "All services":
        view = view[view.service == service]
    view = match(view, ["job_no", "customer", "phone", "title", "assigned_to"], q).reset_index(drop=True)
    cols = {"job_no": "Job no", "created_at": "Registered", "customer": "Customer", "phone": "Phone", "service": "Service", "title": "Title",
            "status": "Status", "stage": "Stage", "assigned_to": "Engineer", "due_on": "Promised"}
    if money:
        cols.update({"fee": "Fee", "paid": "Paid", "balance": "Balance"})
    table = view[list(cols)].rename(columns=cols)
    table["Registered"] = table["Registered"].astype(str).str[:10]
    table["Late"] = ["Overdue" if o else "" for o in view.overdue]
    table.insert(0, "No", range(1, len(table) + 1))
    st.caption(f"{len(table)} job(s). Click a row to open the job, or choose it in the list below.")
    rows = []
    styled = pretty(table, ["Fee", "Paid", "Balance"], "Status")
    try:
        ev = wide(st.dataframe, styled, hide_index=True, on_select="rerun", selection_mode="single-row", key="jobs_tbl")
        rows = list(ev.selection.rows) if ev is not None else []
    except TypeError:  # an older Streamlit without row selection: the list below is used
        wide(st.dataframe, styled, hide_index=True)
    if len(table):
        st.download_button(":material/download: Download Excel", to_excel({"Jobs": table}), f"jobs_{date.today()}.xlsx", mime=XLSX, key="dl_jobs")
    labels = {f"{r.job_no} · {r.customer} · {r.title} · {r.status}": int(r.id) for r in view.itertuples()}
    chosen = st.selectbox("Open a job", list(labels), index=None, placeholder="Search and choose a job", key=K("jb_pick"))
    rows = [r_ for r_ in rows if r_ < len(view)]  # a row that is no longer in the list (the list changed) is ignored
    if rows:
        st.session_state.job_open = int(view.id.iloc[rows[0]])
    elif chosen:
        st.session_state.job_open = labels[chosen]
    jid = st.session_state.get("job_open")
    if jid and jid in set(jobs.id.astype(int)):
        st.divider()
        if st.button("Close this job", key="jb_close"):
            st.session_state.job_open = None
            st.rerun()
        job_detail(jid)


def new_job_tab():
    cust = load_customers()
    st.caption("Register the job when the customer comes. Give the customer the job number: they bring it back to collect the document.")
    mode = st.radio("Customer", ["Existing customer", "New customer"], horizontal=True, key=K("nj_mode"), index=0 if len(cust) else 1)
    cid, new = None, {}
    if mode == "Existing customer":
        labels = {f"{r.Name} · {r.Phone}": int(r.id) for r in cust.itertuples()}
        pick = st.selectbox("Search by name or phone", list(labels), index=None, placeholder="Choose the customer", key=K("nj_c"))
        cid = labels.get(pick)
    else:
        a, b = st.columns(2)
        new = {"name": a.text_input("Name", key=K("nj_n")), "phone": b.text_input("Phone", key=K("nj_p")),
               "email": a.text_input("Email (the customer gets progress updates here)", key=K("nj_e")), "note": b.text_input("Note (optional)", key=K("nj_o"))}
    a, b = st.columns(2)
    service = a.selectbox("Service", SERVICES, key=K("nj_s"))
    title = b.text_input("Title", placeholder="For example: 3-bedroom house design, Kicukiro", key=K("nj_t"))
    desc = st.text_area("Description (optional)", key=K("nj_d"))
    a, b, c = st.columns(3)
    fee = a.number_input("Fee (Frw)", min_value=0, step=1000, value=None, placeholder="Agreed fee", key=K("nj_f"))
    due = b.date_input("Promised date", date.today() + timedelta(days=7), min_value=date.today(), key=K("nj_due"))
    deposit = c.number_input("Deposit paid now (Frw)", min_value=0, step=1000, value=None, placeholder="0", key=K("nj_dep"))
    d, e = st.columns(2)
    method = d.selectbox("Deposit paid by", PAY_METHODS, key=K("nj_m"))
    ref = e.text_input("Deposit reference (optional)", key=K("nj_r"))
    up = st.file_uploader("Documents from the customer (PDF, JPG, PNG)", type=["pdf", "jpg", "jpeg", "png"], accept_multiple_files=True, key=K("nj_u"))
    if st.button("Register the job", type="primary", key=K("nj_go")):
        if mode == "New customer":
            cid, err = save_customer(new["name"], new["phone"], new["email"], new["note"])
            if err:
                return st.error(err)
        if not cid:
            return st.warning("Choose the customer.")
        job_no, err = create_job(cid, service, title, desc, fee, due, deposit, method, ref, [(u.name, u.getvalue()) for u in (up or [])])
        if job_no is None:
            return st.error(err)
        done(f"Job {job_no} registered. The CEO was told. Give the number {job_no} to the customer." + (f" {err}" if err else ""))


def jobs_page():
    st.markdown(DASH_CSS, unsafe_allow_html=True)
    n = unread_count()
    with st.expander(f"Notifications ({n} new)" if n else "Notifications", expanded=bool(n)):
        notices_panel()
    names = ["Jobs"]
    if has(CEO, RECEPTIONIST):
        names += ["New job", "Customers"]
    if has(CEO, ACCOUNTANT, RECEPTIONIST):
        names.append("Payments")
    tabs = dict(zip(names, st.tabs(names)))
    with tabs["Jobs"]:
        jobs_tab()
    if "New job" in tabs:
        with tabs["New job"]:
            new_job_tab()
        with tabs["Customers"]:
            customers_tab()
    if "Payments" in tabs:
        with tabs["Payments"]:
            payments_tab()
