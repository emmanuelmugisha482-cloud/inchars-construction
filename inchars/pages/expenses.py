"""Expenses page."""

from datetime import date, datetime, timedelta

import streamlit as st

from inchars.accounts import has
from inchars.components.common import done, match, search_box
from inchars.components.sheets import add_cost_rows_form, cost_frame, download_workbook, sheet_view
from inchars.config import ACCOUNTANT, CEO
from inchars.data.expenses import delete_expenses, load_expenses
from inchars.data.scope import categories_df, no_field_message, visible_sites
from inchars.db import conn


def expenses_page():
    sites = visible_sites()
    if sites.empty:
        return no_field_message()
    ids = [int(i) for i in sites.id]
    with st.expander("Add rows (Excel sheet)", expanded=True):
        st.caption("Choose the category, then write the rows: Date, Item, Qty, P/U. The total is Qty x P/U.")
        add_cost_rows_form(sites, "cxe")
    exp = load_expenses(ids)
    st.subheader("Category sheets")
    a, b, c, d = st.columns([2, 2, 1.5, 1.5])
    cat = a.selectbox("Category", ["All categories"] + categories_df().name.tolist(), key="ce_cat")
    with b:
        q = search_box("ce_q", "Search item or person", "Type part of an item name…")
    lo = date.fromisoformat(exp.Date.min()) if len(exp) else date.today()
    hi = max(date.fromisoformat(exp.Date.max()), date.today()) if len(exp) else date.today()
    fk = st.session_state.get("ceo_field", "All fields")
    d_from, d_to = c.date_input("From", lo, key=f"ce_from_{fk}"), d.date_input("To", hi, key=f"ce_to_{fk}")
    view = exp[(exp.Date >= str(d_from)) & (exp.Date <= str(d_to))]
    if cat != "All categories":
        view = view[view.Category == cat]
    view = cost_frame(match(view, ["Item", "Entered by"], q))
    extras = (["Category"] if cat == "All categories" else []) + (["Field"] if len(sites) > 1 else []) + ["Entered by"]
    ticked = sheet_view(view, "cev", extras, removable=True)
    c1, c2 = st.columns(2)
    with c1:
        download_workbook(view, "expenses", "ce_wb")
    with c2:
        if ticked and st.button("Delete ticked rows", key="ce_del"):
            if has(CEO):
                with conn() as c_:
                    for rid in ticked:
                        c_.execute("DELETE FROM expenses WHERE id=?", (rid,))
                n_del = len(ticked)
            else:
                n_del = delete_expenses(ticked, st.session_state.user, st.session_state.uid, has(ACCOUNTANT))
            done(f"{n_del} row(s) deleted." if n_del else "Nothing deleted: you can only delete your own entries of today.")
