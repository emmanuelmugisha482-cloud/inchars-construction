"""Material stock page."""

from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st

from inchars.accounts import has
from inchars.components.common import K, done, match, pretty, search_box, to_excel, wide
from inchars.config import ACCOUNTANT, CEO, STORE, XLSX
from inchars.data.materials import load_mat_requests, load_moves, stock_balances
from inchars.data.scope import categories_df, no_field_message, visible_sites
from inchars.db import conn, df


def stock_sheet(mv, reqs):
    """One row per field and material: date, category, item, in stock, received, used, requested, by."""
    bal = stock_balances(mv)
    mats = df("SELECT id, COALESCE(category, 'Uncategorised') AS category FROM materials")
    cat_of = dict(zip(mats.id, mats.category))
    req = pd.Series(dtype=float)
    if len(reqs):
        r = reqs[reqs.Status != "Rejected"]
        req = r.groupby(["site_id", "material_id"]).Requested.sum()
    last = mv.sort_values(["Date"], ascending=False, kind="stable").drop_duplicates(["site_id", "material_id"]) if len(mv) else mv
    last_d = {(int(a), int(b)): (d, by) for a, b, d, by in zip(last.site_id, last.material_id, last.Date, last["Entered by"])} if len(last) else {}
    keys = {(int(a), int(b)) for a, b in zip(bal.site_id, bal.material_id)} if len(bal) else set()
    keys |= {(int(a), int(b)) for a, b in req.index}
    names = {}
    for src in (bal, reqs):
        if len(src):
            for a, b, f, m, u in zip(src.site_id, src.material_id, src.Field, src.Material, src.Unit):
                names[(int(a), int(b))] = (f, m, u)
    brow = {(int(a), int(b)): (x, y, z) for a, b, x, y, z in zip(bal.site_id, bal.material_id, bal.Balance, bal.Received, bal.Used)} if len(bal) else {}
    rows = []
    for key in sorted(keys, key=lambda k: (names[k][0], names[k][1])):
        f, m, u = names[key]
        b, rcv, used = brow.get(key, (0.0, 0.0, 0.0))
        d, by = last_d.get(key, ("", ""))
        rows.append({"Date": d, "Field": f, "Category": cat_of.get(key[1], "Uncategorised"), "Item": m, "Unit": u or "",
                     "In stock": float(b), "Received": float(rcv), "Used": float(used), "Requested": float(req.get(key, 0.0)), "By": by})
    return pd.DataFrame(rows, columns=["Date", "Field", "Category", "Item", "Unit", "In stock", "Received", "Used", "Requested", "By"])


def stock_page():
    sites = visible_sites()
    if sites.empty:
        return no_field_message()
    ids = [int(i) for i in sites.id]
    field = st.session_state.get("ceo_field", "All fields")
    mv, reqs = load_moves(ids), load_mat_requests(ids)
    bal = stock_balances(mv)
    recent = str(date.today() - timedelta(days=30))
    st.markdown(f"**Field:** {field}")
    k = st.columns(4)
    k[0].metric("Materials in stock", int((bal.Balance > 1e-9).sum()) if len(bal) else 0)
    k[1].metric("Out of stock", int((bal.Balance <= 1e-9).sum()) if len(bal) else 0)
    k[2].metric("Received (30 days)", int(((mv.Type == "Received") & (mv.Date >= recent)).sum()) if len(mv) else 0)
    k[3].metric("Used (30 days)", int(((mv.Type == "Used") & (mv.Date >= recent)).sum()) if len(mv) else 0)

    st.subheader("Stock balance")
    sheet = stock_sheet(mv, reqs)
    if sheet.empty:
        st.info("No materials have been recorded yet.")
    else:
        a, b = st.columns([2, 3])
        cat = a.selectbox("Category", ["All categories"] + sorted(set(sheet.Category)), key="cs_cat")
        with b:
            q = search_box("cs_q", "Search material", "Type part of a material name…")
        view = sheet if cat == "All categories" else sheet[sheet.Category == cat]
        view = match(view, ["Item", "By"], q)
        cols = ["Date"] + (["Field"] if field == "All fields" else []) + ["Category", "Item", "Unit", "In stock", "Received", "Used", "Requested", "By"]
        shown = view[cols].reset_index(drop=True)
        shown.insert(0, "No", range(1, len(shown) + 1))
        st.caption(f"{len(shown)} materials")
        wide(st.dataframe, pretty(shown, qty_cols=["In stock", "Received", "Used", "Requested"]), hide_index=True)
        st.download_button(":material/download: Download Excel", to_excel({"Stock balance": shown}),
                           f"material_stock_{date.today()}.xlsx", mime=XLSX, key="dl_ceo_stock")
        if has(CEO, ACCOUNTANT, STORE):
            with st.expander("Set the category of a material"):
                st.caption("Categories are the same as the cost workbook. Add a new category in Fields & categories.")
                mats = df("SELECT id, name, unit, COALESCE(category, '') AS category FROM materials ORDER BY name")
                opts = categories_df().name.tolist()
                tbl = mats.rename(columns={"name": "Item", "unit": "Unit", "category": "Category"}).drop(columns="id")
                tbl["Category"] = tbl["Category"].where(tbl["Category"] != "", None)
                ed = wide(st.data_editor, tbl, hide_index=True, key=f"{K('mc')}_{len(mats)}", disabled=["Item", "Unit"], column_config={
                    "Category": st.column_config.SelectboxColumn("Category", options=opts, required=False)})
                if st.button("Save categories", key="mc_save"):
                    with conn() as c:
                        for i, cat_ in zip(mats.id, ed["Category"]):
                            c.execute("UPDATE materials SET category=? WHERE id=?", (cat_ if isinstance(cat_, str) and cat_ else None, int(i)))
                    done("Categories saved.")

    st.subheader("Movements")
    m1, m2 = st.columns([2, 3])
    kind = m1.radio("Show", ["All", "Received", "Used", "Count"], horizontal=True, key="cs_kind")
    with m2:
        q2 = search_box("cs_q2", "Search material, note or person", "Type part of a name…")
    mvs = mv if kind == "All" else mv[mv.Type == kind]
    mvs = match(mvs, ["Material", "Detail", "Entered by"], q2)
    st.caption(f"{len(mvs)} movements" + (" (showing the latest 300)" if len(mvs) > 300 else ""))
    mcols = ["Date", "Field", "Material", "Unit", "Type", "Qty", "Detail", "Entered by"]
    shown = mvs.head(300)[mcols].reset_index(drop=True)
    shown.insert(0, "No", range(1, len(shown) + 1))
    wide(st.dataframe, pretty(shown, qty_cols=["Qty"]), hide_index=True)
    if len(shown):
        st.download_button(":material/download: Download Excel ", to_excel({"Movements": shown}), f"material_movements_{date.today()}.xlsx",
                           mime=XLSX, key="dl_ceo_mv")
