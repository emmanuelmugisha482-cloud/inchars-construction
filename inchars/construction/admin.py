"""Office pages: Users, Fields & categories, Import from Excel, and Reports."""

import pandas as pd
import streamlit as st
from datetime import date, datetime, timedelta

from inchars.accounts import (
    add_user_roles,
    categories_df,
    delete_user_full,
    has,
    no_field_message,
    set_user_password,
    set_user_roles,
    set_user_sites,
    visible_sites,
)
from inchars.data import load_expenses, load_funds
from inchars.database import IntegrityErrors, bulk_insert, conn, df, run
from inchars.settings import ACCOUNTANT, ALIASES, CEO, FIELD, FIELD_ROLES, FUNDS_VISIBLE, ROLES, SHEET_TO_CATEGORY, XLSX
from inchars.widgets import (
    K,
    c_donut,
    cost_frame,
    done,
    download_sheet,
    download_workbook,
    match,
    money,
    norm,
    pretty,
    search_box,
    sheet_view,
    to_excel,
    top_other,
    wide,
)


# ======================================================================
# from pages/users.py
# ======================================================================

def page_users():
    if not has(CEO):
        return st.error("Only the CEO can manage users.")
    me = st.session_state.uid
    sites = df("SELECT id, name FROM sites ORDER BY name")
    site_ids = dict(zip(sites.name, sites.id.astype(int)))
    users = df("SELECT id, username, role, email FROM users ORDER BY username")
    links, rl = df("SELECT u.user_id, s.name FROM user_sites u JOIN sites s ON s.id=u.site_id"), df("SELECT user_id, role FROM user_roles")
    fields_of = {int(k): sorted(v.tolist()) for k, v in links.groupby("user_id")["name"]} if len(links) else {}
    roles_of = {int(k): sorted(v.tolist()) for k, v in rl.groupby("user_id")["role"]} if len(rl) else {}
    show = pd.DataFrame({
        "User name": users.username.values,
        "Roles": [", ".join(roles_of.get(int(i), [r])) for i, r in zip(users.id, users.role)],
        "Email": users.email.fillna("").values,
        "Fields": ["All fields" if CEO in roles_of.get(int(i), [r]) else ", ".join(fields_of.get(int(i), []))
                   for i, r in zip(users.id, users.role)]})
    q = search_box("q_users", "Search user", "Type part of a name…")
    view = match(show, ["User name", "Roles", "Email", "Fields"], q).reset_index(drop=True)
    st.caption("Click a row to edit that user, or choose the user from the list below the table.")
    try:
        ev = wide(st.dataframe, view, hide_index=True, on_select="rerun", selection_mode="single-row", key="users_tbl")
        rows = [r for r in (ev.selection.rows if ev is not None else []) if r < len(view)]  # a row that is no longer in the list is ignored
    except TypeError:  # an older Streamlit without row selection: the list below is used
        wide(st.dataframe, view, hide_index=True)
        rows = []
    with st.expander("Add a user"):
        st.caption("Choose one or more roles. The CEO sees all fields. Accountant, Store manager and Field manager need at least one field. "
                   "Receptionist and Engineer work on services (jobs for customers) and need no field.")
        a1, a2 = st.columns(2)
        nu = a1.text_input("User name", key=K("nu"))
        nroles = a2.multiselect("Roles", ROLES, default=[FIELD], key=K("nr"))
        ne = a1.text_input("Email (optional, used to reset a forgotten password)", key=K("ne"))
        npw = a2.text_input("Temporary password (at least 8 characters)", type="password", key=K("np"))
        ns = st.multiselect("Fields this user works on", list(site_ids), key=K("ns")) if nroles and CEO not in nroles and any(r in FIELD_ROLES for r in nroles) else []
        if st.button("Add user"):
            err = None
            if not nroles:
                err = "Choose at least one role."
            elif CEO not in nroles and any(r in FIELD_ROLES for r in nroles) and not ns:
                err = "Choose at least one field for this user." if site_ids else "Create a field first (Fields & categories)."
            err = err or add_user_roles(nu, nroles, ne, npw)
            if err:
                st.error(err)
            else:
                set_user_sites(int(df("SELECT id FROM users WHERE lower(username)=lower(?)", (nu.strip(),))["id"][0]),
                               [site_ids[n] for n in ns])
                done(f"User {nu.strip()} added.")
    labels = {f"{u} ({show.Roles[i]})": int(users.id[i]) for i, u in enumerate(users.username)}
    who = st.selectbox("Or choose a user from the list", list(labels), index=None, placeholder="Search and choose a user", key=K("wu"))
    uid = labels[who] if who else (int(users[users.username == view["User name"].iloc[rows[0]]].id.iloc[0]) if rows else None)
    if uid is None:
        return
    uname = users[users.id == uid].username.iloc[0]
    st.subheader(f"Edit {uname}")
    st.caption("The user gets the pages of every role you tick the next time they open a page.")
    cur_roles, cur_fields = roles_of.get(uid, []), fields_of.get(uid, [])
    nr = st.multiselect("Roles", ROLES, default=[r for r in cur_roles if r in ROLES], key=K(f"er{uid}"))
    nf = st.multiselect("Fields this user works on", list(site_ids), default=[f for f in cur_fields if f in site_ids],
                        key=K(f"ef{uid}")) if CEO not in nr and any(r in FIELD_ROLES for r in nr) else cur_fields
    if st.button("Save roles and fields"):
        ceos = [i for i, r in roles_of.items() if CEO in r]
        if not nr:
            st.error("Choose at least one role.")
        elif CEO not in nr and any(r in FIELD_ROLES for r in nr) and not nf:
            st.error("An Accountant, Store manager or Field manager needs at least one field.")
        elif uid in ceos and CEO not in nr and len(ceos) <= 1:
            st.error("There must be at least one CEO.")
        else:
            set_user_roles(uid, nr)
            if CEO not in nr:
                set_user_sites(uid, [site_ids[n] for n in nf] if any(r in FIELD_ROLES for r in nr) else [])
            done(f"Roles and fields saved for {uname}.")
    p1, p2 = st.columns(2)
    pw = p1.text_input("New password (at least 8 characters)", type="password", key=K("wp"))
    if p1.button("Set new password"):
        err = set_user_password(uid, pw)
        if err:
            st.error(err)
        else:
            done(f"Password changed for {uname}.")
    sure = p2.checkbox("I am sure I want to delete this user", key=K("ws"))
    if p2.button("Delete this user"):
        err = delete_user_full(uid, me) if sure else "Tick the box to confirm."
        if err:
            st.error(err)
        else:
            done(f"{uname} deleted.")


