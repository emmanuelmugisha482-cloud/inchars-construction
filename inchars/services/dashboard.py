"""Dashboards for services: the landing page of the Receptionist and Engineer, and the Services tab of the CEO and Accountant."""

import html
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from inchars.accounts import has
from inchars.components.charts import card, c_bar, c_donut, c_grouped, month_label
from inchars.components.common import money
from inchars.components.kpi import AMBER, GREEN, RED, insights_html, kpi, kpi_grid, monthly_vals, spark_svg, trend
from inchars.config import CEO, ENGINEER, RECEPTIONIST
from inchars.services.config import OPEN_STATUSES
from inchars.services.core import load_jobs, sees_all_jobs
from inchars.services.money import load_payments
from inchars.services.notify import unread_count
from inchars.styles import DASH_CSS

STATUS_COLORS = {"With CEO": "#e0922f", "With engineer": "#2f6fb0", "Waiting for customer": "#b5527f", "In review": "#7a5fc4", "Reverted": "#f0605d", "Ready": "#2e9b57", "Collected": "#7b8794", "Cancelled": "#9aa3ad"}


def _last_months(n=6):
    out, cur = [], date.today().replace(day=1)
    for _ in range(n):
        out.insert(0, str(cur)[:7])
        cur = (cur - timedelta(days=1)).replace(day=1)
    return out


def _who():
    if has(CEO):
        return "boss"
    return "reception" if has(RECEPTIONIST) else "engineer"


def service_cards(jobs, pays, who):
    month, months = str(date.today())[:7], _last_months()
    labs = [month_label(m, False) for m in months]
    open_ = jobs[jobs.status.isin(OPEN_STATUSES)]
    ready, waiting = jobs[jobs.status == "Ready"], jobs[jobs.status == "Waiting for customer"]
    unpaid = jobs[(jobs.status != "Cancelled") & (jobs.balance > 0)]
    overdue = int(jobs.overdue.sum())
    reg = jobs.assign(Date=jobs.created_at.astype(str), One=1)
    reg_m = monthly_vals(reg, "One", months)
    got_month = float(pays[pays["date"].astype(str).str[:7] == month].amount.sum()) if len(pays) else 0.0
    coll_month = int((jobs.collected_at.astype(str).str[:7] == month).sum())
    if who == "boss":
        return [kpi("Open jobs", len(open_), "not collected yet", "blue", spark_svg(reg_m, labs, "#2f6fb0"), trend(reg_m[-1], reg_m[-2], labs[-2], lambda v: f"{v:,.0f}")),
                kpi("Needs your decision", int(jobs.status.isin(["With CEO", "In review"]).sum()), "to assign, or to check", "amber" if jobs.status.isin(["With CEO", "In review"]).any() else "green"),
                kpi("Ready for the customer", len(ready), "document finished", "green"),
                kpi("Overdue", overdue, "past the promised date", "red" if overdue else "green"),
                kpi("Still to pay", money(unpaid.balance.sum()) if len(unpaid) else "0", f"Frw · {len(unpaid)} job(s)", "red" if len(unpaid) else "green"),
                kpi("Received this month", money(got_month), "Frw for services", "green")]
    if who == "reception":
        return [kpi("Open jobs", len(open_), "not collected yet", "blue", spark_svg(reg_m, labs, "#2f6fb0"), trend(reg_m[-1], reg_m[-2], labs[-2], lambda v: f"{v:,.0f}")),
                kpi("Ready to hand over", len(ready), f"{int((ready.balance > 0).sum())} not paid in full", "green" if not (ready.balance > 0).any() else "amber"),
                kpi("Waiting for customer", len(waiting), "an engineer needs something", "amber" if len(waiting) else "green"),
                kpi("Still to pay", money(unpaid.balance.sum()) if len(unpaid) else "0", f"Frw · {len(unpaid)} job(s)", "red" if len(unpaid) else "green"),
                kpi("Registered this month", int(reg_m[-1]), "new jobs", "blue"),
                kpi("Collected this month", coll_month, "documents handed over", "grey")]
    todo = int(jobs.status.isin(["With engineer", "Reverted"]).sum())
    finished = int((jobs.ready_at.astype(str).str[:7] == month).sum())
    return [kpi("My open jobs", len(open_), "not collected yet", "blue"),
            kpi("To do", todo, "given to you, or sent back to you", "amber" if todo else "green"),
            kpi("Waiting for customer", len(waiting), "you asked the customer for something", "amber" if len(waiting) else "green"),
            kpi("Overdue", overdue, "past the promised date", "red" if overdue else "green"),
            kpi("Ready for the customer", len(ready), "finished, waiting to be collected", "green"),
            kpi("Finished this month", finished, "marked ready", "grey")]


