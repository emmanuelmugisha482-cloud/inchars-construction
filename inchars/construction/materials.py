"""Materials: Material stock, Materials received / used / Stock count (store), and Material requests."""

import pandas as pd
import streamlit as st
from datetime import date, datetime, timedelta

from inchars.accounts import categories_df, has, no_field_message, visible_sites
from inchars.data import (
    add_moves,
    count_stock,
    create_mat_request,
    decide_mat_requests,
    deliver_mat_lines,
    load_expenses,
    load_mat_requests,
    load_materials,
    load_moves,
    parse_moves,
    parse_req,
    stock_balances,
    update_mat_pending,
)
from inchars.database import conn, df
from inchars.settings import ACCOUNTANT, CEO, STORE, XLSX
from inchars.widgets import K, add_cost_rows_form, cost_frame, done, download_workbook, match, pretty, search_box, sheet_view, to_excel, wide


# ======================================================================
# from pages/stock.py
# ======================================================================

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
    kind = m1.selectbox("Show", ["All", "Received", "Used", "Count"], key="cs_kind")
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


# ======================================================================
# from pages/store.py
# ======================================================================

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


# ======================================================================
# from pages/material_requests.py
# ======================================================================

def section_approve(reqs, sites, user, is_ceo):
    st.subheader("Waiting for your approval")
    pending = reqs[reqs.Status == "Pending"]
    if not is_ceo:
        st.caption("You and the CEO can both approve. Your own requests are decided by the CEO.")
        pending = pending[pending["Requested by"] != user]
    if pending.empty:
        return st.success("No pending requests.")
    f1, f2 = st.columns(2)
    field = f1.selectbox("Field", ["All fields"] + sites.name.tolist(), key="ap_field") if len(sites) > 1 else "All fields"
    with f2:
        q = search_box("ap_q", "Search material or person", "Type part of a name…")
    shown = match(pending if field == "All fields" else pending[pending.Field == field], ["Material", "Requested by"], q)
    if shown.empty:
        return st.info("No pending request matches your search.")
    bal = stock_balances(load_moves(sorted({int(i) for i in shown.site_id})))
    have = {(int(a), int(b)): float(c) for a, b, c in zip(bal.site_id, bal.material_id, bal.Balance)}
    st.write("Every row is one material. Choose **Approve**, **Reject** or **Decide later**, change the quantity if needed, "
             "then press **Confirm decisions**.")
    tbl = pd.DataFrame({
        "Request #": shown.batch.astype(int).values, "Field": shown.Field.values, "By": shown["Requested by"].values,
        "Material": shown.Material.values, "Unit": shown.Unit.values, "Requested": shown.Requested.values,
        "In stock now": [have.get((int(a), int(b)), 0.0) for a, b in zip(shown.site_id, shown.material_id)],
        "Needed by": shown["Needed by"].values, "Decision": "Approve", "Quantity to approve": shown.Requested.values,
        "Reason if rejected": ""})
    ed = wide(st.data_editor, tbl, hide_index=True, key=f"mdec{K('m')}_{abs(hash(tuple(int(i) for i in shown.id)))}",
              disabled=["Request #", "Field", "By", "Material", "Unit", "Requested", "In stock now", "Needed by"],
              column_config={"Decision": st.column_config.SelectboxColumn(options=["Approve", "Reject", "Decide later"], required=True),
                             "Quantity to approve": st.column_config.NumberColumn(min_value=0.0, step=0.01)})
    if st.button(":material/check: Confirm decisions", type="primary", key="mdec_go"):
        decisions, problems = {}, []
        for rid, mat, dec, qty, why in zip(shown.id, ed["Material"], ed["Decision"], ed["Quantity to approve"], ed["Reason if rejected"]):
            qty = 0.0 if pd.isna(qty) else float(qty)
            if dec == "Approve":
                if qty <= 0:
                    problems.append(mat)
                else:
                    decisions[int(rid)] = (qty, "")
            elif dec == "Reject":
                decisions[int(rid)] = (0.0, why.strip() if isinstance(why, str) and why.strip() else "Not approved")
        if problems:
            st.error("Enter a quantity above 0, or choose Reject, for: " + ", ".join(problems))
        elif not decisions:
            st.warning("Nothing to confirm: every row is set to Decide later.")
        else:
            err = decide_mat_requests(decisions, user, is_ceo)
            if err:
                st.error(err)
            else:
                ok_n = sum(q > 0 for q, _ in decisions.values())
                done(f"{ok_n} approved, {len(decisions) - ok_n} rejected, {len(pending) - len(decisions)} left for later.")


