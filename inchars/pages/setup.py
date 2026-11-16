"""Fields and categories page."""

from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st

from inchars.accounts import has
from inchars.components.common import K, done, match, search_box, wide
from inchars.config import ACCOUNTANT, CEO
from inchars.data.scope import categories_df
from inchars.db import IntegrityErrors, conn, df, run


def add_site(name, client, location, budget, start):
    with conn() as c:
        return c.execute("INSERT INTO sites(name, client, location, budget, start_date) VALUES(?,?,?,?,?) RETURNING id",
                         (name.strip(), client.strip(), location.strip(), int(budget or 0), str(start))).fetchone()["id"]


def page_setup():
    if not has(CEO, ACCOUNTANT):
        return st.error("This page is for the CEO and the Accountant.")
    if has(CEO):
        setup_fields()
    setup_categories()


def setup_fields():
    st.subheader("Fields")
    st.caption("A field is one construction project or site.")
    a1, a2, a3 = st.columns(3)
    name = a1.text_input("Field name", key=K("sn"))
    client = a2.text_input("Client", key=K("sc"))
    location = a3.text_input("Location", key=K("sl"))
    b1, b2 = st.columns(2)
    budget = b1.number_input("Budget (Frw, optional)", min_value=0, step=100000, value=None, placeholder="Enter budget", key=K("sb"))
    start = b2.date_input("Start date", date.today(), key=K("sd"))
    if st.button("Add field", type="primary"):
        if not name.strip():
            st.warning("Write the field name.")
        else:
            try:
                add_site(name, client, location, budget, start)
                done(f"Field {name.strip()} added.")
            except IntegrityErrors:
                st.error("A field with that name already exists.")
    sites = df("SELECT id, name, client, location, budget, status FROM sites ORDER BY name")
    if len(sites):
        tbl = sites.rename(columns={"name": "Field", "client": "Client", "location": "Location", "budget": "Budget",
                                    "status": "Status"}).drop(columns="id")
        tbl["Budget"] = tbl["Budget"].fillna(0).astype(int)
        ed = wide(st.data_editor, tbl, hide_index=True, key=f"{K('st')}_{len(sites)}", disabled=["Field"], column_config={
            "Status": st.column_config.SelectboxColumn("Status", options=["Active", "Completed", "On hold"], required=True),
            "Budget": st.column_config.NumberColumn("Budget", min_value=0, step=100000)})
        if st.button("Save changes to fields"):
            with conn() as c:
                for i, cl, lo, bu, stt in zip(sites.id, ed["Client"], ed["Location"], ed["Budget"], ed["Status"]):
                    c.execute("UPDATE sites SET client=?, location=?, budget=?, status=? WHERE id=?",
                              (cl or "", lo or "", int(0 if pd.isna(bu) else bu), stt, int(i)))
            done("Fields saved.")


def setup_categories():
    st.subheader("Categories")
    cats = categories_df()
    q = search_box("q_cats", "Search category", "Type part of a name…")
    wide(st.dataframe, match(cats[["name"]].rename(columns={"name": "Category"}), ["Category"], q), hide_index=True)
    c1, c2 = st.columns([3, 1])
    newcat = c1.text_input("New category", key=K("nc"))
    c2.write("")
    if c2.button("Add category"):
        if not newcat.strip():
            st.warning("Write the category name.")
        else:
            try:
                run("INSERT INTO categories(name) VALUES(?)", (newcat.strip(),))
                done(f"Category {newcat.strip()} added.")
            except IntegrityErrors:
                st.error("That category already exists.")
