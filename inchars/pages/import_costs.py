"""Import the cost workbook (Excel) page."""

from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st

from inchars.accounts import has
from inchars.components.common import K, done, money, norm, pretty, wide
from inchars.config import ACCOUNTANT, ALIASES, CEO, FUNDS_VISIBLE, SHEET_TO_CATEGORY
from inchars.data.scope import no_field_message, visible_sites
from inchars.db import IntegrityErrors, bulk_insert, conn, df
from inchars.pages.setup import add_site


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
