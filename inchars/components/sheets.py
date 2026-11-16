"""Excel-style tables: No, Date, Item, Qty, P/U, Total with a Grand total line, download bars and the add-rows form."""

from datetime import date, datetime, timedelta
import re

import pandas as pd
import streamlit as st

from inchars.components.common import K, done, money, norm, pretty, to_excel, wide
from inchars.config import XLSX
from inchars.data.expenses import add_expenses
from inchars.data.scope import categories_df


CORE = ["No", "Date", "Item", "Qty", "P/U", "Total"]


NEW_CAT = "+ New category…"


def sheet_number(frame, cols):
    out = frame.reset_index(drop=True).copy()
    out.insert(0, "No", range(1, len(out) + 1))
    return out[cols]


def _xl_dates(tbl):
    """Real Excel dates (not text), so the file looks like the workbook and can be imported again."""
    tbl = tbl.copy()
    if "Date" in tbl.columns:
        tbl["Date"] = pd.Series([pd.to_datetime(v, errors="coerce") for v in tbl["Date"]], index=tbl.index, dtype=object).map(
            lambda v: None if pd.isna(v) else v.date())
    return tbl


def sheet_with_total(frame):
    """One category sheet exactly like the workbook: No, Date, Item, Qty, P/U, Total, then the Grand total line."""
    t = _xl_dates(sheet_number(frame, CORE))
    line = {"No": None, "Date": None, "Item": "Grand total", "Qty": None, "P/U": None, "Total": float(pd.to_numeric(t.Total).sum())}
    return pd.concat([t, pd.DataFrame([line])], ignore_index=True)


def _sheet_name(name, used):
    base = re.sub(r"[\[\]:*?/\\]", "", str(name)).strip()[:28] or "Sheet"
    out, n = base, 2
    while out.lower() in used:
        out, n = f"{base}_{n}", n + 1
    used.add(out.lower())
    return out


def cost_workbook(frame, category_col="Category"):
    """Excel file with a SUMMARY sheet and one sheet per category (the same layout as the cost workbook)."""
    used = {"summary"}
    sheets = {}
    summ = []
    for cat, part in frame.groupby(category_col):
        sheets[_sheet_name(cat, used)] = sheet_with_total(part)
        summ.append({"Category": cat, "Rows": len(part), "Total": float(pd.to_numeric(part.Total).sum())})
    summary = pd.DataFrame(summ, columns=["Category", "Rows", "Total"])
    if len(summary):
        summary = pd.concat([summary, pd.DataFrame([{"Category": "Grand total", "Rows": int(summary.Rows.sum()),
                                                      "Total": float(summary.Total.sum())}])], ignore_index=True)
    return to_excel({"SUMMARY": summary, **sheets})


def sheet_view(frame, key, extras=(), removable=False):
    """Excel-like table with No, Date, Item, Qty, P/U, Total (and the extra columns), the Grand total, and a
    download bar. Returns the ids ticked for removal when removable=True (the frame needs an id column)."""
    cols = CORE + [c for c in extras if c in frame.columns]
    tbl = sheet_number(frame, cols)
    total = float(pd.to_numeric(tbl.Total).sum()) if len(tbl) else 0.0
    st.markdown(f"**{len(tbl)} rows · Grand total {money(total)} Frw**")
    ticked = []
    if removable and len(tbl):
        t2 = tbl.copy()
        t2["Remove"] = False
        ed = wide(st.data_editor, t2, hide_index=True, key=f"{K(key)}_{len(t2)}_{int(total)}", disabled=cols)
        ticked = [int(i) for i, r in zip(frame.reset_index(drop=True).id, ed["Remove"]) if r]
    else:
        wide(st.dataframe, pretty(tbl, ["P/U", "Total"], qty_cols=["Qty"]), hide_index=True)
    return ticked


def download_sheet(frame, extras, name, key, label="Download Excel"):
    """Download bar for one table. Keeps the Excel layout, so he can keep working in Excel if he prefers."""
    cols = CORE + [c for c in extras if c in frame.columns]
    tbl = _xl_dates(sheet_number(frame, cols))
    if len(tbl):
        line = {c: None for c in cols}
        line.update({"Item": "Grand total", "Total": float(pd.to_numeric(tbl.Total).sum())})
        tbl = pd.concat([tbl, pd.DataFrame([line])], ignore_index=True)
    sheet_name = _sheet_name(name, set())
    st.download_button(f":material/download: {label}", to_excel({sheet_name: tbl}), f"{norm(name) or 'sheet'}_{date.today()}.xlsx",
                       mime=XLSX, key=f"dl_{key}")


def download_workbook(frame, name, key, label="Download Excel (one sheet per category)"):
    if frame.empty:
        return
    st.download_button(f":material/download: {label}", cost_workbook(frame), f"{norm(name) or 'sheets'}_{date.today()}.xlsx",
                       mime=XLSX, key=f"dl_{key}")


def category_pick(key):
    """Category list (the same for everybody, the workbook categories) with the option to add a new one."""
    cats = categories_df().name.tolist()
    choice = st.selectbox("Category", cats + [NEW_CAT], key=key)
    if choice == NEW_CAT:
        return st.text_input("New category name", key=key + "n").strip(), True
    return choice, False