def section_new(sites, user):
    field = pick_field(sites, K("qf"))
    sid = int(sites[sites.name == field].id.iloc[0])
    labels = {f"{r.name} ({r.unit})": int(r.id) for r in load_materials().itertuples()}
    st.subheader("New request")
    st.caption("The request goes to the Accountant and to the CEO. Whoever is available approves it. Choose the material "
               "from the list, or type a new material and its unit in the last two columns.")
    needed = st.date_input("Needed by", date.today() + timedelta(days=7), min_value=date.today(), key=K("qd"))
    grid = pd.DataFrame({"Material": [None] * 4, "Qty": [0.0] * 4, "New material": [""] * 4, "Unit": [""] * 4, "Note": [""] * 4})
    ed = wide(st.data_editor, grid, num_rows="dynamic", hide_index=True, key=K("qg"), column_config={
        "Material": st.column_config.SelectboxColumn("Material", options=list(labels) or ["(list is empty)"], required=False),
        "Qty": st.column_config.NumberColumn("Quantity", min_value=0.0, step=0.01),
        "New material": st.column_config.TextColumn("New material (if not in the list)"),
        "Unit": st.column_config.TextColumn("Unit (new material)"),
        "Note": st.column_config.TextColumn("What it is for (optional)")})
    ok, bad = parse_req(ed, labels)
    if ok:
        st.write(f"**{len(ok)} line(s) ready to send.**")
    for msg in bad:
        st.warning(msg)
    if st.button("Send request", type="primary"):
        if bad:
            st.error("Please correct the lines marked above.")
        elif not ok:
            st.info("Add at least one material.")
        else:
            b = create_mat_request(sid, labels, ok, needed, user)
            done(f"Request #{b} sent with {len(ok)} line(s).")


def section_mine(reqs, user):
    mine = reqs[(reqs.Status == "Pending") & (reqs["Requested by"] == user)]
    if mine.empty:
        return
    st.subheader("My pending requests")
    st.caption("You can still change these. Edit a quantity or tick Remove, then press Save changes.")
    tbl = pd.DataFrame({"Request #": mine.batch.astype(int).values, "Field": mine.Field.values, "Material": mine.Material.values,
                        "Unit": mine.Unit.values, "Quantity": mine.Requested.values, "Remove": False})
    ed = wide(st.data_editor, tbl, hide_index=True, key=f"{K('mp')}_{len(mine)}_{int(mine.id.iloc[0])}",
              disabled=["Request #", "Field", "Material", "Unit"],
              column_config={"Quantity": st.column_config.NumberColumn(min_value=0.01, step=0.01),
                             "Remove": st.column_config.CheckboxColumn()})
    if st.button("Save changes", key="mp_save"):
        removes = [int(i) for i, rm in zip(mine.id, ed["Remove"]) if rm]
        changes = {int(i): float(q) for i, q, rm, old in zip(mine.id, ed["Quantity"], ed["Remove"], mine.Requested)
                   if not rm and pd.notna(q) and abs(float(q) - float(old)) > 1e-9}
        if not removes and not changes:
            st.info("Nothing to change.")
        else:
            skipped = update_mat_pending(user, changes, removes)
            done("Changes saved." + (f" {skipped} line(s) could not be changed because they were already decided." if skipped else ""))


def section_delivery(reqs, user, uid):
    appr = reqs[reqs.Status == "Approved"]
    if appr.empty:
        return
    st.subheader("Approved: record the delivery")
    st.caption("When the materials arrive, check the quantity received and press Record delivery. The quantity is added to your stock.")
    tbl = pd.DataFrame({"Request #": appr.batch.astype(int).values, "Field": appr.Field.values, "Material": appr.Material.values,
                        "Unit": appr.Unit.values, "Approved": appr.Approved.values, "Received": appr.Approved.values})
    ed = wide(st.data_editor, tbl, hide_index=True, key=f"{K('dl')}_{len(appr)}_{int(appr.id.iloc[0])}",
              disabled=["Request #", "Field", "Material", "Unit", "Approved"],
              column_config={"Received": st.column_config.NumberColumn("Received", min_value=0.0, step=0.01)})
    if st.button("Record delivery", type="primary", key="dl_go"):
        deliv = {int(i): float(q) for i, q in zip(appr.id, ed["Received"]) if pd.notna(q) and float(q) > 0}
        if not deliv:
            st.info("Enter the quantity received for at least one line.")
        else:
            done(f"{deliver_mat_lines(deliv, user, uid)} delivery line(s) recorded. The stock was updated.")


