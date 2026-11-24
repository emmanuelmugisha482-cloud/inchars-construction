"""The Dashboard page (construction and services tabs)."""

import html
import importlib
import pandas as pd
import streamlit as st
import traceback
from datetime import date, datetime, timedelta

from inchars.accounts import categories_df, has, no_field_message, visible_sites
from inchars.data import (
    load_debts,
    load_expenses,
    load_funds,
    load_ledger,
    load_mat_requests,
    load_money_requests,
    load_moves,
    load_taxes,
    stock_balances,
)
from inchars.database import df
from inchars.settings import ACCOUNTANT, CEO, DASH_CSS, FIELD, FUNDS_VISIBLE, STORE
from inchars.widgets import (
    AMBER,
    GREEN,
    RED,
    c_bar,
    c_donut,
    c_grouped,
    c_heat,
    c_lines,
    card,
    insights_html,
    kpi,
    kpi_grid,
    money,
    month_label,
    month_series,
    monthly_vals,
    pretty,
    short,
    spark_svg,
    top_other,
    trend,
    wide,
)


# ======================================================================
# from pages/dashboard.py
# ======================================================================

def services_part(name):
    """The services dashboard (jobs) is loaded here only when needed. If it is broken, only this part shows a message."""
    try:
        fn = getattr(importlib.import_module("inchars.jobs.extras"), name)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        return st.error(f"The services dashboard cannot start ({type(e).__name__}). The rest of the dashboard works. Open System check.")
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        st.error(f"The services dashboard stopped with an error ({type(e).__name__}). The rest of the dashboard works.")


def dash_alerts(sites, exp, fnd, bal, mr, reqs, debts):
    """(level, text) lines for the 'Critical issue alerts' box."""
    out = []
    for s in sites.itertuples():
        spent, got = float(exp[exp.site_id == s.id].Total.sum()), float(fnd[fnd.site_id == s.id].Amount.sum())
        if got and spent > got:
            out.append(("High", f"{s.name}: spent is {money(spent - got)} Frw more than the funds received."))
        if (s.budget or 0) > 0 and spent >= 0.9 * s.budget:
            out.append(("High" if spent >= s.budget else "Medium", f"{s.name}: {spent / s.budget * 100:.0f}% of the budget is used."))
        last = exp[exp.site_id == s.id].Date.max() if len(exp[exp.site_id == s.id]) else None
        if s.status == "Active" and last and (date.today() - date.fromisoformat(last)).days >= 14:
            out.append(("Low", f"{s.name}: no expense recorded for {(date.today() - date.fromisoformat(last)).days} days."))
    out_stock = bal[bal.Balance <= 1e-9] if len(bal) else bal
    if len(out_stock):
        names = ", ".join(sorted(set(out_stock.Material))[:3])
        out.append(("Medium", f"{len(out_stock)} material line(s) out of stock ({names}{'…' if out_stock.Material.nunique() > 3 else ''})."))
    me = st.session_state.user
    pend = reqs[reqs.Status == "Pending"] if len(reqs) else reqs
    if has(CEO, ACCOUNTANT):
        pm = int(pend[pend["Requested by"] != me].batch.nunique()) if len(pend) else 0
        if pm:
            out.append(("Medium", f"{pm} material request(s) waiting for approval."))
    else:
        pm = int(pend[pend["Requested by"] == me].batch.nunique()) if len(pend) else 0
        if pm:
            out.append(("Low", f"{pm} of your material request(s) are waiting for approval."))
    pmr = int((mr.Status == "Pending").sum()) if len(mr) else 0
    if pmr and has(CEO):
        out.append(("Medium", f"{pmr} money request(s) waiting for your decision."))
    elif pmr:
        out.append(("Low", f"{pmr} of your money request(s) are waiting for the CEO."))
    ap = int(((mr.Status == "Approved") & (mr["Requested by"] != me)).sum()) if len(mr) and has(CEO, ACCOUNTANT) else 0
    if ap:
        out.append(("Low", f"{ap} approved money request(s) not paid yet."))
    if has(STORE) and len(reqs):
        dv = int((reqs.Status == "Approved").sum())
        if dv:
            out.append(("Medium", f"{dv} approved material line(s) waiting for you to record the delivery."))
    if len(debts):
        late = debts[(debts.Due < str(date.today())) & (debts.Direction == "We owe")]
        if len(late):
            out.append(("High", f"{len(late)} overdue debt(s) we owe, {money(late.Balance.sum())} Frw."))
    return out


