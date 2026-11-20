"""Job panel, 'Money' part: what the customer paid, and recording a payment."""

from datetime import date

import streamlit as st

from inchars.accounts import has
from inchars.components.common import K, done, pretty, wide
from inchars.components.sheets import download_sheet, sheet_number
from inchars.config import CEO
from inchars.services.config import PAY_METHODS
from inchars.services.payments import add_payment, delete_payment, load_payments
from inchars.services.rules import can
from inchars.services.ui import pick


def _msg(err, ok):
    if err:
        st.error(err)
    else:
        done(ok)


def money_section(j, p):
    jid, k = int(j.id), f"{p}{int(j.id)}_"
    pays = load_payments(jid)
    st.markdown(f"Fee **{j.fee:,.0f}** · Paid **{j.paid:,.0f}** · Balance **{j.balance:,.0f}** Frw")
    if pays.empty:
        st.caption("No payment recorded yet.")
    else:
        sheet = pays.assign(Date=pays["date"], Item=pays.method + (" " + pays.reference.fillna("")).str.rstrip(), Qty=1.0,
                            **{"P/U": pays.amount, "Total": pays.amount})
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
            _msg(add_payment(jid, amount, method, ref, day), "Payment recorded. The customer got a receipt by email.")
