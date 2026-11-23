"""The Jobs page: tabs Jobs (a list like a register book: date, name, service, engineer, status), New job, Inbox and Payments. Each tab runs on its own: if one is broken, the others work.
This file draws the page shell and the list of jobs; the other tabs are in new_job.py, notify.py (Inbox) and money.py (Payments)."""

import html
from datetime import date, datetime

import pandas as pd
import streamlit as st

from inchars.accounts import has
from inchars.components.common import match, search_box, to_excel
from inchars.config import CEO, RECEPTIONIST, XLSX
from inchars.services.config import OPEN_STATUSES, SERVICES
from inchars.services.core import can, load_jobs, sees_all_jobs, sees_money
from inchars.services.ui import pick, run_section
from inchars.styles import DASH_CSS

CHIPS = ["Open", "Needs me", "With CEO", "In review", "Reverted", "Ready", "Waiting", "Missing info", "All"]
PAGE_SIZE = 15
# the coloured label of each status (the same look as the picture: a small rounded label)
PILL = {"With CEO": ("#e0922f", ""), "With engineer": ("#2f6fb0", ""), "Waiting for customer": ("#b5527f", ""), "In review": ("#7a5fc4", ""),
        "Reverted": ("#f0605d", "\u2715 "), "Ready": ("#2e9b57", "\u2713 "), "Collected": ("#7b8794", ""), "Cancelled": ("#9aa3ad", "")}
ROW_CSS = """<style>
.jr-h{font-size:.78rem;color:#6b7785;font-weight:600;padding:6px 0;border-bottom:2px solid #cfe9f1}
.jr{font-size:.92rem;padding:7px 0;border-bottom:1px solid #d9eef4;line-height:1.25}
.jr small{color:#8a96a3}
.pill{display:inline-block;color:#fff;border-radius:14px;padding:3px 12px;font-size:.8rem;font-weight:600;white-space:nowrap}
</style>"""
WIDTHS = [2.1, 1.9, 1.9, 1.7, 2.3, 2.0, 0.55, 0.55]


def needs_me(jobs):
    """The jobs that need this person's action now."""
    if has(CEO):
        return jobs.status.isin(["With CEO", "In review"]) | jobs.overdue
    if has(RECEPTIONIST):
        return jobs.status.isin(["Ready", "Waiting for customer"])
    return jobs.status.isin(["With engineer", "Reverted"])  # the Engineer


def apply_chip(jobs, chip):
    if chip == "Open":
        return jobs[jobs.status.isin(OPEN_STATUSES)]
    if chip == "Needs me":
        return jobs[needs_me(jobs)]
    if chip == "Waiting":
        return jobs[jobs.status == "Waiting for customer"]
    if chip == "Missing info":
        return jobs[(jobs.missing != "") & jobs.status.isin(OPEN_STATUSES)]
    if chip == "All":
        return jobs
    return jobs[jobs.status == chip]


def summary_line(jobs):
    n = lambda mask: int(mask.sum())
    parts = [f"{n(jobs.status.isin(OPEN_STATUSES))} open", f"{n(jobs.status == 'With CEO')} with the CEO", f"{n(jobs.status == 'In review')} waiting for the CEO's check",
             f"{n(jobs.status == 'Reverted')} sent back", f"{n(jobs.status == 'Ready')} ready", f"{n(jobs.overdue)} overdue"]
    return " \u00b7 ".join(parts)


def when(iso):
    """2026-11-23T10:15:00 -> 23/11/2026 10:15:00"""
    try:
        return datetime.fromisoformat(str(iso)).strftime("%d/%m/%Y %H:%M:%S")
    except ValueError:
        return str(iso)


def pill(status):
    color, mark = PILL.get(status, ("#7b8794", ""))
    return f'<span class="pill" style="background:{color}">{mark}{html.escape(status)}</span>'


def table_of(view, money):
    """The same rows as the list, for the Excel download."""
    t = pd.DataFrame({"Date": [when(d) for d in view.created_at], "Job no": view.job_no.values, "Family name": view.surname.values, "First name": view.firstname.values,
                      "Service": view.service.values, "Title": view.title.values, "Engineer": [e or "N/A" for e in view.assigned_to], "Status": view.status.values})
    if money:
        t["Balance"] = view.balance.values
    t["Missing info"] = view.missing.values
    t.insert(0, "No", range(1, len(t) + 1))
    return t


