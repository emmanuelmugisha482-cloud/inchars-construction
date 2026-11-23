"""Money of the jobs: payments (data), the Payments tab, and the Money part of an opened job. Only the CEO and the Receptionist see money."""

from datetime import date

import streamlit as st

from inchars.accounts import has
from inchars.components.common import K, done, match, money, pretty, search_box, to_excel, wide
from inchars.components.kpi import kpi, kpi_grid
from inchars.components.sheets import CORE, download_sheet, sheet_number
from inchars.config import CEO, RECEPTIONIST, XLSX
from inchars.db import conn, df
from inchars.services.config import PAY_METHODS
from inchars.services.core import can, fetch_job, load_jobs, log_event, now, sees_money
from inchars.services.notify import after_commit, notify, usernames_with_role
from inchars.services.ui import pick


def load_payments(job_id=None):
    sql = """SELECT p.id, p.job_id, j.job_no, c.name AS customer, p.paid_on AS date, p.amount, p.method, p.reference, p.received_by, p.note
             FROM job_payments p JOIN jobs j ON j.id=p.job_id JOIN customers c ON c.id=j.customer_id"""
    out = df(sql + (" WHERE p.job_id=?" if job_id else "") + " ORDER BY p.paid_on DESC, p.id DESC", (int(job_id),) if job_id else ())
    if not sees_money():  # an Engineer never sees payments
        out = out.iloc[0:0]
    return out.reset_index(drop=True)


@after_commit
def add_payment(job_id, amount, method, reference, paid_on):
    with conn() as c:
        j = fetch_job(c, job_id)
        if not j or not can("pay", j):
            return "You cannot record a payment on this job."
        amount = int(amount or 0)
        if amount <= 0:
            return "Enter the amount paid."
        if method not in PAY_METHODS:
            return "Choose how it was paid."
        if amount > j["balance"]:
            return f"The amount is more than the balance ({j['balance']:,.0f} Frw)."
        if paid_on and str(paid_on) > str(date.today()):
            return "The date is in the future."
        c.execute("INSERT INTO job_payments(job_id, amount, method, reference, paid_on, received_by, note, created_at) VALUES(?,?,?,?,?,?,?,?)",
                  (int(job_id), amount, method, (reference or "").strip(), str(paid_on or date.today()), st.session_state.user, "", now()))
        left = j["balance"] - amount
        log_event(c, job_id, "Payment", f"{amount:,} Frw ({method})" + (". Paid in full." if left <= 0 else f". Balance {left:,.0f} Frw."))
        if left <= 0 and j["status"] == "Ready":
            notify(c, job_id, usernames_with_role(c, RECEPTIONIST) + usernames_with_role(c, CEO), f"Job {j['job_no']} ({j['customer']}) is paid in full and ready to hand over.")
    return None


def delete_payment(payment_id):
    if not has(CEO):
        return "Only the CEO can delete a payment."
    with conn() as c:
        p = c.execute("SELECT job_id, amount FROM job_payments WHERE id=?", (int(payment_id),)).fetchone()
        if p:
            c.execute("DELETE FROM job_payments WHERE id=?", (int(payment_id),))
            log_event(c, p["job_id"], "Payment deleted", f"{int(p['amount']):,} Frw")
    return None


def _msg(err, ok):
    if err:
        st.error(err)
    else:
        done(ok)


def money_section(j, p):
    """The Money part of an opened job."""
    jid, k = int(j.id), f"{p}{int(j.id)}_"
    pays = load_payments(jid)
    st.markdown(f"Fee **{j.fee:,.0f}** · Paid **{j.paid:,.0f}** · Balance **{j.balance:,.0f}** Frw")
    if pays.empty:
        st.caption("No payment recorded yet.")
    else:
        sheet = pays.assign(Date=pays["date"], Item=pays.method + (" " + pays.reference.fillna("")).str.rstrip(), Qty=1.0, **{"P/U": pays.amount, "Total": pays.amount})
        t = sheet_number(sheet, ["No", "Date", "Item", "Qty", "P/U", "Total", "received_by"]).rename(columns={"received_by": "Received by"})
        wide(st.dataframe, pretty(t, ["P/U", "Total"], qty_cols=["Qty"]), hide_index=True)
        download_sheet(sheet.rename(columns={"received_by": "Received by"}), ["Received by"], f"Payments {j.job_no}", k + "jp")
        if has(CEO):
            with st.expander("Delete a payment (CEO)"):
                labels = {f"{r.date} · {r.amount:,.0f} Frw · {r.method}": int(r.id) for r in pays.itertuples()}
                chosen = st.selectbox("Payment", list(labels), index=None, placeholder="Choose a payment", key=K(k + "dp"))
                if chosen and st.button("Delete this payment", key=K(k + "dpb")):
                    _msg(delete_payment(labels[chosen]), "Payment deleted.")
    if can("pay", j) and j.balance > 0:
        st.markdown("**Record a payment**")
        bal = int(j.balance)
        quick = {f"Full balance ({bal:,})": bal, f"Half ({bal // 2:,})": bal // 2, "Other amount": None}
        choice = pick("Amount", list(quick), K(k + "amt"), default=f"Full balance ({bal:,})")
        amount = quick.get(choice)
        if choice == "Other amount" or choice is None:
            amount = st.number_input("Amount (Frw)", min_value=0, max_value=bal, step=1000, value=None, placeholder=f"up to {bal:,}", key=K(k + "pa"))
        method = pick("Paid by", PAY_METHODS, K(k + "pm"), default="Cash") or "Cash"
        a, b = st.columns(2)
        ref = a.text_input("Reference (optional)", key=K(k + "pr"), placeholder="Receipt or transaction number")
        day = b.date_input("Date", date.today(), max_value=date.today(), key=K(k + "pd"))
        if st.button("Save payment", type="primary", key=K(k + "pb"), disabled=not amount):
            _msg(add_payment(jid, amount, method, ref, day), "Payment recorded.")


def payments_tab():
    """The Payments tab: money received for services, and the balances still to pay."""
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
    t = sheet_number(sheet, CORE + ["method", "reference", "received_by"]).rename(columns={"method": "Paid by", "reference": "Reference", "received_by": "Received by"})
    st.markdown(f"**{len(t)} payment(s) · Total {money(view.amount.sum()) if len(view) else '0'} Frw**")
    wide(st.dataframe, pretty(t, ["P/U", "Total"], qty_cols=["Qty"]), hide_index=True)
    if len(t):
        download_sheet(sheet.rename(columns={"method": "Paid by", "reference": "Reference", "received_by": "Received by"}), ["Paid by", "Reference", "Received by"], "Service payments", "jp_dl")
    st.subheader("Balances still to pay")
    if unpaid.empty:
        return st.success("Every job is paid in full.")
    bal = unpaid[["job_no", "customer", "phone", "service", "status", "fee", "paid", "balance"]].rename(
        columns={"job_no": "Job no", "customer": "Customer", "phone": "Phone", "service": "Service", "status": "Status", "fee": "Fee", "paid": "Paid", "balance": "Balance"})
    bal = bal.sort_values("Balance", ascending=False).reset_index(drop=True)
    bal.insert(0, "No", range(1, len(bal) + 1))
    wide(st.dataframe, pretty(bal, ["Fee", "Paid", "Balance"], "Status"), hide_index=True)
    st.download_button(":material/download: Download Excel", to_excel({"Balances": bal}), f"balances_{date.today()}.xlsx", mime=XLSX, key="dl_bal")