def dash_deadlines(mr, reqs, debts):
    rows, today, soon = [], str(date.today()), str(date.today() + timedelta(days=30))
    for _, r in mr[mr.Status.isin(["Pending", "Approved"])].iterrows():
        if r["Needed by"] and r["Needed by"] <= soon:
            rows.append({"Due": r["Needed by"], "Type": "Money request", "Field": r.Field, "Detail": f"{r.Purpose} ({money(r.Requested)} Frw)", "Status": r.Status})
    if len(reqs):
        p = reqs[reqs.Status.isin(["Pending", "Approved"])].drop_duplicates("batch")
        for _, r in p.iterrows():
            if r["Needed by"] and r["Needed by"] <= soon:
                rows.append({"Due": r["Needed by"], "Type": "Material request", "Field": r.Field, "Detail": f"Request #{int(r.batch)}", "Status": r.Status})
    for _, r in debts.iterrows():
        if r.Due and r.Due <= soon:
            rows.append({"Due": r.Due, "Type": "Debt (" + r.Direction + ")", "Field": r.Field, "Detail": f"{r.Who}: {money(r.Balance)} Frw", "Status": "Overdue" if r.Due < today else "Open"})
    out = pd.DataFrame(rows, columns=["Due", "Type", "Field", "Detail", "Status"])
    return out.sort_values("Due").head(10)


def dash_recent(exp, fnd, mv, mr):
    rows = []
    for _, r in exp.head(8).iterrows():
        rows.append({"Date": r.Date, "What": "Expense", "Field": r.Field, "Detail": f"{r.Item} ({r.Category})", "Amount": r.Total, "By": r["Entered by"]})
    for _, r in fnd.head(5).iterrows():
        rows.append({"Date": r.Date, "What": "Funds received", "Field": r.Field, "Detail": str(r.Source) if pd.notna(r.Source) else "", "Amount": r.Amount, "By": r["Entered by"]})
    for _, r in mv.head(8).iterrows():
        rows.append({"Date": r.Date, "What": f"Material {str(r.Type).lower()}", "Field": r.Field, "Detail": f"{r.Material} ({abs(float(r.Qty)):g} {r.Unit})", "Amount": None, "By": r["Entered by"]})
    for _, r in mr.head(5).iterrows():
        rows.append({"Date": r.Date, "What": "Money request", "Field": r.Field, "Detail": f"{r.Purpose} - {r.Status}", "Amount": r.Requested, "By": r["Requested by"]})
    out = pd.DataFrame(rows, columns=["Date", "What", "Field", "Detail", "Amount", "By"])
    return out.sort_values("Date", ascending=False).head(10)


def dash_materials_tab(bal, mv, reqs):
    c1, c2 = st.columns(2)
    with c1:
        stat = pd.DataFrame({"Status": ["In stock", "Out of stock"], "Lines": [int((bal.Balance > 1e-9).sum()), int((bal.Balance <= 1e-9).sum())]}) if len(bal) else pd.DataFrame()
        with card("Stock status", "Material lines with and without balance", stat if len(stat) else None, "stat"):
            if len(stat):
                c_donut(stat, "Status", "Lines", colors=["#0b6a35", "#c9453b"])
            else:
                st.caption("No materials recorded yet.")
    with c2:
        top = pd.DataFrame()
        if len(bal):
            top = bal[bal.Balance > 1e-9].assign(Item=lambda d: d.Material + " (" + d.Unit.fillna("") + ")").sort_values("Balance", ascending=False).head(10)[["Item", "Balance"]]
        with card("Largest stock balances", "Top 10 materials in stock", top if len(top) else None, "topstock"):
            if len(top):
                c_bar(top, "Item", "Balance", unit="Quantity")
            else:
                st.caption("Nothing to show yet.")
    c1, c2 = st.columns(2)
    with c1:
        rcv = pd.DataFrame()
        if len(mv):
            by = mv[mv.Type.isin(["Received", "Used"])].assign(Month=lambda d: d.Date.astype(str).str[:7])
            if len(by):
                rcv = by.groupby(["Month", "Type"]).size().unstack(fill_value=0).reindex(columns=["Received", "Used"], fill_value=0).sort_index().tail(12)
                rcv.index = [month_label(x) for x in rcv.index]
                rcv = rcv.reset_index().rename(columns={"index": "Month"})
        with card("Materials received and used", "Number of movements by month", rcv if len(rcv) else None, "rcv"):
            if len(rcv):
                c_grouped(rcv, "Month", ["Received", "Used"], ytitle="Movements")
            else:
                st.caption("Nothing to show yet.")
    with c2:
        rs = reqs.groupby("Status", as_index=False).size().rename(columns={"size": "Lines"}) if len(reqs) else pd.DataFrame()
        with card("Material requests by status", "All requests sent by the field managers", rs if len(rs) else None, "reqst"):
            if len(rs):
                c_donut(rs, "Status", "Lines")
            else:
                st.caption("No material requests yet.")
    c1, c2 = st.columns(2)
    with c1:
        mr_top = reqs.groupby("Material", as_index=False).Requested.sum().sort_values("Requested", ascending=False).head(10) if len(reqs) else pd.DataFrame()
        with card("Most requested materials", "Total quantity requested · top 10", mr_top if len(mr_top) else None, "mreq"):
            if len(mr_top):
                c_bar(mr_top, "Material", "Requested", unit="Quantity", color="#2f6fb0")
            else:
                st.caption("No material requests yet.")
    with c2:
        bycat = pd.DataFrame()
        if len(bal):
            mats = df("SELECT id, COALESCE(category, 'Uncategorised') AS category FROM materials")
            inst = bal[bal.Balance > 1e-9].assign(Category=lambda d: d.material_id.map(dict(zip(mats.id, mats.category))).fillna("Uncategorised"))
            bycat = inst.groupby("Category", as_index=False).size().rename(columns={"size": "Lines"}).sort_values("Lines", ascending=False)
        with card("Materials in stock by category", "Number of material lines", bycat if len(bycat) else None, "matcat"):
            if len(bycat):
                c_bar(bycat, "Category", "Lines", unit="Lines")
            else:
                st.caption("Nothing to show yet.")


