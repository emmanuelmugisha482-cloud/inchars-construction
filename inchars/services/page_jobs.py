"""The Jobs page: four tabs. Jobs (search, tap a job), New job, Inbox (notifications) and Payments."""

from datetime import date

import pandas as pd
import streamlit as st

from inchars.accounts import has
from inchars.components.common import K, pretty, search_box, to_excel, wide, match
from inchars.config import ACCOUNTANT, CEO, RECEPTIONIST, XLSX
from inchars.services.config import OPEN_STATUSES, SERVICES
from inchars.services.jobs import load_jobs
from inchars.services.notices import email_state, load_notices, mark_notices_read, unread_count
from inchars.services.rules import sees_all_jobs
from inchars.services.ui import pick, run_section
from inchars.styles import DASH_CSS

CHIPS = ["Open", "Needs me", "Ready", "Waiting", "New", "No email", "All"]


def needs_me(jobs):
    """The jobs that need this person's action now."""
    if has(CEO):
        return (jobs.status == "New") | jobs.overdue
    if has(RECEPTIONIST):
        return jobs.status.isin(["Ready", "Waiting for customer"]) | ((jobs.customer_email == "") & jobs.status.isin(OPEN_STATUSES))
    if has(ACCOUNTANT):
        return (jobs.balance > 0) & jobs.status.isin(["Ready", "Collected"])
    return jobs.status.isin(["Assigned", "In progress", "Waiting for customer"])  # the Engineer


def apply_chip(jobs, chip):
    if chip == "Open":
        return jobs[jobs.status.isin(OPEN_STATUSES)]
    if chip == "Needs me":
        return jobs[needs_me(jobs)]
    if chip == "Ready":
        return jobs[jobs.status == "Ready"]
    if chip == "Waiting":
        return jobs[jobs.status == "Waiting for customer"]
    if chip == "New":
        return jobs[jobs.status == "New"]
    if chip == "No email":
        return jobs[(jobs.customer_email == "") & jobs.status.isin(OPEN_STATUSES)]
    return jobs


def summary_line(jobs):
    n = lambda mask: int(mask.sum())
    parts = [f"{n(jobs.status.isin(OPEN_STATUSES))} open", f"{n(jobs.status == 'New')} new", f"{n(jobs.status == 'Ready')} ready",
             f"{n(jobs.status == 'Waiting for customer')} waiting for customer", f"{n(jobs.overdue)} overdue"]
    noemail = n((jobs.customer_email == "") & jobs.status.isin(OPEN_STATUSES))
    if noemail:
        parts.append(f"{noemail} customer(s) without email")
    return " · ".join(parts)


def table_of(view, money):
    t = view[["job_no", "customer", "title", "status"]].rename(columns={"job_no": "Job no", "customer": "Customer", "title": "Title", "status": "Status"}).copy()
    t["Info"] = [(("Overdue" if o else "") + (" · " if o and s else "") + s) for o, s in zip(view.overdue, view.stage)]
    t["Engineer"] = view.assigned_to.values
    t["Promised"] = view.due_on.values
    if money:
        t["Balance"] = view.balance.values
    t["Email"] = ["" if e else "none" for e in view.customer_email]
    t.insert(0, "No", range(1, len(t) + 1))
    return t


def selected_row(table, view, key):
    """Draw the table; return the position of the tapped row (a row that is no longer in the list is ignored)."""
    styled = pretty(table, ["Balance"], "Status")
    try:
        ev = wide(st.dataframe, styled, hide_index=True, on_select="rerun", selection_mode="single-row", key=key)
        rows = [r for r in (ev.selection.rows if ev is not None else []) if r < len(view)]
    except TypeError:  # an older Streamlit without row selection
        wide(st.dataframe, styled, hide_index=True)
        rows = []
    return rows[0] if rows else None


