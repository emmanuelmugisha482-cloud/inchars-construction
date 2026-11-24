"""Money: Expenses, Funds received, Money requests, Money used, and Taxes & debts."""

import pandas as pd
import streamlit as st
from datetime import date, datetime, timedelta

from inchars.accounts import categories_df, has, no_field_message, visible_sites
from inchars.data import (
    create_money_request,
    decide_money_requests,
    delete_expenses,
    field_choices,
    load_expenses,
    load_funds,
    load_ledger,
    load_money_requests,
    money_cats,
    pay_money_requests,
    service_only,
    update_money_pending,
)
from inchars.database import bulk_insert, conn, df, ensure_named, run
from inchars.settings import ACCOUNTANT, CEO, TAX_TYPES
from inchars.widgets import (
    CORE,
    K,
    add_cost_rows_form,
    bars,
    category_pick,
    cost_frame,
    done,
    download_sheet,
    download_workbook,
    field_target,
    match,
    money,
    parse_sheet,
    pretty,
    search_box,
    sheet_editor,
    sheet_number,
    sheet_preview,
    sheet_show,
    sheet_view,
    wide,
)


# ======================================================================
# from pages/expenses.py
# ======================================================================

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


# ======================================================================
# from pages/funds.py
# ======================================================================

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


# ======================================================================
# from pages/money_requests.py
# ======================================================================

def money_pay_block(reqs, user):
    appr = reqs[(reqs.Status == "Approved") & (reqs["Requested by"] != user)]
    if appr.empty:
        st.success("No approved request is waiting for payment.")
    else:
        st.caption("Tick Paid when the money has been given. It is then added to the person's money used.")
        tbl = pd.DataFrame({"Request #": appr.id.values, "By": appr["Requested by"].values, "Field": appr.Field.values,
                            "Category": appr.Category.values, "Purpose": appr.Purpose.values, "Approved": appr.Approved.values, "Paid": False})
        ed = wide(st.data_editor, tbl, hide_index=True, key=f"{K('mx')}_{len(appr)}_{int(appr.id.iloc[0])}",
                  disabled=["Request #", "By", "Field", "Category", "Purpose", "Approved"], column_config={"Paid": st.column_config.CheckboxColumn()})
        if st.button("Record payment", type="primary", key="cmx_go"):
            pids = [int(i) for i, p in zip(appr.id, ed["Paid"]) if p]
            if not pids:
                st.info("Tick the requests that were paid.")
            else:
                done(f"{pay_money_requests(pids, user)} payment(s) recorded.")


def money_all(reqs):
    if reqs.empty:
        return st.caption("No money requests yet.")
    a, b, c = st.columns([2, 2, 3])
    status = a.selectbox("Show", ["All", "Pending", "Approved", "Paid", "Rejected"], key="cmr_st")
    cat = b.selectbox("Category", ["All categories"] + sorted(set(categories_df().name) | set(reqs.Category.dropna())), key="cmr_cat")
    with c:
        q = search_box("cmr_q", "Search purpose, category or person", "Type part of a word…")
    view = reqs if status == "All" else reqs[reqs.Status == status]
    view = view if cat == "All categories" else view[view.Category == cat]
    view = match(view, ["Purpose", "Category", "Requested by", "Field"], q)
    sheet = view.assign(Item=view.Purpose, Qty=1.0, **{"P/U": view.Requested, "Total": view.Requested})
    extras = ["Category", "Field", "Approved", "Status", "Needed by", "Requested by", "Decided by", "Reason", "Paid on"]
    t = sheet_number(sheet, CORE + extras)
    st.markdown(f"**{len(t)} requests · Grand total {money(pd.to_numeric(t.Total).sum())} Frw**")
    wide(st.dataframe, pretty(t, ["P/U", "Total", "Approved"], "Status", qty_cols=["Qty"]), hide_index=True)
    download_sheet(sheet, extras, "Money requests", "cmr_dl")