# ======================================================================
# from pages/setup.py
# ======================================================================

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


# ======================================================================
# from pages/import_costs.py
# ======================================================================

def _num(v):
    try:
        if v is None or isinstance(v, bool):
            return None
        f = float(v)
        return None if f != f else f
    except (TypeError, ValueError):
        return None


def parse_cost_workbook(file):
    """Reads a workbook like the KDBS one: one sheet per cost category, plus a SUMMARY sheet with the funds."""
    from openpyxl import load_workbook
    wb = load_workbook(file, read_only=True, data_only=True)
    rows, checks, skipped, funds = [], [], 0, None
    for name in wb.sheetnames:
        key, data = norm(name), list(wb[name].iter_rows(values_only=True))
        if key == "summary":
            for r in data:
                text = " ".join(str(c) for c in r if isinstance(c, str))
                if norm(text).startswith("recordedmoneypaid"):
                    nums = [_num(c) for c in r if _num(c)]
                    funds = int(nums[0]) if nums else funds
            continue
        if key in ("dashboard", "pivots", "dashcalc"):
            continue
        hdr = None
        for i, r in enumerate(data[:20]):
            cells = [norm(c) for c in r]
            col = {k: next((j for j, c in enumerate(cells) if c in al), None) for k, al in ALIASES.items()}
            if col["item"] is not None and col["date"] is not None and col["total"] is not None:
                hdr = (i, col)
                break
        if hdr is None:
            continue
        i0, col = hdr
        cat, sheet_total, got, amount = SHEET_TO_CATEGORY.get(key, name.strip()), None, 0, 0
        for r in data[i0 + 1:]:
            cell = lambda j: r[j] if j is not None and j < len(r) else None
            if any(isinstance(c, str) and "grandtotal" in norm(c) for c in r):
                sheet_total = _num(cell(col["total"]))
                if sheet_total is None:
                    nums = [x for x in (_num(c) for c in r) if x]
                    sheet_total = nums[-1] if nums else None
                break
            item, tot, qty, price, d = cell(col["item"]), _num(cell(col["total"])), _num(cell(col["qty"])), _num(cell(col["price"])), cell(col["date"])
            if not isinstance(item, str) or not item.strip():
                continue
            if tot is None and qty is not None and price is not None:
                tot = qty * price
            if not tot:
                continue
            if isinstance(d, datetime):
                d = d.date()
            if not isinstance(d, date):
                skipped += 1
                continue
            qty = qty if qty else 1.0
            rows.append({"Category": cat, "Item": item.strip(), "Qty": qty, "Unit price": price if price else tot / qty,
                         "Total": int(round(tot)), "Date": d})
            got += 1
            amount += int(round(tot))
        checks.append({"Category": cat, "Rows": got, "Imported": amount, "Sheet total": int(sheet_total or 0)})
    return pd.DataFrame(rows), pd.DataFrame(checks), skipped, funds