def service_says(jobs, who):
    says = []
    new = int((jobs.status == "With CEO").sum())
    if who == "boss" and new:
        says.append((AMBER, f"<b>To assign:</b> {new} job(s) are waiting for your decision: give to an engineer, or send back."))
    overdue = jobs[jobs.overdue]
    if len(overdue):
        says.append((RED, f"<b>Overdue:</b> {len(overdue)} job(s) are past the promised date ({html.escape(', '.join(overdue.job_no.head(3)))}{'…' if len(overdue) > 3 else ''})."))
    ready = jobs[jobs.status == "Ready"]
    if len(ready):
        unpaid = ready[ready.balance > 0]
        if who != "engineer" and len(unpaid):
            says.append((AMBER, f"<b>Ready but not paid:</b> {len(unpaid)} finished job(s) cannot be handed over before payment ({money(unpaid.balance.sum())} Frw)."))
        else:
            says.append((GREEN, f"<b>Ready:</b> {len(ready)} finished job(s) are waiting for the customer."))
    wait = jobs[jobs.status == "Waiting for customer"]
    if len(wait):
        says.append((AMBER, f"<b>Waiting for customer:</b> {len(wait)} job(s) cannot continue until the customer gives what was asked."))
    n = unread_count()
    if n:
        says.append(("#2f6fb0", f"<b>Notifications:</b> you have {n} new message(s) on the Jobs page."))
    return says