def moneyreq_ceo():
    user = st.session_state.user
    field = st.session_state.get("ceo_field", "All fields")
    reqs = load_money_requests()
    if field != "All fields":
        reqs = reqs[reqs.Field == field]
    reqs = reqs.reset_index(drop=True)
    pending = reqs[reqs.Status == "Pending"]
    t1, t2, t3 = st.tabs([f"Waiting for your decision ({len(pending)})", "Approved: record the payment", "All requests"])
    with t1:
        if pending.empty:
            st.success("No pending money requests.")
        else:
            tbl = pd.DataFrame({"Request #": pending.id.values, "By": pending["Requested by"].values, "Field": pending.Field.values,
                                "Category": pending.Category.values, "Purpose": pending.Purpose.values, "Requested": pending.Requested.values,
                                "Needed by": pending["Needed by"].values, "Decision": "Approve", "Amount to approve": pending.Requested.values,
                                "Reason if rejected": ""})
            ed = wide(st.data_editor, tbl, hide_index=True, key=f"mrd{K('d')}_{abs(hash(tuple(int(i) for i in pending.id)))}",
                      disabled=["Request #", "By", "Field", "Category", "Purpose", "Requested", "Needed by"], column_config={
                          "Decision": st.column_config.SelectboxColumn(options=["Approve", "Reject", "Decide later"], required=True),
                          "Amount to approve": st.column_config.NumberColumn(min_value=0, step=1000)})
            if st.button(":material/check: Confirm decisions", type="primary", key="cmrd_go"):
                decisions, problems = {}, []
                for rid, dec, amt, why in zip(pending.id, ed["Decision"], ed["Amount to approve"], ed["Reason if rejected"]):
                    amt = 0 if pd.isna(amt) else float(amt)
                    if dec == "Approve":
                        if amt <= 0:
                            problems.append(f"#{int(rid)}")
                        else:
                            decisions[int(rid)] = (amt, "")
                    elif dec == "Reject":
                        decisions[int(rid)] = (0, why.strip() if isinstance(why, str) and why.strip() else "Not approved")
                if problems:
                    st.error("Enter an amount above 0, or choose Reject, for: " + ", ".join(problems))
                elif not decisions:
                    st.warning("Nothing to confirm: every row is set to Decide later.")
                else:
                    err = decide_money_requests(decisions, user)
                    if err:
                        st.error(err)
                    else:
                        ok_n = sum(a > 0 for a, _ in decisions.values())
                        done(f"{ok_n} approved, {len(decisions) - ok_n} rejected.")
    with t2:
        money_pay_block(reqs, user)
    with t3:
        money_all(reqs)


def money_new_block(sites, reqs, user):
    st.subheader("New money request")
    st.caption("Ask the CEO for money, for example for fuel, transport, tools or materials.")
    fc = field_choices(sites)
    if service_only():  # Receptionist / Engineer: no field, and only Office use or Personal use
        st.caption("Choose Office use for things the office needs, or Personal use for yourself.")
        field, newcat = "(no field)", ""
        cat = st.selectbox("Category", money_cats(), key=K("mc"))
    else:
        a1, a2, a3 = st.columns(3)
        field = a1.selectbox("Field", list(fc), key=K("mf"))
        cat = a2.selectbox("Category", money_cats(), key=K("mc"))
        newcat = a3.text_input("or a new category", key=K("mn"))
    b1, b2, b3 = st.columns([1, 2, 1])
    amount = b1.number_input("Amount (Frw)", min_value=1, step=1000, value=None, placeholder="Enter amount", key=K("ma"))
    purpose = b2.text_input("What is it for?", key=K("mp"))
    needed = b3.date_input("Needed by", date.today() + timedelta(days=2), min_value=date.today(), key=K("md"))
    if st.button("Send request to the CEO", type="primary", key="mr_send"):
        if not amount or not purpose.strip():
            st.warning("Enter the amount and what it is for.")
        else:
            create_money_request(fc[field], cat, newcat, purpose, amount, needed, user)
            done(f"Request for {money(amount)} Frw sent to the CEO.")
    mine = reqs[(reqs.Status == "Pending") & (reqs["Requested by"] == user)]
    if len(mine):
        st.subheader("My pending requests")
        tbl = pd.DataFrame({"Request #": mine.id.values, "Category": mine.Category.values, "Purpose": mine.Purpose.values,
                            "Amount": mine.Requested.values, "Remove": False})
        ed = wide(st.data_editor, tbl, hide_index=True, key=f"{K('mq')}_{len(mine)}_{int(mine.id.iloc[0])}",
                  disabled=["Request #", "Category", "Purpose"], column_config={"Amount": st.column_config.NumberColumn(min_value=1, step=1000),
                                                                                "Remove": st.column_config.CheckboxColumn()})
        if st.button("Save changes", key="mq_save"):
            removes = [int(i) for i, rm in zip(mine.id, ed["Remove"]) if rm]
            changes = {int(i): float(a) for i, a, rm, old in zip(mine.id, ed["Amount"], ed["Remove"], mine.Requested)
                       if not rm and pd.notna(a) and float(a) != float(old)}
            if not removes and not changes:
                st.info("Nothing to change.")
            else:
                skipped = update_money_pending(user, changes, removes)
                done("Changes saved." + (f" {skipped} could not be changed because they were already decided." if skipped else ""))