def job_rows(page_view, p="jb"):
    """The list: one line per job like a register book. The eye opens the job, the pencil lets you change it (only if you may)."""
    st.markdown(ROW_CSS, unsafe_allow_html=True)
    head = st.columns(WIDTHS)
    for col, label in zip(head, ["Date", "Family name", "First name", "Service", "Engineer", "Status", "", ""]):
        col.markdown(f'<div class="jr-h">{label}</div>', unsafe_allow_html=True)
    for r in page_view.itertuples():
        cells = st.columns(WIDTHS, vertical_alignment="center")
        cells[0].markdown(f'<div class="jr">{when(r.created_at)}</div>', unsafe_allow_html=True)
        cells[1].markdown(f'<div class="jr"><b>{html.escape(r.surname.upper())}</b></div>', unsafe_allow_html=True)
        hint = "<br><small>no email</small>" if not r.customer_email else ""
        cells[2].markdown(f'<div class="jr">{html.escape(r.firstname)}{hint}</div>', unsafe_allow_html=True)
        cells[3].markdown(f'<div class="jr">{html.escape(r.service)}</div>', unsafe_allow_html=True)
        cells[4].markdown(f'<div class="jr">{html.escape(r.assigned_to) if r.assigned_to else "N/A"}</div>', unsafe_allow_html=True)
        cells[5].markdown(f'<div class="jr">{pill(r.status)}</div>', unsafe_allow_html=True)
        if cells[6].button(":material/visibility:", key=f"{p}_eye{r.id}", help="Open this job"):
            st.session_state[p + "_open"], st.session_state[p + "_part"] = int(r.id), "Work"
            st.session_state[p + "_ver"] = st.session_state.get(p + "_ver", 0) + 1
        if can("edit", {"status": r.status, "assigned_to": r.assigned_to, "service": r.service}) and cells[7].button(":material/edit:", key=f"{p}_pen{r.id}", help="Change this job"):
            st.session_state[p + "_open"], st.session_state[p + "_part"] = int(r.id), "Details"
            st.session_state[p + "_ver"] = st.session_state.get(p + "_ver", 0) + 1


def jobs_tab():
    jobs = load_jobs()
    if jobs.empty:
        return st.info("There are no jobs yet." if sees_all_jobs() else "No job has been given to you yet.")
    money = sees_money()
    st.caption(summary_line(jobs))
    q = search_box("jb_q", "Search", "Job number, name, email, phone, title, engineer…")
    chip = pick("Show", CHIPS, "jb_chip", default="Open") or "Open"
    service = pick("Service", ["All services"] + SERVICES, "jb_service", default="All services") or "All services"
    view = apply_chip(jobs, chip)
    if service != "All services":
        view = view[view.service == service]
    view = match(view, ["job_no", "customer", "phone", "customer_email", "title", "assigned_to", "status", "stage"], q).reset_index(drop=True)
    sig = (q, chip, service)
    if st.session_state.get("jb_sig") != sig:  # a new search or filter starts on the first page
        st.session_state.jb_sig, st.session_state.jb_page = sig, 0
    pages = max(1, -(-len(view) // PAGE_SIZE))
    page = min(st.session_state.get("jb_page", 0), pages - 1)
    st.caption(f"{len(view)} job(s). The eye opens a job" + (", the pencil changes it." if has(CEO, RECEPTIONIST) else "."))
    job_rows(view.iloc[page * PAGE_SIZE:(page + 1) * PAGE_SIZE])
    if pages > 1:
        a, b, c = st.columns([1, 3, 1])
        if a.button("\u2039 Previous", key="jb_prev", disabled=page == 0):
            st.session_state.jb_page = page - 1
            st.rerun()
        b.markdown(f'<div style="text-align:center;padding-top:6px">Page {page + 1} of {pages}</div>', unsafe_allow_html=True)
        if c.button("Next \u203a", key="jb_next", disabled=page >= pages - 1):
            st.session_state.jb_page = page + 1
            st.rerun()
    if len(view):
        st.download_button(":material/download: Download Excel", to_excel({"Jobs": table_of(view, money)}), f"jobs_{date.today()}.xlsx", mime=XLSX, key="dl_jobs")
    jid = st.session_state.get("jb_open")
    if jid and jid in set(jobs.id.astype(int)):
        run_section("panel", "job_panel", jid, "jb")


def _unread():
    """The number on the Inbox tab. If the Inbox file is broken the tab simply shows no number."""
    try:
        from inchars.services.notify import unread_count
        return unread_count()
    except Exception:  # noqa: BLE001
        return 0


def jobs_page():
    st.markdown(DASH_CSS, unsafe_allow_html=True)
    n = _unread()
    inbox = f"Inbox ({n})" if n else "Inbox"
    names = ["Jobs"] + (["New job"] if has(CEO, RECEPTIONIST) else []) + [inbox] + (["Payments"] if sees_money() else [])
    tabs = dict(zip(names, st.tabs(names)))
    with tabs["Jobs"]:
        run_section("page_jobs", "jobs_tab")
    if "New job" in tabs:
        with tabs["New job"]:
            run_section("new_job", "new_job_form")
    with tabs[inbox]:
        run_section("notify", "inbox_tab")
    if "Payments" in tabs:
        with tabs["Payments"]:
            run_section("money", "payments_tab")
