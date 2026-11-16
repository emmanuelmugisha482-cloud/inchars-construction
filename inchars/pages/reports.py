"""Reports page: summary and category sheets."""

from datetime import date, datetime, timedelta

import streamlit as st

from inchars.accounts import has
from inchars.components.charts import c_donut
from inchars.components.common import money, norm, pretty, to_excel, top_other, wide
from inchars.components.sheets import cost_frame, download_sheet, download_workbook, sheet_view
from inchars.config import FUNDS_VISIBLE, XLSX
from inchars.data.expenses import load_expenses, load_funds
from inchars.data.scope import no_field_message, visible_sites


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
