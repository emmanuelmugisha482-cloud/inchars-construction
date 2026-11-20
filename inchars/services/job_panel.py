"""One job opened: the header and five parts (Update, Message, Documents, Money, History). Each part runs on its own."""

import streamlit as st

from inchars.accounts import has
from inchars.components.common import K, done, wide
from inchars.components.kpi import kpi, kpi_grid
from inchars.config import ACCOUNTANT, CEO, RECEPTIONIST
from inchars.services.customers import save_customer_email
from inchars.services.history import load_events
from inchars.services.jobs import load_jobs
from inchars.services.rules import can
from inchars.services.ui import pick, run_section

TONE = {"New": "amber", "Assigned": "blue", "In progress": "blue", "Waiting for customer": "amber", "Ready": "green", "Collected": "grey", "Cancelled": "grey"}


def history_section(j, p):
    ev = load_events(int(j.id))
    if ev.empty:
        st.caption("No history yet.")
    else:
        wide(st.dataframe, ev, hide_index=True)


def job_panel(job_id, p="jb"):
    """p is a short prefix so that the same job can be shown in two places without clashing."""
    jobs = load_jobs()
    rows = jobs[jobs.id == job_id]
    if rows.empty:
        return st.warning("This job is not available to you.")
    j = rows.iloc[0]
    money = has(CEO, ACCOUNTANT, RECEPTIONIST)
    with st.container(border=True):
        a, b = st.columns([6, 1])
        a.markdown(f"### {j.job_no} · {j.title}")
        if b.button("Close", key=K(f"{p}close{int(j.id)}")):
            st.session_state[p + "_open"] = None
            st.rerun()
        st.caption(f"{j.service} · {j.customer} · {j.phone} · " + (j.customer_email if j.customer_email else "NO EMAIL ADDRESS"))
        if not j.customer_email and can("set_email", j):
            st.warning("This customer has no email address, so he or she cannot receive our updates. Add it here:")
            c1, c2 = st.columns([4, 1])
            mail = c1.text_input("Customer's email", key=K(f"{p}mail{int(j.id)}"), label_visibility="collapsed", placeholder="name@example.com")
            if c2.button("Save", key=K(f"{p}mailb{int(j.id)}")):
                err = save_customer_email(int(j.customer_id), mail)
                if err:
                    st.error(err)
                else:
                    done("Email saved. The customer will now receive updates.")
        cards = [kpi("Status", j.status, ("stage: " + j.stage) if j.stage else "where the job is now", TONE.get(j.status, "grey")),
                 kpi("Engineer", j.assigned_to or "not assigned", "doing the work", "blue" if j.assigned_to else "amber", small=True),
                 kpi("Promised", j.due_on or "-", "overdue" if j.overdue else "date given to the customer", "red" if j.overdue else "grey")]
        if money:
            cards.append(kpi("Balance", f"{j.balance:,.0f}", "paid in full" if j.balance <= 0 else f"Frw to pay · fee {j.fee:,.0f}", "green" if j.balance <= 0 else "red"))
        kpi_grid(cards)
        if j.description:
            st.caption(j.description)
        parts = ["Update", "Message", "Documents"] + (["Money"] if money else []) + ["History"]
        part = pick("", parts, K(f"{p}part{int(j.id)}"), default="Update") or "Update"
        if part == "Update":
            run_section("panel_update", "update_section", j, p)
        elif part == "Message":
            run_section("panel_message", "message_section", j, p)
        elif part == "Documents":
            run_section("panel_files", "files_section", j, p)
        elif part == "Money":
            run_section("panel_money", "money_section", j, p)
        else:
            run_section("job_panel", "history_section", j, p)
