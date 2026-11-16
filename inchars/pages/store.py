"""Store manager pages: materials received, materials used, stock count."""

from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st

from inchars.accounts import has
from inchars.components.common import K, done, match, pretty, search_box, to_excel, wide
from inchars.config import STORE, XLSX
from inchars.data.materials import (
    add_moves,
    count_stock,
    load_materials,
    load_moves,
    parse_moves,
    stock_balances,
)
from inchars.data.scope import no_field_message, visible_sites


def pick_field(sites, key):
    if len(sites) == 1:
        st.markdown(f"**Field:** {sites.name.iloc[0]}")
        return sites.name.iloc[0]
    return st.selectbox("Field", sites.name.tolist(), key=key)


def move_columns(kind, labels):
    cfg = {"Date": st.column_config.DateColumn("Date", max_value=date.today(), format="YYYY-MM-DD"),
           "Material": st.column_config.SelectboxColumn("Material", options=labels or ["(list is empty)"], required=False),
           "Qty": st.column_config.NumberColumn("Quantity", min_value=0.0, step=0.01),
           "Detail": st.column_config.TextColumn("Supplier / delivery note" if kind == "Received" else "Used for (work or activity)")}
    if kind == "Received":
        cfg["New material"] = st.column_config.TextColumn("New material (if not in the list)")
        cfg["Unit"] = st.column_config.TextColumn("Unit (new material)")
    return cfg


def recent_moves(site_id, kind, key):
    st.subheader("Recent entries")
    q = search_box(key, "Search material or note", "Type part of a name…")
    mv = load_moves([site_id])
    mv = match(mv[mv.Type == kind], ["Material", "Detail", "Entered by"], q).head(100)
    shown = mv.assign(Qty=mv.Qty.abs())[["Date", "Material", "Unit", "Qty", "Detail", "Entered by"]].reset_index(drop=True)
    shown.insert(0, "No", range(1, len(shown) + 1))
    wide(st.dataframe, pretty(shown, qty_cols=["Qty"]), hide_index=True)
    if len(shown):
        st.download_button(":material/download: Download Excel", to_excel({kind: shown}), f"materials_{kind.lower()}_{date.today()}.xlsx",
                           mime=XLSX, key=f"dl_{key}")


def page_receive():
    if not has(STORE):
        return st.error("This page is for the Store manager.")
    sites = visible_sites()
    if sites.empty:
        return no_field_message()
    field = pick_field(sites, K("rf"))
    sid = int(sites[sites.name == field].id.iloc[0])
    mats = load_materials()
    labels = {f"{r.name} ({r.unit})": int(r.id) for r in mats.itertuples()}
    st.subheader("Materials received")
    st.caption("One line for each delivery. Choose the material from the list. If it is not in the list yet, type its "
               "name and unit in the last columns and it is added for everybody. Add more lines with the + at the bottom.")
    grid = pd.DataFrame({"Date": [date.today()] * 5, "Material": [None] * 5, "Qty": [0.0] * 5, "New material": [""] * 5,
                         "Unit": [""] * 5, "Detail": [""] * 5})
    ed = wide(st.data_editor, grid, num_rows="dynamic", hide_index=True, key=K("rg"), column_config=move_columns("Received", list(labels)))
    ok, bad = parse_moves(ed, labels, allow_new=True)
    if ok:
        st.write(f"**{len(ok)} line(s) ready to save.**")
    for msg in bad:
        st.warning(msg)
    if st.button("Save received materials", type="primary"):
        if bad:
            st.error("Please correct the lines marked above.")
        elif not ok:
            st.info("Nothing to save yet.")
        else:
            add_moves(sid, labels, "Received", ok, st.session_state.user)
            done(f"{len(ok)} delivery line(s) saved for {field}.")
    recent_moves(sid, "Received", "q_rec")


