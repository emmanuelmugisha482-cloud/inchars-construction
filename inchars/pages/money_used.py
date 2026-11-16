"""Money used page."""

from datetime import date, datetime, timedelta

import streamlit as st

from inchars.accounts import has
from inchars.components.common import K, done, match, money, search_box
from inchars.components.sheets import (
    category_pick,
    download_workbook,
    parse_sheet,
    sheet_editor,
    sheet_preview,
    sheet_view,
)
from inchars.config import CEO
from inchars.data.money import field_choices, load_ledger
from inchars.data.scope import visible_sites
from inchars.db import bulk_insert, conn, df, ensure_named


def moneyused_form(sites, field, user):
    with st.expander("Record money I used (Excel sheet)", expanded=True):
        st.caption("Money you spent yourself: materials, fuel, food, personal use. Personal entries are visible only to you.")
        a, b, c = st.columns(3)
        with a:
            cat, is_new = category_pick(K("mu_c"))
        kind = b.selectbox("Kind", ["Business", "Personal"], key=K("mu_k"))
        fc = field_choices(sites)
        names = list(fc)
        default = names.index(field) if field in names else 0
        fname = c.selectbox("Field", names, index=default, key=K("mu_f"))
        ed = sheet_editor("mu_g", rows=4)
        ok, bad = parse_sheet(ed)
        sheet_preview(ok)
        for msg in bad:
            st.warning(msg)
        if st.button("Save money used", type="primary", key="mu_save"):
            if not cat:
                st.warning("Write the name of the new category.")
            elif bad:
                st.error("Please correct the lines marked above.")
            elif not ok:
                st.info("Nothing to save yet.")
            else:
                now = datetime.now().isoformat(timespec="seconds")
                with conn() as c_:
                    if is_new:
                        ensure_named(c_, "categories", cat)
                    bulk_insert(c_, "money_ledger", ["spent_on", "person", "site_id", "kind", "category", "amount", "note", "qty", "unit_price",
                                                     "entered_by", "entered_at"],
                                [(str(e["date"]), user, fc[fname], kind, cat, e["total"], e["item"], e["qty"], e["price"], user, now) for e in ok])
                done(f"{len(ok)} row(s) saved.")


def moneyused_page():
    user = st.session_state.user
    field = st.session_state.get("ceo_field", "All fields")
    sites = visible_sites()
    if has(CEO):
        moneyused_form(sites, field, user)
    st.subheader("Money used")
    led = load_ledger()
    if field != "All fields":
        led = led[led.Field == field]
    if led.empty:
        return st.caption("Nothing recorded yet.")
    raw = df("SELECT id, qty, unit_price FROM money_ledger")
    led = led.merge(raw, on="id", how="left").reset_index(drop=True)
    a, b, c = st.columns([2, 2, 3])
    kind = a.radio("Show", ["All", "Business", "Personal"], horizontal=True, key="mu_show")
    cat = b.selectbox("Category", ["All categories"] + sorted(set(led.Category)), key="mu_cat")
    with c:
        q = search_box("mu_q", "Search item, person or field", "Type part of a word…")
    view = led if kind == "All" else led[led.Kind == kind]
    view = view if cat == "All categories" else view[view.Category == cat]
    view = match(view, ["Note", "Person", "Field"], q)
    k = st.columns(3)
    k[0].metric("Total", money(view.Amount.sum()))
    k[1].metric("Business", money(view[view.Kind == "Business"].Amount.sum()))
    k[2].metric("Personal", money(view[view.Kind == "Personal"].Amount.sum()))
    sheet = view.assign(Item=view.Note.fillna(""), Qty=view.qty.fillna(1.0), **{"P/U": view.unit_price.fillna(view.Amount), "Total": view.Amount})
    extras = (["Category"] if cat == "All categories" else []) + ["Kind", "Person", "Field"]
    mine = sheet[(sheet.entered_by == user) & sheet.request_id.isna()]
    ticked = sheet_view(sheet, "muv", extras, removable=bool(len(mine)))
    own = set(mine.id.astype(int))
    c1, c2 = st.columns(2)
    with c1:
        download_workbook(sheet, "money_used", "mu_wb")
    with c2:
        ticked = [i for i in ticked if i in own]
        if ticked and st.button("Delete ticked rows (my own)", key="mu_del"):
            with conn() as c_:
                for i in ticked:
                    c_.execute("DELETE FROM money_ledger WHERE id=? AND entered_by=? AND request_id IS NULL", (i, user))
            done(f"{len(ticked)} row(s) deleted.")