def services_tab(who=None):
    who = who or _who()
    if not has(CEO, RECEPTIONIST, ENGINEER):
        return st.info("The customers' jobs are handled by the CEO, the Receptionist and the Engineers.")
    jobs = load_jobs()
    pays = load_payments() if sees_all_jobs() else pd.DataFrame(columns=["date", "amount"])
    st.markdown('<div class="dsec">Services</div><div class="dcap">Consultancy, house design and building permits</div>', unsafe_allow_html=True)
    if jobs.empty:
        return st.info("No job has been registered yet." if sees_all_jobs() else "No job has been given to you yet.")
    kpi_grid(service_cards(jobs, pays, who))
    st.markdown('<div class="dsec">What the data says</div>', unsafe_allow_html=True)
    insights_html(service_says(jobs, who))
    st.markdown('<div class="dsec">Analysis</div><div class="dcap">Hover a chart for the exact value. Every chart has a Download data button.</div>', unsafe_allow_html=True)
    money_ok = who != "engineer"
    c1, c2 = st.columns(2)
    with c1:
        by = jobs.groupby("status", as_index=False).size().rename(columns={"size": "Jobs"})
        by["o"] = by.status.map({s: i for i, s in enumerate(STATUS_COLORS)})
        by = by.sort_values("o")
        with card("Jobs by status", "Where all the jobs are now", by[["status", "Jobs"]], "jstatus"):
            c_donut(by.rename(columns={"status": "Status"}), "Status", "Jobs", colors=[STATUS_COLORS[s] for s in by.status])
    with c2:
        sv = jobs.groupby("service", as_index=False).size().rename(columns={"size": "Jobs", "service": "Service"}).sort_values("Jobs", ascending=False)
        with card("Jobs by service", "Number of jobs of each service", sv, "jservice"):
            c_bar(sv, "Service", "Jobs", unit="Jobs", color="#2f6fb0")
    c1, c2 = st.columns(2)
    with c1:
        months = _last_months(12)
        reg = jobs.assign(Date=jobs.created_at.astype(str), One=1)
        col = jobs.assign(Date=jobs.collected_at.astype(str), One=1)
        mm = pd.DataFrame({"Month": [month_label(m) for m in months], "Registered": monthly_vals(reg, "One", months), "Collected": monthly_vals(col, "One", months)})
        with card("Jobs registered and collected by month", "Last 12 months", mm, "jmonth"):
            c_grouped(mm, "Month", ["Registered", "Collected"], ytitle="Jobs")
    with c2:
        if who == "boss" or who == "reception":
            wl = jobs[jobs.status.isin(OPEN_STATUSES) & (jobs.assigned_to != "")].groupby("assigned_to", as_index=False).size().rename(columns={"size": "Open jobs", "assigned_to": "Engineer"})
            with card("Work of each engineer", "Open jobs given to each engineer", wl if len(wl) else None, "jwork"):
                if len(wl):
                    c_bar(wl.sort_values("Open jobs", ascending=False), "Engineer", "Open jobs", unit="Jobs", color="#8e5ea2")
                else:
                    st.caption("No job is assigned yet.")
        else:
            soon = jobs[jobs.status.isin(OPEN_STATUSES) & (jobs.due_on != "")].sort_values("due_on")
            tb = soon[["job_no", "customer", "title", "status", "due_on"]].rename(columns={"job_no": "Job no", "customer": "Customer", "title": "Title", "status": "Status", "due_on": "Promised"}).head(10)
            with card("Promised dates", "Your open jobs, the earliest first", tb if len(tb) else None, "jdue"):
                if len(tb):
                    st.dataframe(tb, hide_index=True)
                else:
                    st.caption("Nothing open.")
    c1, c2 = st.columns(2)
    done_ = jobs[(jobs.ready_at != "")]
    with c1:
        if len(done_):
            d = done_.assign(Days=[max((pd.to_datetime(r).normalize() - pd.to_datetime(c).normalize()).days, 0) for r, c in zip(done_.ready_at, done_.created_at)])
            av = d.groupby("service", as_index=False).Days.mean().rename(columns={"service": "Service"})
            av["Days"] = av.Days.round(1)
        else:
            av = pd.DataFrame()
        with card("Days to finish a job", "Average from registering to ready, by service", av if len(av) else None, "jdays"):
            if len(av):
                c_bar(av.sort_values("Days", ascending=False), "Service", "Days", fmt=",.1f", unit="Days", color="#e0922f")
            else:
                st.caption("No job has been finished yet.")
    with c2:
        if money_ok:
            fs = jobs[jobs.status != "Cancelled"].groupby("service", as_index=False)[["fee", "paid"]].sum().rename(columns={"service": "Service", "fee": "Fee", "paid": "Received"})
            with card("Fees and money received by service", "Frw · agreed fees against what was paid", fs if len(fs) else None, "jfees"):
                if len(fs):
                    c_grouped(fs, "Service", ["Fee", "Received"])
                else:
                    st.caption("Nothing to show yet.")
        else:
            wt = jobs[jobs.status == "Waiting for customer"][["job_no", "customer", "phone", "title"]].rename(columns={"job_no": "Job no", "customer": "Customer", "phone": "Phone", "title": "Title"})
            with card("Waiting for the customer", "Jobs where you asked the customer for something", wt if len(wt) else None, "jwait"):
                if len(wt):
                    st.dataframe(wt, hide_index=True)
                else:
                    st.caption("No job is waiting for a customer.")
    if money_ok and len(pays):
        rm = pays.assign(Date=pays["date"].astype(str))
        months = _last_months(12)
        pm = pd.DataFrame({"Month": [month_label(m) for m in months], "Received": monthly_vals(rm, "amount", months)})
        with card("Money received by month", "Frw received for services · last 12 months", pm, "jrecv"):
            c_bar(pm, "Month", "Received", horizontal=False, unit="Frw")


def jobs_dashboard():
    """Landing page of the Receptionist and the Engineer."""
    user = st.session_state.user
    st.markdown(DASH_CSS, unsafe_allow_html=True)
    st.markdown(f'<div class="dh">Dashboard</div><div class="dsub">Services · {html.escape(user)} · updates automatically</div>', unsafe_allow_html=True)
    services_tab()