def page_use():
    if not has(STORE):
        return st.error("This page is for the Store manager.")
    sites = visible_sites()
    if sites.empty:
        return no_field_message()
    field = pick_field(sites, K("uf"))
    sid = int(sites[sites.name == field].id.iloc[0])
    bal = stock_balances(load_moves([sid]))
    avail = bal[bal.Balance > 1e-9]
    if avail.empty:
        return st.info("There is nothing in stock yet. Record the materials received first.")
    labels = {f"{r.Material} ({r.Unit}) - {r.Balance:g} in stock": int(r.material_id) for r in avail.itertuples()}
    st.subheader("Materials used")
    st.caption("One line for each use. You cannot use more than the quantity in stock.")
    grid = pd.DataFrame({"Date": [date.today()] * 5, "Material": [None] * 5, "Qty": [0.0] * 5, "Detail": [""] * 5})
    ed = wide(st.data_editor, grid, num_rows="dynamic", hide_index=True, key=K("ug"), column_config=move_columns("Used", list(labels)))
    ok, bad = parse_moves(ed, labels)
    if ok:
        st.write(f"**{len(ok)} line(s) ready to save.**")
    for msg in bad:
        st.warning(msg)
    if st.button("Save used materials", type="primary"):
        if bad:
            st.error("Please correct the lines marked above.")
        elif not ok:
            st.info("Nothing to save yet.")
        else:
            err = add_moves(sid, labels, "Used", ok, st.session_state.user)
            if err:
                st.error(err)
            else:
                done(f"{len(ok)} usage line(s) saved for {field}.")
    recent_moves(sid, "Used", "q_use")


def page_count():
    if not has(STORE):
        return st.error("This page is for the Store manager.")
    sites = visible_sites()
    if sites.empty:
        return no_field_message()
    field = pick_field(sites, K("cf"))
    sid = int(sites[sites.name == field].id.iloc[0])
    mats = load_materials()
    if mats.empty:
        return st.info("The materials list is empty.")
    st.write("Count what is really in the store. Search for a material, type the counted quantity, then search for the "
             "next one. Your counts are kept while you search. Leave **Counted** empty for materials you did not count.")
    bal = stock_balances(load_moves([sid]))
    have = dict(zip(bal.material_id.astype(int), bal.Balance)) if len(bal) else {}
    base = mats.assign(system=[float(have.get(int(i), 0.0)) for i in mats.id])
    counts = st.session_state.setdefault("mcount", {})  # {(field id, material id): counted}
    q = search_box("q_count", "Search material", "Type part of a material name…")
    shown = match(base, ["name"], q)
    grid = pd.DataFrame({"Material": shown.name.values, "Unit": shown.unit.values, "In system": shown.system.values,
                         "Counted": pd.array([counts.get((sid, int(i))) for i in shown.id], dtype="Float64")})
    ed = wide(st.data_editor, grid, hide_index=True, height=420, disabled=["Material", "Unit", "In system"],
              column_config={"Counted": st.column_config.NumberColumn("Counted", min_value=0.0, step=0.01)},
              key=f"mc{K('c')}_{sid}_{q}")
    for i, c in zip(shown.id, ed["Counted"]):
        if pd.notna(c):
            counts[(sid, int(i))] = float(c)
        else:
            counts.pop((sid, int(i)), None)
    sysq, names = dict(zip(base.id.astype(int), base.system)), dict(zip(base.id.astype(int), base.name))
    diff = pd.DataFrame([{"Material": names[m], "In system": sysq[m], "Counted": c, "Difference": c - sysq[m]}
                         for (s_, m), c in counts.items() if s_ == sid and m in sysq and abs(c - sysq[m]) > 1e-9])
    if len(diff):
        st.write(f"**{len(diff)} material(s) will be corrected:**")
        wide(st.dataframe, pretty(diff, qty_cols=["In system", "Counted", "Difference"]), hide_index=True)
    reason = st.text_input("Note", "Physical count", key=K("cn"))
    b1, b2 = st.columns(2)
    if b1.button("Save stock count", type="primary", disabled=diff.empty):
        n = count_stock(sid, {m: c for (s_, m), c in counts.items() if s_ == sid and m in sysq}, reason, st.session_state.user)
        for key in [k for k in counts if k[0] == sid]:
            counts.pop(key)
        done(f"Stock corrected for {n} material(s).")
    if b2.button("Clear my counts", disabled=not any(k[0] == sid for k in counts)):
        for key in [k for k in counts if k[0] == sid]:
            counts.pop(key)
        done("Counts cleared.")