def moneyreq_page():
    """Money requests for the Accountant, Store manager and Field manager (the CEO has his own page)."""
    if has(CEO):
        return moneyreq_ceo()
    user = st.session_state.user
    field = st.session_state.get("ceo_field", "All fields")
    sites = visible_sites()
    reqs = load_money_requests()
    if field != "All fields":
        reqs = reqs[reqs.Field == field]
    reqs = reqs.reset_index(drop=True)
    pay = reqs[(reqs.Status == "Approved") & (reqs["Requested by"] != user)] if has(ACCOUNTANT) else reqs.iloc[0:0]
    names = ["New request"] + ([f"Approved: record the payment ({len(pay)})"] if has(ACCOUNTANT) else []) + ["All requests"]
    tabs = st.tabs(names)
    with tabs[0]:
        money_new_block(sites, reqs, user)
    if has(ACCOUNTANT):
        with tabs[1]:
            money_pay_block(reqs, user)
    with tabs[-1]:
        money_all(reqs)


# ======================================================================
# from pages/money_used.py
# ======================================================================

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
    kind = a.selectbox("Show", ["All", "Business", "Personal"], key="mu_show")
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


# ======================================================================
# from pages/taxes_debts.py
# ======================================================================

def page_taxdebt():
    if not has(CEO, ACCOUNTANT):
        return st.error("This page is for the CEO and the Accountant.")
    user = st.session_state.user
    fc = field_choices(visible_sites())
    st.subheader("Taxes paid")
    a1, a2, a3 = st.columns(3)
    ttype = a1.selectbox("Tax", TAX_TYPES, key=K("tt"))
    period = a2.text_input("Period", placeholder="For example October 2026", key=K("tp"))
    tamount = a3.number_input("Amount (Frw)", min_value=1, step=1000, value=None, placeholder="Enter amount", key=K("ta"))
    b1, b2, b3 = st.columns(3)
    tday = b1.date_input("Date paid", date.today(), max_value=date.today(), key=K("td"))
    tref = b2.text_input("Receipt or reference", key=K("tr"))
    tfield = b3.selectbox("Field (optional)", list(fc), key=K("tf"))
    if st.button("Save tax payment", type="primary"):
        if not tamount:
            st.warning("Enter the amount.")
        else:
            run("INSERT INTO taxes(tax_type, period, amount, paid_on, reference, site_id, entered_by) VALUES(?,?,?,?,?,?,?)",
                (ttype, period.strip(), int(tamount), str(tday), tref.strip(), fc[tfield], user))
            done(f"{money(tamount)} Frw of {ttype} recorded.")
    taxes = df("""SELECT t.paid_on AS "Date", t.tax_type AS "Tax", t.period AS "Period", t.amount AS "Amount",
                  t.reference AS "Reference", COALESCE(s.name, '(no field)') AS "Field", t.entered_by AS "By"
                  FROM taxes t LEFT JOIN sites s ON s.id=t.site_id ORDER BY t.paid_on DESC, t.id DESC""")
    if taxes.empty:
        st.caption("No tax payments recorded yet.")
    else:
        q = search_box("q_tax", "Search tax, period or reference", "Type part of a word…")
        view = match(taxes, ["Tax", "Period", "Reference", "Field"], q)
        st.caption(f"{len(view)} payments, total {money(view.Amount.sum())} Frw")
        bars(view.groupby("Tax", as_index=False).Amount.sum().sort_values("Amount", ascending=False), "Tax", "Amount")
        sheet_show(view.assign(Item=view.Tax + " " + view.Period.fillna(""), Qty=1.0, **{"P/U": view.Amount, "Total": view.Amount}),
                   ["Period", "Reference", "Field", "By"], "Taxes paid", "tx")

    st.subheader("Debts")
    st.caption("Money the company owes, or money others owe the company.")
    d1, d2, d3 = st.columns(3)
    party = d1.text_input("Who", placeholder="Supplier, bank, client…", key=K("dp"))
    direction = d2.selectbox("Direction", ["We owe", "Owed to us"], key=K("dd"))
    damount = d3.number_input("Amount (Frw)", min_value=1, step=1000, value=None, placeholder="Enter amount", key=K("da"))
    e1, e2, e3 = st.columns(3)
    due = e1.date_input("Due date", date.today() + timedelta(days=30), key=K("de"))
    dfield = e2.selectbox("Field (optional)", list(fc), key=K("df"))
    dnote = e3.text_input("Note (optional)", key=K("dn"))
    if st.button("Save debt", type="primary"):
        if not party.strip() or not damount:
            st.warning("Enter who and the amount.")
        else:
            run("INSERT INTO debts(party, direction, amount, due_on, site_id, note, entered_by, entered_on) VALUES(?,?,?,?,?,?,?,?)",
                (party.strip(), direction, int(damount), str(due), fc[dfield], dnote.strip(), user, str(date.today())))
            done(f"Debt with {party.strip()} saved.")
    debts = df("""SELECT d.id, d.party AS "Who", d.direction AS "Direction", d.amount AS "Amount",
                  COALESCE((SELECT SUM(p.amount) FROM debt_payments p WHERE p.debt_id=d.id), 0) AS "Paid",
                  d.due_on AS "Due", COALESCE(s.name, '(no field)') AS "Field", d.note AS "Note"
                  FROM debts d LEFT JOIN sites s ON s.id=d.site_id ORDER BY d.due_on""")
    if debts.empty:
        return st.caption("No debts recorded yet.")
    debts["Balance"] = debts.Amount - debts.Paid
    debts["Status"] = ["Settled" if b <= 0 else ("Overdue" if due_ < str(date.today()) else "Open") for b, due_ in zip(debts.Balance, debts.Due)]
    q2 = search_box("q_debt", "Search who, field or note", "Type part of a word…")
    view = match(debts, ["Who", "Field", "Note"], q2)
    k = st.columns(2)
    k[0].metric("We owe (open)", money(view[(view.Direction == "We owe") & (view.Balance > 0)].Balance.sum()))
    k[1].metric("Owed to us (open)", money(view[(view.Direction == "Owed to us") & (view.Balance > 0)].Balance.sum()))
    sheet_show(view.assign(Date=view.Due, Item=view.Who, Qty=1.0, **{"P/U": view.Amount, "Total": view.Amount}),
               ["Direction", "Paid", "Balance", "Status", "Field", "Note"], "Debts", "db")
    openr = debts[debts.Balance > 0]
    if len(openr):
        st.markdown("**Record a payment on a debt**")
        labels = {f"{r.Who} ({r.Direction}) - balance {money(r.Balance)}": int(r.id) for r in openr.itertuples()}
        p1, p2, p3 = st.columns(3)
        which = p1.selectbox("Debt", list(labels), index=None, placeholder="Choose a debt", key=K("pd"))
        pam = p2.number_input("Amount paid (Frw)", min_value=1, step=1000, value=None, placeholder="Enter amount", key=K("pa"))
        pday = p3.date_input("Date", date.today(), max_value=date.today(), key=K("pt"))
        if st.button("Save payment", key="dp_save"):
            if not which or not pam:
                st.warning("Choose the debt and enter the amount.")
            elif pam > float(openr[openr.id == labels[which]].Balance.iloc[0]):
                st.error("The payment is more than the balance of this debt.")
            else:
                run("INSERT INTO debt_payments(debt_id, amount, paid_on, entered_by) VALUES(?,?,?,?)", (labels[which], int(pam), str(pday), user))
                done("Payment saved.")