def matreq_all(reqs):
    if reqs.empty:
        st.caption("No requests yet.")
    else:
        c1, c2 = st.columns([2, 3])
        status = c1.selectbox("Show", ["All", "Pending", "Approved", "Delivered", "Rejected"], key="cr_st")
        with c2:
            q = search_box("cr_q", "Search material, field or person", "Type part of a name…")
        view = reqs if status == "All" else reqs[reqs.Status == status]
        view = match(view, ["Material", "Field", "Requested by", "Decided by"], q)
        cols = ["batch", "Date", "Field", "Material", "Unit", "Requested", "Approved", "Delivered", "Status", "Needed by", "Requested by", "Decided by", "Reason"]
        shown = view[cols].rename(columns={"batch": "Request #"}).reset_index(drop=True)
        shown.insert(0, "No", range(1, len(shown) + 1))
        st.caption(f"{len(shown)} lines")
        wide(st.dataframe, pretty(shown, status_col="Status", qty_cols=["Requested", "Approved", "Delivered"]), hide_index=True)
        if len(shown):
            st.download_button(":material/download: Download Excel", to_excel({"Material requests": shown}),
                               f"material_requests_{date.today()}.xlsx", mime=XLSX, key="dl_ceo_req")


def matreq_ceo():
    user, uid = st.session_state.user, st.session_state.uid
    sites = visible_sites()
    if sites.empty:
        return no_field_message()
    ids = [int(i) for i in sites.id]
    reqs = load_mat_requests(ids)
    waiting = int(reqs[reqs.Status == "Pending"].batch.nunique()) if len(reqs) else 0
    t1, t2, t3 = st.tabs([f"Waiting for approval ({waiting})", "Add materials (Excel sheet)", "All requests"])
    with t1:
        section_approve(reqs, sites.iloc[:1], user, True)  # the Field drop-down at the top already chooses the field
    with t2:
        st.caption("You do not send requests. Add the materials directly in the sheet of their category: the same table as the workbook.")
        with st.expander("Add rows", expanded=True):
            add_cost_rows_form(sites, "cmx")
        exp = load_expenses(ids)
        st.subheader("Category sheets")
        a, b = st.columns([2, 3])
        cats = categories_df().name.tolist()
        cat = a.selectbox("Category", ["All categories"] + cats, key="cm_cat")
        with b:
            q = search_box("cm_q", "Search item", "Type part of an item name…")
        view = exp if cat == "All categories" else exp[exp.Category == cat]
        view = cost_frame(match(view, ["Item"], q))
        extras = (["Category"] if cat == "All categories" else []) + (["Field"] if len(sites) > 1 else []) + ["Entered by"]
        ticked = sheet_view(view, "cmv", extras, removable=True)
        c1, c2 = st.columns(2)
        with c1:
            if len(view):
                download_workbook(view, "materials", "cm_wb")
        with c2:
            if ticked and st.button("Delete ticked rows", key="cm_del"):
                with conn() as c:
                    for rid in ticked:
                        c.execute("DELETE FROM expenses WHERE id=?", (rid,))
                done(f"{len(ticked)} row(s) deleted.")
    with t3:
        matreq_all(reqs)


def matreq_page():
    """Material requests for the Accountant, Store manager and Field manager (the CEO has his own page)."""
    if has(CEO):
        return matreq_ceo()
    user, uid = st.session_state.user, st.session_state.uid
    sites = visible_sites()
    if sites.empty:
        return no_field_message()
    reqs = load_mat_requests([int(i) for i in sites.id])
    pend = reqs[(reqs.Status == "Pending") & (reqs["Requested by"] != user)] if len(reqs) else reqs
    to_deliver = reqs[reqs.Status == "Approved"] if len(reqs) else reqs
    names = []
    if has(ACCOUNTANT):
        names.append(f"Waiting for approval ({int(pend.batch.nunique()) if len(pend) else 0})")
    if has(STORE):
        names.append(f"Record delivery ({len(to_deliver)})")
    names += ["New request", "All requests"]
    tabs = st.tabs(names)
    i = 0
    if has(ACCOUNTANT):
        with tabs[i]:
            section_approve(reqs, sites.iloc[:1], user, False)
        i += 1
    if has(STORE):
        with tabs[i]:
            section_delivery(reqs, user, uid)
        i += 1
    with tabs[i]:
        section_new(sites, user)
        section_mine(reqs, user)
    with tabs[i + 1]:
        matreq_all(reqs)
