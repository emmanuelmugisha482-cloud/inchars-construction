"""Expenses and funds received: read and write."""

from datetime import date, datetime, timedelta

import pandas as pd

from inchars.config import EXP_COLS, FUND_COLS
from inchars.db import bulk_insert, conn, df, ensure_named


def load_expenses(site_ids):
    if not site_ids:
        return pd.DataFrame(columns=EXP_COLS)
    return df(f"""SELECT e.id, e.site_id, e.spent_on AS "Date", s.name AS "Field", c.name AS "Category",
                  e.item AS "Item", e.qty AS "Qty", e.unit_price AS "Unit price", e.total AS "Total",
                  e.entered_by AS "Entered by" FROM expenses e JOIN sites s ON s.id=e.site_id
                  JOIN categories c ON c.id=e.category_id WHERE e.site_id IN ({','.join('?' * len(site_ids))})
                  ORDER BY e.spent_on DESC, e.id DESC""", tuple(site_ids))


def load_funds(site_ids):
    if not site_ids:
        return pd.DataFrame(columns=FUND_COLS)
    return df(f"""SELECT f.id, f.site_id, f.received_on AS "Date", s.name AS "Field", f.amount AS "Amount",
                  f.source AS "Source", f.note AS "Note", f.entered_by AS "Entered by" FROM funds f
                  JOIN sites s ON s.id=f.site_id WHERE f.site_id IN ({','.join('?' * len(site_ids))})
                  ORDER BY f.received_on DESC, f.id DESC""", tuple(site_ids))


def add_expenses(site_id, cat_ids, entries, user):
    now = datetime.now().isoformat(timespec="seconds")
    with conn() as c:
        rows = []
        for e in entries:
            cid = ensure_named(c, "categories", e["new_category"]) if e["new_category"] else cat_ids[e["category"]]
            rows.append((site_id, cid, e["item"], e["qty"], e["price"], e["total"], str(e["date"]), user, now, "entry"))
        bulk_insert(c, "expenses", ["site_id", "category_id", "item", "qty", "unit_price", "total", "spent_on",
                                    "entered_by", "entered_at", "source"], rows)


def delete_expenses(ids, user, uid, is_accountant):
    """An Accountant can delete any entry of their fields. Others only their own entries of today."""
    n = 0
    with conn() as c:
        for rid in ids:
            if is_accountant:
                n += c.execute("DELETE FROM expenses WHERE id=? AND site_id IN (SELECT site_id FROM user_sites WHERE user_id=?)",
                               (rid, uid)).rowcount
            else:
                n += c.execute("""DELETE FROM expenses WHERE id=? AND entered_by=? AND SUBSTR(entered_at,1,10)=?
                                  AND site_id IN (SELECT site_id FROM user_sites WHERE user_id=?)""",
                               (rid, user, str(date.today()), uid)).rowcount
    return n