def sheet_editor(key, rows=5):
    """Rows written like in Excel: Date, Item, Qty, P/U and the Total of every row, live, with the Grand total below."""
    n_key = K(key + "_n")
    n = st.session_state.get(n_key, rows)
    widths = [0.5, 1.6, 3.2, 1.0, 1.6, 1.7]
    head = st.columns(widths)
    for col, t in zip(head, ["No", "Date", "Item", "Qty", "P/U (Frw)", "Total"]):
        col.markdown(f"**{t}**")
    recs, grand = [], 0.0
    for i in range(n):
        c = st.columns(widths)
        c[0].markdown(f"{i + 1}")
        d = c[1].date_input("Date", date.today(), max_value=date.today(), key=f"{K(key)}_d{i}", label_visibility="collapsed")
        item = c[2].text_input("Item", key=f"{K(key)}_t{i}", placeholder="Item", label_visibility="collapsed")
        q = c[3].number_input("Qty", min_value=0.0, value=1.0, step=1.0, key=f"{K(key)}_q{i}", label_visibility="collapsed")
        p = c[4].number_input("P/U", min_value=0.0, value=None, step=100.0, key=f"{K(key)}_p{i}", placeholder="0", label_visibility="collapsed")
        line = float(q or 0) * float(p or 0)
        grand += line
        c[5].markdown(f"<div style='padding-top:6px;font-weight:700;text-align:right'>{money(line)}</div>", unsafe_allow_html=True)
        recs.append({"Date": d, "Item": item, "Qty": q, "P/U": p})
    foot = st.columns(widths)
    if foot[1].button("+ Add 5 rows", key=f"{K(key)}_more"):
        st.session_state[n_key] = n + 5
        st.rerun()
    foot[2].markdown("<div style='text-align:right;padding-top:6px'><b>Total</b></div>", unsafe_allow_html=True)
    foot[5].markdown(f"<div style='padding-top:6px;font-weight:800;text-align:right;border-top:2px solid #0b6a35'>{money(grand)}</div>", unsafe_allow_html=True)
    return pd.DataFrame(recs, columns=["Date", "Item", "Qty", "P/U"])


def parse_sheet(ed):
    ok, bad = [], []
    for pos, (_, r) in enumerate(ed.iterrows(), start=1):
        item = str(r["Item"]).strip() if pd.notna(r["Item"]) else ""
        qty, pu = pd.to_numeric(r["Qty"], errors="coerce"), pd.to_numeric(r["P/U"], errors="coerce")
        if not item and (pd.isna(pu) or pu == 0):
            continue  # empty row
        d = pd.to_datetime(r["Date"], errors="coerce")
        d = date.today() if pd.isna(d) else d.date()
        if not item:
            bad.append(f"Row {pos}: write the item name.")
        elif pd.isna(qty) or qty <= 0:
            bad.append(f"Row {pos}: the quantity must be above 0.")
        elif pd.isna(pu) or pu <= 0:
            bad.append(f"Row {pos}: enter the P/U.")
        elif d > date.today():
            bad.append(f"Row {pos}: the date is in the future.")
        else:
            ok.append({"date": d, "item": item, "qty": float(qty), "price": float(pu), "total": int(round(float(qty) * float(pu)))})
    return ok, bad


def sheet_preview(ok):
    if ok:
        prev = pd.DataFrame({"Date": [str(e["date"]) for e in ok], "Item": [e["item"] for e in ok], "Qty": [e["qty"] for e in ok],
                             "P/U": [e["price"] for e in ok], "Total": [e["total"] for e in ok]})
        st.markdown(f"**{len(ok)} row(s) ready · Grand total {money(prev.Total.sum())} Frw**")
        wide(st.dataframe, pretty(sheet_number(prev, CORE), ["P/U", "Total"], qty_cols=["Qty"]), hide_index=True)


def field_target(sites, key):
    """The field a new row belongs to: the chosen field, or a list when All fields is chosen."""
    if len(sites) == 1:
        st.markdown(f"**Field:** {sites.name.iloc[0]}")
        return int(sites.id.iloc[0]), sites.name.iloc[0]
    name = st.selectbox("Add to field", sites.name.tolist(), key=key)
    return int(sites[sites.name == name].id.iloc[0]), name


def add_cost_rows_form(sites, key):
    """Add rows to a category sheet (they are saved as expenses of the field)."""
    user = st.session_state.user
    a, b = st.columns(2)
    with a:
        cat, is_new = category_pick(K(key + "c"))
    with b:
        site_id, fname = field_target(sites, K(key + "f"))
    ed = sheet_editor(key + "g")
    ok, bad = parse_sheet(ed)
    sheet_preview(ok)
    for msg in bad:
        st.warning(msg)
    if st.button("Save rows", type="primary", key=f"{key}_save"):
        if not cat:
            st.warning("Write the name of the new category.")
        elif bad:
            st.error("Please correct the lines marked above.")
        elif not ok:
            st.info("Nothing to save yet.")
        else:
            cats = categories_df()
            cat_ids = dict(zip(cats.name, cats.id.astype(int)))
            entries = [{"date": e["date"], "category": None if is_new else cat, "new_category": cat if is_new else "",
                        "item": e["item"], "qty": e["qty"], "price": e["price"], "total": e["total"]} for e in ok]
            add_expenses(site_id, cat_ids, entries, user)
            done(f"{len(ok)} row(s) saved in {cat} for {fname}.")


def cost_frame(exp):
    """Expenses in the workbook layout."""
    return exp.rename(columns={"Unit price": "P/U"})


def sheet_show(frame, extras, name, key):
    """A read-only table in the workbook layout (No, Date, Item, Qty, P/U, Total + extras) with its download bar."""
    cols = CORE + [c for c in extras if c in frame.columns]
    t = sheet_number(frame, cols)
    st.markdown(f"**{len(t)} rows · Grand total {money(pd.to_numeric(t.Total).sum())} Frw**")
    wide(st.dataframe, pretty(t.head(300), ["P/U", "Total"], "Status" if "Status" in t.columns else None, qty_cols=["Qty"]), hide_index=True)
    if len(t):
        download_sheet(frame, extras, name, key)
