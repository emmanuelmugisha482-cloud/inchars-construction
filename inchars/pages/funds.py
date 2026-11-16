"""Funds received page (CEO)."""

from datetime import date, datetime, timedelta

import streamlit as st

from inchars.components.common import K, done, match, money, search_box
from inchars.components.sheets import download_sheet, field_target, sheet_view
from inchars.data.expenses import load_funds
from inchars.data.scope import no_field_message, visible_sites
from inchars.db import conn, run


def funds_page():
    user = st.session_state.user
    sites = visible_sites()
    if sites.empty:
        return no_field_message()
    ids = [int(i) for i in sites.id]
    with st.expander("Record funds received", expanded=True):
        a, b, c = st.columns(3)
        with a:
            site_id, fname = field_target(sites, K("cf_f"))
        amount = b.number_input("Amount (Frw)", min_value=1, step=1000, value=None, placeholder="Enter amount", key=K("cf_a"))
        day = c.date_input("Date received", date.today(), max_value=date.today(), key=K("cf_d"))
        d, e = st.columns(2)
        source = d.text_input("Received from", placeholder="For example the client name", key=K("cf_s"))
        note = e.text_input("Note (optional)", key=K("cf_n"))
        if st.button("Save funds", type="primary", key="cf_save"):
            if not amount:
                st.warning("Enter the amount.")
            else:
                run("INSERT INTO funds(site_id, amount, received_on, source, note, entered_by) VALUES(?,?,?,?,?,?)",
                    (site_id, int(amount), str(day), source.strip(), note.strip(), user))
                done(f"{money(amount)} Frw recorded for {fname}.")
    st.subheader("Funds received")
    q = search_box("cf_q", "Search field, source or note", "Type part of a name…")
    fnd = match(load_funds(ids), ["Field", "Source", "Note"], q)
    sheet = fnd.assign(Item=fnd.Source.fillna("").where(fnd.Source.fillna("") != "", "Funds received"), Qty=1.0,
                       **{"P/U": fnd.Amount, "Total": fnd.Amount})
    extras = (["Field"] if len(sites) > 1 else []) + ["Note", "Entered by"]
    ticked = sheet_view(sheet, "cfv", extras, removable=True)
    c1, c2 = st.columns(2)
    with c1:
        download_sheet(sheet, extras, "Funds received", "cf_dl")
    with c2:
        if ticked and st.button("Delete ticked records", key="cf_del"):
            with conn() as c_:
                for rid in ticked:
                    c_.execute("DELETE FROM funds WHERE id=?", (rid,))
            done(f"{len(ticked)} record(s) deleted.")
