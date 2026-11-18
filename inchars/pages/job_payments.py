"""Payments tab: money received for services, and the balances still to pay."""

from datetime import date

import pandas as pd
import streamlit as st

from inchars.components.common import match, money, pretty, search_box, to_excel, wide
from inchars.components.kpi import kpi, kpi_grid
from inchars.components.sheets import CORE, download_sheet, sheet_number
from inchars.config import XLSX
from inchars.data.jobs import load_jobs, load_payments


def payments_tab():
    pays, jobs = load_payments(), load_jobs()
    live = jobs[jobs.status != "Cancelled"]
    unpaid = live[live.balance > 0]
    month = str(date.today())[:7]
    kpi_grid([kpi("Received this month", money(pays[pays["date"].astype(str).str[:7] == month].amount.sum()) if len(pays) else "0", "Frw for services", "green"),
              kpi("Received in total", money(pays.amount.sum()) if len(pays) else "0", f"{len(pays)} payment(s)", "blue"),
              kpi("Still to pay", money(unpaid.balance.sum()) if len(unpaid) else "0", f"{len(unpaid)} job(s) with a balance", "red" if len(unpaid) else "green")])
    st.subheader("Payments")
    a, b, c = st.columns([2, 2, 3])
    months = ["All months"] + sorted({str(d)[:7] for d in pays["date"]}, reverse=True) if len(pays) else ["All months"]
    sel_m = a.selectbox("Month", months, key="jp_month")
    method = b.selectbox("Paid by", ["All"] + sorted(set(pays.method)) if len(pays) else ["All"], key="jp_method")
    with c:
        q = search_box("jp_q", "Search job, customer or reference", "Type part of a word…")
    view = pays if sel_m == "All months" else pays[pays["date"].astype(str).str[:7] == sel_m]
    view = view if method == "All" else view[view.method == method]
    view = match(view, ["job_no", "customer", "reference", "received_by"], q)
    sheet = view.assign(Date=view["date"], Item=view.job_no + " " + view.customer, Qty=1.0, **{"P/U": view.amount, "Total": view.amount})
    extras = ["method", "reference", "received_by"]
    t = sheet_number(sheet, CORE + extras).rename(columns={"method": "Paid by", "reference": "Reference", "received_by": "Received by"})
    st.markdown(f"**{len(t)} payment(s) · Total {money(view.amount.sum()) if len(view) else '0'} Frw**")
    wide(st.dataframe, pretty(t, ["P/U", "Total"], qty_cols=["Qty"]), hide_index=True)
    if len(t):
        download_sheet(sheet.rename(columns={"method": "Paid by", "reference": "Reference", "received_by": "Received by"}),
                       ["Paid by", "Reference", "Received by"], "Service payments", "jp_dl")
    st.subheader("Balances still to pay")
    if unpaid.empty:
        return st.success("Every job is paid in full.")
    bal = unpaid[["job_no", "customer", "phone", "service", "status", "fee", "paid", "balance"]].rename(
        columns={"job_no": "Job no", "customer": "Customer", "phone": "Phone", "service": "Service", "status": "Status", "fee": "Fee", "paid": "Paid", "balance": "Balance"})
    bal = bal.sort_values("Balance", ascending=False).reset_index(drop=True)
    bal.insert(0, "No", range(1, len(bal) + 1))
    wide(st.dataframe, pretty(bal, ["Fee", "Paid", "Balance"], "Status"), hide_index=True)
    st.download_button(":material/download: Download Excel", to_excel({"Balances": bal}), f"balances_{date.today()}.xlsx", mime=XLSX, key="dl_bal")