def import_costs(site_id, rows, user, fname, funds=None, funds_date=None):
    now = datetime.now().isoformat(timespec="seconds")
    with conn() as c:
        have = {r["name"]: r["id"] for r in c.execute("SELECT id, name FROM categories").fetchall()}
        for n in sorted(set(rows.Category) - set(have)):
            have[n] = c.execute("INSERT INTO categories(name) VALUES(?) RETURNING id", (n,)).fetchone()["id"]
        bulk_insert(c, "expenses", ["site_id", "category_id", "item", "qty", "unit_price", "total", "spent_on",
                                    "entered_by", "entered_at", "source"],
                    [(site_id, have[r["Category"]], r["Item"], float(r["Qty"]), float(r["Unit price"]), int(r["Total"]),
                      str(r["Date"]), user, now, f"import:{fname}") for r in rows.to_dict("records")])
        if funds:
            c.execute("INSERT INTO funds(site_id, amount, received_on, source, note, entered_by) VALUES(?,?,?,?,?,?)",
                      (site_id, int(funds), str(funds_date), "Imported", f"From the SUMMARY sheet of {fname}", user))


def page_import():
    if not has(CEO, ACCOUNTANT):
        return st.error("Only the CEO and the Accountant can import workbooks.")
    st.write("Upload your cost workbook (one sheet per cost category, with a SUMMARY sheet). Every row is checked "
             "before anything is saved.")
    up = st.file_uploader("Excel workbook (.xlsx)", type=["xlsx"], key=K("imp"))
    if up is None:
        return
    try:
        rows, checks, skipped, funds = parse_cost_workbook(up)
    except Exception as e:
        return st.error(f"Could not read the workbook: {e}")
    if rows.empty:
        return st.error("No cost rows were found. Each category sheet needs the columns: item name, quantity, price, total, date.")
    checks["Check"] = ["OK" if abs(a - b) < 1 else "Differs" for a, b in zip(checks.Imported, checks["Sheet total"])]
    st.write(f"**{len(rows)} rows found in {len(checks)} category sheets, total {money(rows.Total.sum())} Frw.**")
    wide(st.dataframe, pretty(checks, ["Imported", "Sheet total"]), hide_index=True)
    if skipped:
        st.warning(f"{skipped} row(s) have no valid date and will not be imported.")
    if (checks.Check == "Differs").any():
        st.warning("Some sheets differ from their own grand total (rows without a date or an amount). Check them in Excel.")
    sites = visible_sites()
    site_ids = dict(zip(sites.name, sites.id.astype(int)))
    options = (["New field…"] if has(CEO) else []) + list(site_ids)
    if not options:
        return no_field_message()
    target = st.selectbox("Import into which field?", options, key=K("it"))
    new_name = new_client = ""
    if target == "New field…":
        n1, n2 = st.columns(2)
        new_name = n1.text_input("New field name", key=K("inn"))
        new_client = n2.text_input("Client", key=K("inc"))
    add_funds = False
    if funds and has(*FUNDS_VISIBLE):
        add_funds = st.checkbox(f"Also record the funds received found in the SUMMARY sheet ({money(funds)} Frw)", value=True, key=K("ifd"))
    again = True
    if target != "New field…":
        n_old = int(df("SELECT COUNT(*) AS n FROM expenses WHERE site_id=? AND source LIKE ?", (site_ids[target], "import:%"))["n"][0])
        if n_old:
            st.warning(f"{target} already has {n_old} imported entries. Importing again would add the same entries a second time.")
            again = st.checkbox("I understand, import again", key=K("iag"))
    if st.button("Import", type="primary"):
        if target == "New field…" and not new_name.strip():
            st.warning("Write the name of the new field.")
        elif not again:
            st.warning("Tick the box to confirm.")
        else:
            try:
                sid = add_site(new_name, new_client, "", 0, rows.Date.min()) if target == "New field…" else site_ids[target]
            except IntegrityErrors:
                return st.error("A field with that name already exists. Choose it from the list.")
            import_costs(sid, rows, st.session_state.user, up.name, funds if add_funds else None, rows.Date.max())
            done(f"Imported {len(rows)} entries into {new_name.strip() if target == 'New field…' else target}.")


