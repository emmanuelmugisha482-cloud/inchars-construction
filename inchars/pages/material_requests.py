"""Material requests page for every role."""

from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st

from inchars.accounts import has
from inchars.components.common import K, done, match, pretty, search_box, to_excel, wide
from inchars.components.sheets import add_cost_rows_form, cost_frame, download_workbook, sheet_view
from inchars.config import ACCOUNTANT, CEO, STORE, XLSX
from inchars.data.expenses import load_expenses
from inchars.data.materials import (
    create_mat_request,
    decide_mat_requests,
    deliver_mat_lines,
    load_mat_requests,
    load_materials,
    load_moves,
    parse_req,
    stock_balances,
    update_mat_pending,
)
from inchars.data.scope import categories_df, no_field_message, visible_sites
from inchars.db import conn
from inchars.pages.store import pick_field


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
        status = c1.radio("Show", ["All", "Pending", "Approved", "Delivered", "Rejected"], horizontal=True, key="cr_st")
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