def dashboard_page():
    if not has(CEO, ACCOUNTANT, FIELD):
        return store_dashboard() if has(STORE) else services_part("jobs_dashboard")  # Store manager, or Receptionist / Engineer
    user, field = st.session_state.user, st.session_state.get("ceo_field", "All fields")
    sites = visible_sites()
    if sites.empty:
        return no_field_message()
    can_funds, admin = has(*FUNDS_VISIBLE), has(CEO, ACCOUNTANT)
    ids = [int(i) for i in sites.id]
    exp, fnd, mv, reqs = load_expenses(ids), load_funds(ids), load_moves(ids), load_mat_requests(ids)
    if not can_funds:
        fnd = fnd.iloc[0:0]  # funds are for the CEO only
    mr, led = load_money_requests(), load_ledger()
    if field != "All fields":
        mr, led = mr[mr.Field == field], led[led.Field == field]
    mr, led = mr.reset_index(drop=True), led.reset_index(drop=True)
    debts, taxes = load_debts(field), load_taxes(field)
    if not admin:
        debts, taxes = debts.iloc[0:0], taxes.iloc[0:0]
    bal = stock_balances(mv)
    cats_all = categories_df().name.tolist()

    st.markdown(DASH_CSS, unsafe_allow_html=True)
    st.markdown(f'<div class="dh">{"Financial dashboard" if admin else "Dashboard"}</div><div class="dsub">{html.escape("All fields" if field == "All fields" else field)}'
                f' · all amounts in Frw · updates automatically when anything is recorded</div>', unsafe_allow_html=True)

    # ---- filters (the same four as the Excel dashboard) ----
    if len(exp):
        first, last = exp.Date.min()[:7], max(exp.Date.max()[:7], str(date.today())[:7])
        months, cur = [], date.fromisoformat(first + "-01")
        while str(cur)[:7] <= last:
            months.append(str(cur)[:7])
            cur = date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
    else:
        months = [str(date.today())[:7]]
    labels = [month_label(m) for m in months]
    options = ["All categories"] + cats_all
    saved = st.session_state.get("dash_cat", "All categories")
    saved = saved if saved in options else "All categories"
    f1, f2, f3, f4 = st.columns([2.4, 1.3, 1.3, 1.5])
    cat = f1.selectbox("Category", options, index=options.index(saved))
    st.session_state.dash_cat = cat
    a = f2.selectbox("From month", labels, index=0)
    b = f3.selectbox("To month", labels, index=len(labels) - 1)
    show = f4.selectbox("Show", ["Amount (Frw)", "Transactions"])
    by_count = show == "Transactions"
    i0, i1 = sorted([labels.index(a), labels.index(b)])
    period = months[i0:i1 + 1]
    inp = exp[(exp.Date.str[:7] >= period[0]) & (exp.Date.str[:7] <= period[-1])]
    sel = inp if cat == "All categories" else inp[inp.Category == cat]
    st.markdown(f'<div class="dcap">Showing: <b>{html.escape(cat if cat != "All categories" else "All categories")}</b> | '
                f'{labels[i0]} to {labels[i1]}</div>', unsafe_allow_html=True)

    # ---- numbers ----
    spent_all, funds_all = float(exp.Total.sum()), float(fnd.Amount.sum())
    sel_total, sel_n = float(sel.Total.sum()), len(sel)
    mon_sum, mon_cnt = monthly_vals(sel, "Total", period), monthly_vals(sel, "Total", period, "count")
    mon_lab = [month_label(m, False) for m in period]
    prev_lab = month_label(period[-2], False) if len(period) > 1 else ""
    by_cat_period = inp.groupby("Category").Total.sum().sort_values(ascending=False)
    top_cat = by_cat_period.index[0] if len(by_cat_period) else "-"
    big = sel.loc[sel.Total.idxmax()] if sel_n else None

    st.markdown('<div class="dsec">Selected category and period</div><div class="dcap">Changes with the filters above</div>', unsafe_allow_html=True)
    kpi_grid([
        kpi("Total spend", money(sel_total), "Frw spent in the selection", "green", spark_svg(mon_sum, mon_lab),
            trend(mon_sum[-1], mon_sum[-2], prev_lab) if len(mon_sum) > 1 else None),
        kpi("Transactions", f"{sel_n:,}", "entries recorded", "blue", spark_svg(mon_cnt, mon_lab, "#2f6fb0"),
            trend(mon_cnt[-1], mon_cnt[-2], prev_lab, lambda v: f"{v:,.0f}") if len(mon_cnt) > 1 else None),
        kpi("Average per transaction", money(sel_total / sel_n) if sel_n else "0", "Frw for one entry", "green"),
        kpi("Largest transaction", money(big.Total) if big is not None else "0",
            ("Frw · " + str(big.Item)[:34]) if big is not None else "Frw", "amber"),
        kpi("Share of project spend", f"{sel_total / spent_all * 100:.1f}%" if spent_all else "0%", "of all recorded cost", "grey"),
        kpi("Top category in period", top_cat, f"by amount · {money(by_cat_period.iloc[0])} Frw" if len(by_cat_period) else "by amount", "grey", small=True),
    ])

    st.markdown('<div class="dsec">Whole project</div><div class="dcap">Not affected by the filters</div>', unsafe_allow_html=True)
    balance = funds_all - spent_all
    used_pct = spent_all / funds_all * 100 if funds_all else None
    big_cat = exp.groupby("Category").Total.sum().sort_values(ascending=False)
    if can_funds:
        kpi_grid([
            kpi("Funds received", money(funds_all), "recorded in Funds received", "green"),
            kpi("Recorded expenditure", money(spent_all), f"all {exp.Category.nunique()} categories used", "amber"),
            kpi("Balance / (shortfall)", ("-" if balance < 0 else "") + money(abs(balance)), "funds − expenditure", "red" if balance < 0 else "green"),
            kpi("Funds used", f"{used_pct:.1f}%" if used_pct is not None else "no funds", "expenditure ÷ funds",
                "red" if (used_pct or 0) >= 100 else "amber" if (used_pct or 0) >= 90 else "green"),
            kpi("Biggest cost category", big_cat.index[0] if len(big_cat) else "-", f"all time · {money(big_cat.iloc[0])} Frw" if len(big_cat) else "all time", "grey", small=True),
            kpi("Last transaction", exp.Date.max() if len(exp) else "-", "latest date recorded", "grey"),
        ])
    else:
        budgeted0 = sites[sites.budget.fillna(0) > 0]
        bud0 = float(budgeted0.budget.sum())
        used0 = float(exp[exp.site_id.isin(budgeted0.id)].Total.sum()) / bud0 * 100 if bud0 else None
        kpi_grid([
            kpi("Recorded expenditure", money(spent_all), f"all {exp.Category.nunique()} categories used", "amber"),
            kpi("Budget", money(bud0) if bud0 else "no budget", "Frw set for the field" if bud0 else "the CEO sets it in Fields & categories", "blue"),
            kpi("Budget used", f"{used0:.1f}%" if used0 is not None else "-", "of the budget",
                "red" if (used0 or 0) >= 100 else "amber" if (used0 or 0) >= 90 else "green"),
            kpi("Entries recorded", f"{len(exp):,}", "all time", "green"),
            kpi("Biggest cost category", big_cat.index[0] if len(big_cat) else "-", f"all time · {money(big_cat.iloc[0])} Frw" if len(big_cat) else "all time", "grey", small=True),
            kpi("Last transaction", exp.Date.max() if len(exp) else "-", "latest date recorded", "grey"),
        ])

    pend_mat = int(reqs[reqs.Status == "Pending"].batch.nunique()) if len(reqs) else 0
    pend_money = int((mr.Status == "Pending").sum()) if len(mr) else 0
    out_n = int((bal.Balance <= 1e-9).sum()) if len(bal) else 0
    biz = float(led[led.Kind == "Business"].Amount.sum()) if len(led) else 0.0
    we_owe = float(debts[debts.Direction == "We owe"].Balance.sum()) if len(debts) else 0.0
    owed_us = float(debts[debts.Direction == "Owed to us"].Balance.sum()) if len(debts) else 0.0
    budgeted = sites[sites.budget.fillna(0) > 0]
    budget = float(budgeted.budget.sum())
    spent_budgeted = float(exp[exp.site_id.isin(budgeted.id)].Total.sum())
    bpct = spent_budgeted / budget * 100 if budget else None
    st.markdown('<div class="dsec">Operations</div><div class="dcap">Approvals, stock, money and budget</div>', unsafe_allow_html=True)
    me = user
    mine_mat = int(reqs[(reqs.Status == "Pending") & (reqs["Requested by"] == me)].batch.nunique()) if len(reqs) else 0
    mine_money = int(((mr.Status == "Pending") & (mr["Requested by"] == me)).sum()) if len(mr) else 0
    bcard = kpi("Budget used", f"{bpct:.1f}%" if bpct is not None else "no budget", "of the budget set for the field" if bpct is not None else "no budget set for the field",
                "red" if (bpct or 0) >= 100 else "amber" if (bpct or 0) >= 90 else "green")
    if has(CEO):
        kpi_grid([
            kpi("Waiting for approval", pend_mat + pend_money, f"{pend_mat} material · {pend_money} money requests", "amber" if pend_mat + pend_money else "green"),
            kpi("Materials out of stock", out_n, "material lines with no balance", "red" if out_n else "green"),
            kpi("Money used (business)", money(biz), "Frw recorded in Money used", "blue"),
            kpi("We owe (open)", money(we_owe), "unpaid debts", "amber" if we_owe else "green"),
            kpi("Owed to us (open)", money(owed_us), "money others must pay us", "blue"),
            bcard])
    elif has(ACCOUNTANT):
        to_ok = int(reqs[(reqs.Status == "Pending") & (reqs["Requested by"] != me)].batch.nunique()) if len(reqs) else 0
        to_pay = int(((mr.Status == "Approved") & (mr["Requested by"] != me)).sum()) if len(mr) else 0
        kpi_grid([
            kpi("Material requests to approve", to_ok, "waiting for your approval", "amber" if to_ok else "green"),
            kpi("Money requests to pay", to_pay, "approved by the CEO, not paid yet", "amber" if to_pay else "green"),
            kpi("Materials out of stock", out_n, "material lines with no balance", "red" if out_n else "green"),
            kpi("We owe (open)", money(we_owe), "unpaid debts", "amber" if we_owe else "green"),
            kpi("Owed to us (open)", money(owed_us), "money others must pay us", "blue"),
            bcard])
    else:
        kpi_grid([
            kpi("My material requests", mine_mat, "waiting for approval", "amber" if mine_mat else "green"),
            kpi("My money requests", mine_money, "waiting for the CEO", "amber" if mine_money else "green"),
            kpi("Materials out of stock", out_n, "material lines with no balance", "red" if out_n else "green"),
            kpi("Money used (mine)", money(biz), "Frw recorded in Money used", "blue"),
            bcard])

    # ---- what the data says ----
    says = []
    if len(by_cat_period) and by_cat_period.sum() > 0:
        share = by_cat_period.iloc[0] / by_cat_period.sum() * 100
        says.append((AMBER if share >= 50 else GREEN, f"<b>Biggest cost:</b> {html.escape(str(top_cat))} is {share:.0f}% of the spending in this period ({money(by_cat_period.iloc[0])} Frw)."))
    if len(period) > 1 and mon_sum[-2] > 0:
        pc = (mon_sum[-1] - mon_sum[-2]) / mon_sum[-2] * 100
        says.append((AMBER if pc > 25 else GREEN, f"<b>Spending:</b> {month_label(period[-1])} is {abs(pc):.0f}% {'higher' if pc > 0 else 'lower'} than {month_label(period[-2])} ({money(mon_sum[-1])} against {money(mon_sum[-2])} Frw)."))
    if funds_all:
        says.append((RED, f"<b>Funds:</b> spending is {money(-balance)} Frw more than the funds received.") if balance < 0
                    else (GREEN, f"<b>Funds:</b> {money(balance)} Frw of the funds received is still available ({100 - used_pct:.0f}%)."))
    for lvl, text in dash_alerts(sites, exp, fnd, bal, mr, reqs, debts)[:6]:
        if text.startswith(tuple(s.name for s in sites.itertuples())) and "funds received" in text:
            continue  # already said above
        says.append((RED if lvl == "High" else AMBER if lvl == "Medium" else "#2f6fb0", html.escape(text)))
    st.markdown('<div class="dsec">What the data says</div>', unsafe_allow_html=True)
    insights_html(says[:8])

    # ---- charts ----
    st.markdown('<div class="dsec">Analysis</div><div class="dcap">Hover a chart for the exact value. Click a bar in the category ranking to filter the whole page. '
                'Every chart has a Download data button.</div>', unsafe_allow_html=True)
    t_sp, t_fin, t_mat, t_time, t_money, t_act, t_srv = st.tabs(["Spending", "Financial", "Materials", "Time spent", "Money, taxes & debts" if admin else "Money", "Activity", "Services"])
    metric = "Transactions" if by_count else "Amount"
    unit = "entries" if by_count else "Frw"

    with t_sp:
        rank = inp.groupby("Category", as_index=False).agg(Amount=("Total", "sum"), Transactions=("Total", "size"))
        rank["Value"] = rank["Transactions" if by_count else "Amount"]
        rank = rank.sort_values("Value", ascending=False).reset_index(drop=True)
        rank["Share %"] = (rank.Value / max(rank.Value.sum(), 1e-9) * 100).round(1)
        c1, c2 = st.columns(2)
        with c1:
            with card("Category ranking", f"{labels[i0]} to {labels[i1]} · ranked by {metric.lower()} · click a bar to select it", rank[["Category", "Amount", "Transactions", "Share %"]], "rank"):
                if rank.empty:
                    st.caption("No spending in this period.")
                else:
                    clicked = c_bar(rank.rename(columns={"Value": "Value"})[["Category", "Value"]], "Category", "Value", highlight=cat if cat != "All categories" else None,
                                    unit=unit, key="dash_rank", select=True)
                    last = st.session_state.get("dash_last_click")
                    if clicked != last:
                        st.session_state.dash_last_click = clicked
                        st.session_state.dash_cat = clicked if clicked else "All categories"
                        st.rerun()
        with c2:
            with card("Share of spending", f"Category share of the {metric.lower()} in the period", rank[["Category", "Value", "Share %"]], "share"):
                c_donut(top_other(rank.rename(columns={"Value": "V"}), "Category", "V", 7), "Category", "V",
                        center=short(rank.Value.sum()) if len(rank) else None)
        c1, c2 = st.columns(2)
        with c1:
            ms = pd.DataFrame({"Month": [month_label(m) for m in period], "Value": mon_cnt if by_count else mon_sum})
            with card(f"{metric} by month", f"{cat if cat != 'All categories' else 'All categories'} · {labels[i0]} to {labels[i1]}", ms, "bymonth"):
                c_bar(ms, "Month", "Value", horizontal=False, unit=unit, xtitle="Month")
        with c2:
            cm = pd.concat({"Spent (cumulative)": month_series(exp, "Date", "Total"), "Funds received": month_series(fnd, "Date", "Amount")}, axis=1)
            if len(cm):
                cm = cm.fillna(0).sort_index().cumsum().reset_index()
                cm.columns = ["Month"] + list(cm.columns[1:])
                cm["Month"] = [month_label(m) for m in cm.Month]
            with card("Cumulative spend vs funds received" if can_funds else "Cumulative spending", "Whole project · when the spending line passes the funds line, money is short" if can_funds else "Whole project · month by month", cm if len(cm) else None, "cum"):
                if len(cm):
                    c_lines(cm, "Month", [c for c in ["Spent (cumulative)", "Funds received"] if c in cm.columns])
                else:
                    st.caption("Nothing to show yet.")
        with card("Category ranking table", f"{labels[i0]} to {labels[i1]}", None, "ranktbl"):
            tbl = rank[["Category", "Amount", "Transactions", "Share %"]].copy()
            tbl.insert(0, "Rank", range(1, len(tbl) + 1))
            wide(st.dataframe, pretty(tbl, ["Amount"]), hide_index=True)
            if len(tbl):
                st.download_button("Download data (CSV)", tbl.to_csv(index=False).encode("utf-8"), f"category_ranking_{date.today()}.csv", mime="text/csv", key="csv_ranktbl")

    with t_fin:
        c1, c2 = st.columns(2)
        with c1:
            m = pd.concat({"Spent": month_series(exp, "Date", "Total"), "Funds received": month_series(fnd, "Date", "Amount")}, axis=1)
            m = m.fillna(0).sort_index().tail(12).reset_index() if len(m) else m
            if len(m):
                m.columns = ["Month"] + list(m.columns[1:])
                m["Month"] = [month_label(x) for x in m.Month]
            with card("Spent and funds received by month" if can_funds else "Spending by month", "Last 12 months · whole project", m if len(m) else None, "spf"):
                if len(m):
                    c_grouped(m, "Month", [c for c in ["Spent", "Funds received"] if c in m.columns])
                else:
                    st.caption("Nothing to show yet.")
        with c2:
            stk = pd.DataFrame()
            if len(sel):
                topc = top_other(sel, "Category", "Total", 5).Category.tolist()
                s2 = sel.assign(Month=sel.Date.str[:7], Cat=sel.Category.where(sel.Category.isin(topc), "Other"))
                stk = s2.pivot_table(index="Month", columns="Cat", values="Total", aggfunc="sum", fill_value=0).sort_index().tail(12)
                stk.index = [month_label(x) for x in stk.index]
                stk = stk.reset_index().rename(columns={"index": "Month"})
            with card("Spending by category and month", "Stacked · top 5 categories, the rest together as Other", stk if len(stk) else None, "stk"):
                if len(stk):
                    c_grouped(stk, "Month", [c for c in stk.columns if c != "Month"], stacked=True)
                else:
                    st.caption("No spending in this selection.")
        c1, c2 = st.columns(2)
        with c1:
            items = sel.groupby("Item", as_index=False).Total.sum().sort_values("Total", ascending=False).head(10)
            with card("Biggest items", "Top 10 items by amount in the selection", items, "items"):
                c_bar(items, "Item", "Total", unit="Frw")
        with c2:
            avg = sel.groupby("Category", as_index=False).Total.mean().sort_values("Total", ascending=False).head(10)
            avg["Total"] = avg.Total.round(0)
            with card("Average transaction by category", "Frw for one entry · top 10", avg, "avg"):
                c_bar(avg, "Category", "Total", unit="Frw", color="#2f6fb0")
        if field == "All fields" and len(sites) > 1:
            per = sites[["id", "name", "budget"]].rename(columns={"name": "Field"}).copy()
            per["Funds received"] = per.id.map(fnd.groupby("site_id").Amount.sum()).fillna(0)
            per["Spent"] = per.id.map(exp.groupby("site_id").Total.sum()).fillna(0)
            per = per.sort_values("Spent", ascending=False).head(10)
            cols_ = ["Funds received", "Spent"] if can_funds else ["Spent"]
            with card("Funds received and spent by field" if can_funds else "Spending by field", "Compare the fields side by side", per[["Field"] + cols_], "perfield"):
                c_grouped(per, "Field", cols_)

    with t_mat:
        dash_materials_tab(bal, mv, reqs)

    with t_time:
        c1, c2 = st.columns(2)
        with c1:
            tm = pd.DataFrame({"Month": [month_label(m) for m in period], "Spent": mon_sum})
            with card("Spending over time", f"Month by month · {labels[i0]} to {labels[i1]}", tm, "time"):
                c_lines(tm, "Month", ["Spent"], area=True)
        with c2:
            wd = pd.DataFrame()
            if len(sel):
                order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
                wd = sel.assign(Day=pd.to_datetime(sel.Date, errors="coerce").dt.day_name()).groupby("Day").Total.sum().reindex(order).fillna(0).reset_index()
            with card("Spending by day of the week", "Which days the money goes out", wd if len(wd) else None, "wday"):
                if len(wd):
                    c_bar(wd, "Day", "Total", horizontal=False, unit="Frw")
                else:
                    st.caption("Nothing to show yet.")
        hm = pd.DataFrame()
        if len(sel):
            hm = sel.assign(Month=sel.Date.str[:7]).groupby(["Month", "Category"], as_index=False).Total.sum().sort_values("Month")
            hm = hm[hm.Month.isin(sorted(hm.Month.unique())[-12:])]
            hm["Month"] = [month_label(x) for x in hm.Month]
        with card("Heat map: category by month", "Darker means more money", hm if len(hm) else None, "heat"):
            if len(hm):
                c_heat(hm, "Month", "Category", "Total")
            else:
                st.caption("Nothing to show yet.")
        c1, c2 = st.columns(2)
        with c1:
            cnt = pd.DataFrame({"Month": [month_label(m) for m in period], "Transactions": mon_cnt})
            with card("Number of transactions by month", "How busy each month was", cnt, "cnt"):
                c_bar(cnt, "Month", "Transactions", horizontal=False, unit="entries", color="#2f6fb0")
        with c2:
            lastd = pd.DataFrame()
            if len(exp):
                lastd = exp.groupby("Field").Date.max().reset_index()
                lastd["Days"] = [(date.today() - date.fromisoformat(d)).days for d in lastd.Date]
            with card("Days since the last entry", "By field · a high number means nobody is recording", lastd[["Field", "Days"]] if len(lastd) else None, "since"):
                if len(lastd):
                    c_bar(lastd[["Field", "Days"]], "Field", "Days", unit="Days", color="#8e5ea2")
                else:
                    st.caption("Nothing to show yet.")

    with t_money:
        c1, c2 = st.columns(2)
        bizl = led[led.Kind == "Business"] if len(led) else led
        with c1:
            mu = bizl.groupby("Category", as_index=False).Amount.sum().sort_values("Amount", ascending=False).head(10) if len(bizl) else pd.DataFrame()
            with card("Money used by category", "Business spending · top 10", mu if len(mu) else None, "mu"):
                if len(mu):
                    c_bar(mu, "Category", "Amount", unit="Frw", color="#8e5ea2")
                else:
                    st.caption("Nothing recorded yet.")
        with c2:
            kd = led.groupby("Kind", as_index=False).Amount.sum() if len(led) else pd.DataFrame()
            with card("Business and personal", "Split of the money used", kd if len(kd) else None, "kind"):
                if len(kd):
                    c_donut(kd, "Kind", "Amount", colors=["#0b6a35", "#e0922f"])
                else:
                    st.caption("Nothing recorded yet.")
        c1, c2 = st.columns(2)
        with c1:
            ml = month_series(led, "Date", "Amount")
            mdf = ml.sort_index().tail(12).rename("Money used").reset_index().rename(columns={"M": "Month"}) if len(ml) else pd.DataFrame()
            if len(mdf):
                mdf["Month"] = [month_label(x) for x in mdf.Month]
            with card("Money used by month", "Last 12 months", mdf if len(mdf) else None, "mum"):
                if len(mdf):
                    c_lines(mdf, "Month", ["Money used"], area=True)
                else:
                    st.caption("Nothing recorded yet.")
        with c2:
            ms_ = mr.groupby("Status", as_index=False).size().rename(columns={"size": "Requests"}) if len(mr) else pd.DataFrame()
            with card("Money requests by status", "Requests sent by the field managers", ms_ if len(ms_) else None, "mrs"):
                if len(ms_):
                    c_donut(ms_, "Status", "Requests")
                else:
                    st.caption("No money requests yet.")
        if admin:
            c1, c2 = st.columns(2)
            with c1:
                tx = taxes.groupby("Tax", as_index=False).Amount.sum().sort_values("Amount", ascending=False) if len(taxes) else pd.DataFrame()
                with card("Taxes paid by type", "All recorded tax payments", tx if len(tx) else None, "tax"):
                    if len(tx):
                        c_bar(tx, "Tax", "Amount", unit="Frw", color="#2f6fb0")
                    else:
                        st.caption("No tax payments yet.")
            with c2:
                db = debts.groupby("Direction", as_index=False).Balance.sum() if len(debts) else pd.DataFrame()
                with card("Open debts", "What we owe and what is owed to us", db if len(db) else None, "debt"):
                    if len(db):
                        c_donut(db, "Direction", "Balance", colors=["#c9453b", "#0b6a35"])
                    else:
                        st.caption("No open debts.")

    with t_act:
        c1, c2 = st.columns(2)
        with c1:
            rec = dash_recent(exp, fnd, mv, mr)
            with card("Recent updates", "The latest things recorded", rec if len(rec) else None, "recent"):
                if len(rec):
                    wide(st.dataframe, pretty(rec, ["Amount"]), hide_index=True)
                else:
                    st.caption("Nothing recorded yet.")
        with c2:
            dl = dash_deadlines(mr, reqs, debts)
            with card("Upcoming deadlines", "Next 30 days · requests and debts", dl if len(dl) else None, "dead"):
                if len(dl):
                    wide(st.dataframe, dl, hide_index=True)
                else:
                    st.caption("Nothing due soon.")
    with t_srv:
        if has(CEO, ACCOUNTANT):
            services_part("services_tab")
        else:
            st.caption("Services are shown to the CEO and the Accountant.")