# ======================================================================
# from pages/reports.py
# ======================================================================

def reports_page():
    sites = visible_sites()
    if sites.empty:
        return no_field_message()
    ids = [int(i) for i in sites.id]
    field = st.session_state.get("ceo_field", "All fields")
    exp = load_expenses(ids)
    if exp.empty:
        return st.info("No spending recorded yet.")
    lo, hi = date.fromisoformat(exp.Date.min()), max(date.fromisoformat(exp.Date.max()), date.today())
    a, b = st.columns(2)
    d_from, d_to = a.date_input("From", lo, key=f"cr_from_{field}"), b.date_input("To", hi, key=f"cr_to_{field}")
    sel = exp[(exp.Date >= str(d_from)) & (exp.Date <= str(d_to))]
    if sel.empty:
        return st.info("No spending in this period.")
    st.subheader("Summary")
    by_cat = sel.groupby("Category").agg(Amount=("Total", "sum"), Transactions=("Total", "size")).reset_index().sort_values("Amount", ascending=False)
    by_cat["Share %"] = (by_cat.Amount / by_cat.Amount.sum() * 100).round(1)
    by_month = sel.assign(Month=sel.Date.str[:7]).groupby("Month").Total.sum().reset_index().rename(columns={"Total": "Amount"})
    st.markdown(f"**{len(sel)} transactions · Total {money(sel.Total.sum())} Frw**")
    s1, s2 = st.columns([3, 2])
    with s1:
        wide(st.dataframe, pretty(by_cat, ["Amount"]), hide_index=True)
    with s2:
        c_donut(top_other(sel, "Category", "Total"), "Category", "Total")
    sheets = {"By category": by_cat, "By month": by_month}
    per = sites.rename(columns={"name": "Field"})[["id", "Field"]].copy()
    per["Spent"] = per.id.map(exp.groupby("site_id").Total.sum()).fillna(0).astype(int)
    money_cols = ["Spent"]
    if has(*FUNDS_VISIBLE):
        fnd = load_funds(ids)
        per.insert(1, "Funds received", per.id.map(fnd.groupby("site_id").Amount.sum()).fillna(0).astype(int))
        per["Balance"] = per["Funds received"] - per["Spent"]
        money_cols = ["Funds received", "Spent", "Balance"]
    sheets["By field" if not has(*FUNDS_VISIBLE) else "Funds and balance"] = per.drop(columns="id")
    if len(sites) > 1:
        st.markdown("**By field**" if not has(*FUNDS_VISIBLE) else "**Funds and balance by field**")
        wide(st.dataframe, pretty(per.drop(columns="id"), money_cols), hide_index=True)
    st.download_button(":material/download: Download the summary (Excel)", to_excel(sheets), f"summary_{norm(field) or 'all'}_{date.today()}.xlsx",
                       mime=XLSX, key="dl_cr_sum")

    st.subheader("Category report")
    st.caption("Choose a category to see its sheet: No, Date, Item, Qty, P/U, Total.")
    cat = st.selectbox("Category", ["All categories"] + sorted(set(sel.Category)), key="cr_cat")
    view = sel if cat == "All categories" else sel[sel.Category == cat]
    view = cost_frame(view)
    extras = (["Category"] if cat == "All categories" else []) + (["Field"] if len(sites) > 1 else [])
    sheet_view(view, "crv", extras)
    c1, c2 = st.columns(2)
    with c1:
        download_sheet(view, extras, cat if cat != "All categories" else "All categories", "cr_dl", "Download this sheet (Excel)")
    with c2:
        download_workbook(cost_frame(sel), "report_all_categories", "cr_wb", "Download all categories (one sheet each)")
