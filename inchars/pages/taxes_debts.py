"""Taxes and debts page (CEO and Accountant)."""

from datetime import date, datetime, timedelta

import streamlit as st

from inchars.accounts import has
from inchars.components.common import K, bars, done, match, money, search_box
from inchars.components.sheets import sheet_show
from inchars.config import ACCOUNTANT, CEO, TAX_TYPES
from inchars.data.money import field_choices
from inchars.data.scope import visible_sites
from inchars.db import df, run


def page_taxdebt():
    if not has(CEO, ACCOUNTANT):
        return st.error("This page is for the CEO and the Accountant.")
    user = st.session_state.user
    fc = field_choices(visible_sites())
    st.subheader("Taxes paid")
    a1, a2, a3 = st.columns(3)
    ttype = a1.selectbox("Tax", TAX_TYPES, key=K("tt"))
    period = a2.text_input("Period", placeholder="For example October 2026", key=K("tp"))
    tamount = a3.number_input("Amount (Frw)", min_value=1, step=1000, value=None, placeholder="Enter amount", key=K("ta"))
    b1, b2, b3 = st.columns(3)
    tday = b1.date_input("Date paid", date.today(), max_value=date.today(), key=K("td"))
    tref = b2.text_input("Receipt or reference", key=K("tr"))
    tfield = b3.selectbox("Field (optional)", list(fc), key=K("tf"))
    if st.button("Save tax payment", type="primary"):
        if not tamount:
            st.warning("Enter the amount.")
        else:
            run("INSERT INTO taxes(tax_type, period, amount, paid_on, reference, site_id, entered_by) VALUES(?,?,?,?,?,?,?)",
                (ttype, period.strip(), int(tamount), str(tday), tref.strip(), fc[tfield], user))
            done(f"{money(tamount)} Frw of {ttype} recorded.")
    taxes = df("""SELECT t.paid_on AS "Date", t.tax_type AS "Tax", t.period AS "Period", t.amount AS "Amount",
                  t.reference AS "Reference", COALESCE(s.name, '(no field)') AS "Field", t.entered_by AS "By"
                  FROM taxes t LEFT JOIN sites s ON s.id=t.site_id ORDER BY t.paid_on DESC, t.id DESC""")
    if taxes.empty:
        st.caption("No tax payments recorded yet.")
    else:
        q = search_box("q_tax", "Search tax, period or reference", "Type part of a word…")
        view = match(taxes, ["Tax", "Period", "Reference", "Field"], q)
        st.caption(f"{len(view)} payments, total {money(view.Amount.sum())} Frw")
        bars(view.groupby("Tax", as_index=False).Amount.sum().sort_values("Amount", ascending=False), "Tax", "Amount")
        sheet_show(view.assign(Item=view.Tax + " " + view.Period.fillna(""), Qty=1.0, **{"P/U": view.Amount, "Total": view.Amount}),
                   ["Period", "Reference", "Field", "By"], "Taxes paid", "tx")

    st.subheader("Debts")
    st.caption("Money the company owes, or money others owe the company.")
    d1, d2, d3 = st.columns(3)
    party = d1.text_input("Who", placeholder="Supplier, bank, client…", key=K("dp"))
    direction = d2.selectbox("Direction", ["We owe", "Owed to us"], key=K("dd"))
    damount = d3.number_input("Amount (Frw)", min_value=1, step=1000, value=None, placeholder="Enter amount", key=K("da"))
    e1, e2, e3 = st.columns(3)
    due = e1.date_input("Due date", date.today() + timedelta(days=30), key=K("de"))
    dfield = e2.selectbox("Field (optional)", list(fc), key=K("df"))
    dnote = e3.text_input("Note (optional)", key=K("dn"))
    if st.button("Save debt", type="primary"):
        if not party.strip() or not damount:
            st.warning("Enter who and the amount.")
        else:
            run("INSERT INTO debts(party, direction, amount, due_on, site_id, note, entered_by, entered_on) VALUES(?,?,?,?,?,?,?,?)",
                (party.strip(), direction, int(damount), str(due), fc[dfield], dnote.strip(), user, str(date.today())))
            done(f"Debt with {party.strip()} saved.")
    debts = df("""SELECT d.id, d.party AS "Who", d.direction AS "Direction", d.amount AS "Amount",
                  COALESCE((SELECT SUM(p.amount) FROM debt_payments p WHERE p.debt_id=d.id), 0) AS "Paid",
                  d.due_on AS "Due", COALESCE(s.name, '(no field)') AS "Field", d.note AS "Note"
                  FROM debts d LEFT JOIN sites s ON s.id=d.site_id ORDER BY d.due_on""")
    if debts.empty:
        return st.caption("No debts recorded yet.")
    debts["Balance"] = debts.Amount - debts.Paid
    debts["Status"] = ["Settled" if b <= 0 else ("Overdue" if due_ < str(date.today()) else "Open") for b, due_ in zip(debts.Balance, debts.Due)]
    q2 = search_box("q_debt", "Search who, field or note", "Type part of a word…")
    view = match(debts, ["Who", "Field", "Note"], q2)
    k = st.columns(2)
    k[0].metric("We owe (open)", money(view[(view.Direction == "We owe") & (view.Balance > 0)].Balance.sum()))
    k[1].metric("Owed to us (open)", money(view[(view.Direction == "Owed to us") & (view.Balance > 0)].Balance.sum()))
    sheet_show(view.assign(Date=view.Due, Item=view.Who, Qty=1.0, **{"P/U": view.Amount, "Total": view.Amount}),
               ["Direction", "Paid", "Balance", "Status", "Field", "Note"], "Debts", "db")
    openr = debts[debts.Balance > 0]
    if len(openr):
        st.markdown("**Record a payment on a debt**")
        labels = {f"{r.Who} ({r.Direction}) - balance {money(r.Balance)}": int(r.id) for r in openr.itertuples()}
        p1, p2, p3 = st.columns(3)
        which = p1.selectbox("Debt", list(labels), index=None, placeholder="Choose a debt", key=K("pd"))
        pam = p2.number_input("Amount paid (Frw)", min_value=1, step=1000, value=None, placeholder="Enter amount", key=K("pa"))
        pday = p3.date_input("Date", date.today(), max_value=date.today(), key=K("pt"))
        if st.button("Save payment", key="dp_save"):
            if not which or not pam:
                st.warning("Choose the debt and enter the amount.")
            elif pam > float(openr[openr.id == labels[which]].Balance.iloc[0]):
                st.error("The payment is more than the balance of this debt.")
            else:
                run("INSERT INTO debt_payments(debt_id, amount, paid_on, entered_by) VALUES(?,?,?,?)", (labels[which], int(pam), str(pday), user))
                done("Payment saved.")
