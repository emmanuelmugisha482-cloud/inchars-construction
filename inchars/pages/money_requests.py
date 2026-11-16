"""Money requests page for every role."""

from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st

from inchars.accounts import has
from inchars.components.common import K, done, match, money, pretty, search_box, wide
from inchars.components.sheets import CORE, download_sheet, sheet_number
from inchars.config import ACCOUNTANT, CEO
from inchars.data.money import (
    create_money_request,
    decide_money_requests,
    field_choices,
    load_money_requests,
    money_cats,
    pay_money_requests,
    update_money_pending,
)
from inchars.data.scope import categories_df, visible_sites


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
    status = a.radio("Show", ["All", "Pending", "Approved", "Paid", "Rejected"], horizontal=True, key="cmr_st")
    cat = b.selectbox("Category", ["All categories"] + categories_df().name.tolist(), key="cmr_cat")
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