def store_dashboard():
    """Dashboard of the Store manager: stock, deliveries and movements, in the same style as the other dashboards."""
    user, field = st.session_state.user, st.session_state.get("ceo_field", "All fields")
    sites = visible_sites()
    if sites.empty:
        return no_field_message()
    ids = [int(i) for i in sites.id]
    mv, reqs = load_moves(ids), load_mat_requests(ids)
    bal = stock_balances(mv)
    st.markdown(DASH_CSS, unsafe_allow_html=True)
    st.markdown(f'<div class="dh">Dashboard</div><div class="dsub">{html.escape("All fields" if field == "All fields" else field)}'
                f' · materials and stock · updates automatically when anything is recorded</div>', unsafe_allow_html=True)
    months = []
    cur = date.today().replace(day=1)
    for _ in range(6):
        months.insert(0, str(cur)[:7])
        cur = (cur - timedelta(days=1)).replace(day=1)
    labs = [month_label(m, False) for m in months]
    rec_m = monthly_vals(mv[mv.Type == "Received"] if len(mv) else mv, "Qty", months, "count")
    use_m = monthly_vals(mv[mv.Type == "Used"] if len(mv) else mv, "Qty", months, "count")
    since = str(date.today() - timedelta(days=30))
    in_n = int((bal.Balance > 1e-9).sum()) if len(bal) else 0
    out_n = int((bal.Balance <= 1e-9).sum()) if len(bal) else 0
    to_deliver = int((reqs.Status == "Approved").sum()) if len(reqs) else 0
    pending = int(reqs[reqs.Status == "Pending"].batch.nunique()) if len(reqs) else 0
    rec30 = int(((mv.Type == "Received") & (mv.Date >= since)).sum()) if len(mv) else 0
    use30 = int(((mv.Type == "Used") & (mv.Date >= since)).sum()) if len(mv) else 0
    st.markdown('<div class="dsec">Stock today</div><div class="dcap">Material lines in the store</div>', unsafe_allow_html=True)
    kpi_grid([
        kpi("Materials in stock", in_n, "material lines with a balance", "green"),
        kpi("Out of stock", out_n, "material lines with no balance", "red" if out_n else "green"),
        kpi("Received (30 days)", rec30, "receiving entries", "blue", spark_svg(rec_m, labs, "#2f6fb0"),
            trend(rec_m[-1], rec_m[-2], labs[-2], lambda v: f"{v:,.0f}")),
        kpi("Used (30 days)", use30, "usage entries", "amber", spark_svg(use_m, labs, "#e0922f"),
            trend(use_m[-1], use_m[-2], labs[-2], lambda v: f"{v:,.0f}")),
        kpi("Deliveries to record", to_deliver, "approved lines waiting for you", "amber" if to_deliver else "green"),
        kpi("Requests waiting approval", pending, "sent, not yet decided", "grey"),
    ])
    says = []
    empty = lambda f: f.iloc[0:0]
    for lvl, text in dash_alerts(sites, empty(load_expenses(ids)), empty(load_funds(ids)), bal, empty(load_money_requests()), reqs, empty(load_debts(field))):
        says.append((RED if lvl == "High" else AMBER if lvl == "Medium" else "#2f6fb0", html.escape(text)))
    if len(mv):
        gap = (date.today() - date.fromisoformat(str(mv.Date.max())[:10])).days
        if gap >= 7:
            says.append((AMBER, f"No stock movement has been recorded for {gap} days."))
    st.markdown('<div class="dsec">What the data says</div>', unsafe_allow_html=True)
    insights_html(says[:8])
    st.markdown('<div class="dsec">Analysis</div><div class="dcap">Hover a chart for the exact value. Every chart has a Download data button.</div>', unsafe_allow_html=True)
    t_mat, t_act = st.tabs(["Materials", "Activity"])
    with t_mat:
        dash_materials_tab(bal, mv, reqs)
    with t_act:
        mvs = mv.head(30)[["Date", "Field", "Material", "Unit", "Type", "Qty", "Detail", "Entered by"]].reset_index(drop=True) if len(mv) else pd.DataFrame()
        with card("Recent movements", "The latest 30 entries", mvs if len(mvs) else None, "smv"):
            if len(mvs):
                mvs.insert(0, "No", range(1, len(mvs) + 1))
                wide(st.dataframe, pretty(mvs, qty_cols=["Qty"]), hide_index=True)
            else:
                st.caption("Nothing recorded yet.")