def jobs_tab():
    jobs = load_jobs()
    if jobs.empty:
        return st.info("There are no jobs yet." if sees_all_jobs() else "No job has been given to you yet.")
    money = has(CEO, ACCOUNTANT, RECEPTIONIST)
    st.caption(summary_line(jobs))
    q = search_box("jb_q", "Search", "Job number, customer, phone, email, title, engineer…")
    chip = pick("Show", CHIPS, "jb_chip", default="Open") or "Open"
    service = pick("Service", ["All services"] + SERVICES, "jb_service", default="All services") or "All services"
    view = apply_chip(jobs, chip)
    if service != "All services":
        view = view[view.service == service]
    view = match(view, ["job_no", "customer", "phone", "customer_email", "title", "assigned_to", "status", "stage"], q).reset_index(drop=True)
    if q.strip() and len(view) == 1:  # one match: open it at once (once: Close still works afterwards)
        only = int(view.id.iloc[0])
        if st.session_state.get("jb_last_auto") != only:
            st.session_state.jb_last_auto = only
            st.session_state.jb_open = only
    else:
        st.session_state.jb_last_auto = None
    table = table_of(view, money)
    st.caption(f"{len(table)} job(s). Tap a row to open the job.")
    pos = selected_row(table, view, "jobs_tbl")
    tapped = int(view.id.iloc[pos]) if pos is not None else None
    if tapped != st.session_state.get("jb_last_tap"):  # a row stays selected after Close: only a NEW tap opens a job
        st.session_state.jb_last_tap = tapped
        if tapped is not None:
            st.session_state.jb_open = tapped
    if len(table):
        st.download_button(":material/download: Download Excel", to_excel({"Jobs": table}), f"jobs_{date.today()}.xlsx", mime=XLSX, key="dl_jobs")
    jid = st.session_state.get("jb_open")
    if jid and jid in set(jobs.id.astype(int)):
        run_section("job_panel", "job_panel", jid, "jb")


def inbox_tab():
    st.caption(email_state())
    n = load_notices()
    if n.empty:
        return st.caption("No notifications yet.")
    unread = n.read_at.isna()
    shown = n.assign(New=["new" if u else "" for u in unread])[["New", "When", "Job", "Message"]]
    shown["When"] = shown["When"].astype(str).str.replace("T", " ")
    st.caption("Tap a notification to open its job.")
    try:
        ev = wide(st.dataframe, shown, hide_index=True, on_select="rerun", selection_mode="single-row", key="inbox_tbl")
        rows = [r for r in (ev.selection.rows if ev is not None else []) if r < len(n)]
    except TypeError:
        wide(st.dataframe, shown, hide_index=True)
        rows = []
    tapped = int(n.job_id.iloc[rows[0]]) if rows and pd.notna(n.job_id.iloc[rows[0]]) else None
    if tapped != st.session_state.get("ib_last_tap"):  # only a NEW tap opens a job (Close must keep working)
        st.session_state.ib_last_tap = tapped
        if tapped is not None:
            st.session_state.ib_open = tapped
    if unread.any() and st.button("Mark all as read", key="mark_read"):
        mark_notices_read()
        st.rerun()
    jid = st.session_state.get("ib_open")
    if jid and jid in set(load_jobs().id.astype(int)):
        run_section("job_panel", "job_panel", jid, "ib")


def jobs_page():
    st.markdown(DASH_CSS, unsafe_allow_html=True)
    n = unread_count()
    inbox = f"Inbox ({n})" if n else "Inbox"
    names = ["Jobs"] + (["New job"] if has(CEO, RECEPTIONIST) else []) + [inbox] + (["Payments"] if has(CEO, ACCOUNTANT, RECEPTIONIST) else [])
    tabs = dict(zip(names, st.tabs(names)))
    with tabs["Jobs"]:
        jobs_tab()
    if "New job" in tabs:
        with tabs["New job"]:
            run_section("new_job", "new_job_form")
    with tabs[inbox]:
        inbox_tab()
    if "Payments" in tabs:
        with tabs["Payments"]:
            run_section("payments_page", "payments_tab")
