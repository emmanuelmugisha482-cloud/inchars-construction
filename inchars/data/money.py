"""Money requests, money used, debts and taxes: read and write."""

from datetime import date, datetime, timedelta

import streamlit as st

from inchars.accounts import has
from inchars.config import ACCOUNTANT, CEO
from inchars.data.scope import categories_df
from inchars.db import Abort, conn, df, ensure_named


def money_cats():
    """Money requests and money used use the SAME categories as the expenses / Excel workbook."""
    return categories_df().name.tolist()


def field_choices(sites):
    return {"(no field)": None, **{r.name: int(r.id) for r in sites.itertuples()}}


def load_money_requests():
    """Everybody sees their own requests. The Accountant also sees those of their fields and company-level ones."""
    user, uid = st.session_state.user, st.session_state.uid
    where, params = "r.requested_by=?", [user]
    if has(CEO):
        where, params = "1=1", []
    elif has(ACCOUNTANT):
        where, params = "(r.requested_by=? OR r.site_id IS NULL OR r.site_id IN (SELECT site_id FROM user_sites WHERE user_id=?))", [user, uid]
    return df(f"""SELECT r.id, r.site_id, r.requested_on AS "Date", COALESCE(s.name, '(no field)') AS "Field",
                  r.category AS "Category", r.purpose AS "Purpose", r.amount AS "Requested", r.amount_approved AS "Approved",
                  r.status AS "Status", r.needed_by AS "Needed by", r.requested_by AS "Requested by",
                  r.decided_by AS "Decided by", r.reason AS "Reason", r.paid_on AS "Paid on"
                  FROM money_requests r LEFT JOIN sites s ON s.id=r.site_id WHERE {where} ORDER BY r.id DESC""", tuple(params))


def create_money_request(site_id, category, new_category, purpose, amount, needed_by, user):
    with conn() as c:
        cat = new_category.strip() if new_category.strip() else category
        if new_category.strip():
            ensure_named(c, "categories", cat)
        c.execute("INSERT INTO money_requests(site_id, category, purpose, amount, needed_by, requested_on, requested_by) "
                  "VALUES(?,?,?,?,?,?,?)", (site_id, cat, purpose.strip(), int(amount), str(needed_by), str(date.today()), user))


def update_money_pending(user, changes, removes):
    skipped = 0
    with conn() as c:
        for rid in removes:
            skipped += c.execute("DELETE FROM money_requests WHERE id=? AND status='Pending' AND requested_by=?", (rid, user)).rowcount == 0
        for rid, a in changes.items():
            skipped += c.execute("UPDATE money_requests SET amount=? WHERE id=? AND status='Pending' AND requested_by=?",
                                 (int(a), rid, user)).rowcount == 0
    return skipped


def decide_money_requests(decisions, user):
    """CEO. decisions = {request id: (amount to approve, reason)}. Amount 0 refuses the request."""
    today = str(date.today())
    try:
        with conn() as c:
            for rid, (a, why) in decisions.items():
                if a > 0:
                    n = c.execute("UPDATE money_requests SET status='Approved', amount_approved=?, decided_on=?, decided_by=? "
                                  "WHERE id=? AND status='Pending'", (int(a), today, user, rid)).rowcount
                else:
                    n = c.execute("UPDATE money_requests SET status='Rejected', amount_approved=0, decided_on=?, decided_by=?, reason=? "
                                  "WHERE id=? AND status='Pending'", (today, user, why or "Not approved", rid)).rowcount
                if n != 1:
                    raise Abort("Some requests were already decided. Please refresh the page.")
    except Abort as e:
        return str(e)
    return None


def pay_money_requests(ids, user):
    """CEO or Accountant records the payment. The money is added to the requester's 'money used'."""
    now, n = datetime.now().isoformat(timespec="seconds"), 0
    with conn() as c:
        for rid in ids:
            r = c.execute("SELECT * FROM money_requests WHERE id=? AND status='Approved'", (rid,)).fetchone()
            if r is None or r["requested_by"] == user:
                continue  # nobody records the payment of their own request
            c.execute("UPDATE money_requests SET status='Paid', paid_on=?, paid_by=? WHERE id=?", (str(date.today()), user, rid))
            c.execute("INSERT INTO money_ledger(spent_on, person, site_id, kind, category, amount, note, request_id, entered_by, entered_at) "
                      "VALUES(?,?,?,?,?,?,?,?,?,?)", (str(date.today()), r["requested_by"], r["site_id"], "Business", r["category"],
                                                      r["amount_approved"], r["purpose"], rid, user, now))
            n += 1
    return n


def pending_money_requests():
    r = load_money_requests()
    return int((r.Status == "Pending").sum()) if has(CEO) else int((r.Status == "Approved").sum()) if has(ACCOUNTANT) else 0


def load_ledger():
    user = st.session_state.user
    where, params = ("(l.kind='Business' OR l.person=?)", [user]) if has(CEO, ACCOUNTANT) else ("l.person=?", [user])
    return df(f"""SELECT l.id, l.entered_by, l.request_id, l.spent_on AS "Date", l.person AS "Person",
                  COALESCE(s.name, '(no field)') AS "Field", l.kind AS "Kind", l.category AS "Category", l.amount AS "Amount",
                  l.note AS "Note" FROM money_ledger l LEFT JOIN sites s ON s.id=l.site_id WHERE {where}
                  ORDER BY l.spent_on DESC, l.id DESC""", tuple(params))


def load_debts(field):
    d = df("""SELECT d.party AS "Who", d.direction AS "Direction", d.amount AS "Amount",
              COALESCE((SELECT SUM(p.amount) FROM debt_payments p WHERE p.debt_id=d.id), 0) AS "Paid",
              d.due_on AS "Due", COALESCE(s.name, '(no field)') AS "Field" FROM debts d LEFT JOIN sites s ON s.id=d.site_id""")
    if field != "All fields":
        d = d[d.Field == field]
    d = d.assign(Balance=d.Amount - d.Paid)
    return d[d.Balance > 0].reset_index(drop=True)


def load_taxes(field):
    t = df("""SELECT t.paid_on AS "Date", t.tax_type AS "Tax", t.amount AS "Amount", COALESCE(s.name, '(no field)') AS "Field"
              FROM taxes t LEFT JOIN sites s ON s.id=t.site_id""")
    return t if field == "All fields" else t[t.Field == field]
