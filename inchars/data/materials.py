"""Materials, stock movements and material requests: read and write."""

from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st

from inchars.accounts import has
from inchars.config import CEO, MOVE_COLS, REQ_COLS
from inchars.data.scope import visible_sites
from inchars.db import Abort, bulk_insert, conn, df, ensure_named


def load_materials():
    return df("SELECT id, name, unit FROM materials ORDER BY name")


def load_moves(site_ids):
    if not site_ids:
        return pd.DataFrame(columns=MOVE_COLS)
    return df(f"""SELECT m.id, m.site_id, m.material_id, m.moved_on AS "Date", s.name AS "Field", t.name AS "Material",
                  t.unit AS "Unit", m.kind AS "Type", m.qty AS "Qty", m.detail AS "Detail", m.entered_by AS "Entered by"
                  FROM material_moves m JOIN sites s ON s.id=m.site_id JOIN materials t ON t.id=m.material_id
                  WHERE m.site_id IN ({','.join('?' * len(site_ids))}) ORDER BY m.moved_on DESC, m.id DESC""", tuple(site_ids))


def stock_balances(mv):
    """One row per field and material: received, used, count corrections and the balance."""
    cols = ["site_id", "material_id", "Field", "Material", "Unit", "Received", "Used", "Corrected", "Balance"]
    if mv.empty:
        return pd.DataFrame(columns=cols)
    q = pd.to_numeric(mv.Qty)
    g = mv.assign(Qty=q, R=q.where(mv.Type == "Received", 0.0), U=(-q).where(mv.Type == "Used", 0.0),
                  C=q.where(mv.Type == "Count", 0.0))
    out = g.groupby(["site_id", "material_id", "Field", "Material", "Unit"], as_index=False).agg(
        Received=("R", "sum"), Used=("U", "sum"), Corrected=("C", "sum"), Balance=("Qty", "sum"))
    out["Status"] = ["In stock" if b > 1e-9 else "Out of stock" for b in out.Balance]
    return out.sort_values(["Field", "Material"]).reset_index(drop=True)


def parse_moves(ed, mat_ids, allow_new=False):
    ok, bad = [], []
    for pos, (_, r) in enumerate(ed.iterrows(), start=1):
        mat = r["Material"] if pd.notna(r["Material"]) else None
        new = str(r.get("New material", "") or "").strip() if pd.notna(r.get("New material", "")) else ""
        unit = str(r.get("Unit", "") or "").strip() if pd.notna(r.get("Unit", "")) else ""
        qty = pd.to_numeric(r["Qty"], errors="coerce")
        detail = str(r["Detail"]).strip() if pd.notna(r["Detail"]) else ""
        if not mat and not new and (pd.isna(qty) or qty == 0) and not detail:
            continue  # empty row
        d = pd.to_datetime(r["Date"], errors="coerce")
        d = date.today() if pd.isna(d) else d.date()
        if new and not allow_new:
            bad.append(f"Row {pos}: choose the material from the list.")
        elif new and not unit:
            bad.append(f"Row {pos}: write the unit of the new material (bags, pcs, kg, m3…).")
        elif not new and mat not in mat_ids:
            bad.append(f"Row {pos}: choose a material, or type a new one.")
        elif pd.isna(qty) or qty <= 0:
            bad.append(f"Row {pos}: the quantity must be above 0.")
        elif d > date.today():
            bad.append(f"Row {pos}: the date is in the future.")
        else:
            ok.append({"date": d, "material": mat, "new": (new, unit) if new else None, "qty": float(qty), "detail": detail})
    return ok, bad


def add_moves(site_id, mat_ids, kind, entries, user):
    """Received adds to the stock and Used takes away. New materials are created on the way.
    Returns an error text, or None when saved."""
    sign, now = (1 if kind == "Received" else -1), datetime.now().isoformat(timespec="seconds")
    try:
        with conn() as c:
            ids = [ensure_named(c, "materials", e["new"][0], e["new"][1]) if e["new"] else mat_ids[e["material"]] for e in entries]
            if kind == "Used":  # never use more than is in stock
                need = {}
                for mid, e in zip(ids, entries):
                    need[mid] = need.get(mid, 0) + e["qty"]
                for mid, q in need.items():
                    have = float(c.execute("SELECT COALESCE(SUM(qty), 0) AS b FROM material_moves WHERE site_id=? AND material_id=?",
                                           (site_id, mid)).fetchone()["b"])
                    if q > have + 1e-9:
                        name = c.execute("SELECT name FROM materials WHERE id=?", (mid,)).fetchone()["name"]
                        raise Abort(f"{name}: only {have:g} in stock.")
            bulk_insert(c, "material_moves", ["site_id", "material_id", "kind", "qty", "moved_on", "detail", "entered_by", "entered_at"],
                        [(site_id, mid, kind, sign * e["qty"], str(e["date"]), e["detail"], user, now) for mid, e in zip(ids, entries)])
    except Abort as e:
        return str(e)
    return None


def count_stock(site_id, counted, note, user):
    """counted = {material_id: counted quantity}. Saves the difference as a 'Count' correction."""
    now, changed = datetime.now().isoformat(timespec="seconds"), 0
    with conn() as c:
        for mid, qty in counted.items():
            have = float(c.execute("SELECT COALESCE(SUM(qty), 0) AS b FROM material_moves WHERE site_id=? AND material_id=?",
                                   (site_id, mid)).fetchone()["b"])
            if abs(qty - have) > 1e-9:
                c.execute("INSERT INTO material_moves(site_id, material_id, kind, qty, moved_on, detail, entered_by, entered_at) "
                          "VALUES(?,?,?,?,?,?,?,?)", (site_id, mid, "Count", qty - have, str(date.today()),
                                                      f"Stock count: {note}".strip(), user, now))
                changed += 1
    return changed


def load_mat_requests(site_ids):
    if not site_ids:
        return pd.DataFrame(columns=REQ_COLS)
    return df(f"""SELECT r.id, r.batch, r.site_id, r.material_id, r.requested_on AS "Date", s.name AS "Field",
                  t.name AS "Material", t.unit AS "Unit", r.qty_requested AS "Requested", r.qty_approved AS "Approved",
                  r.qty_delivered AS "Delivered", r.status AS "Status", r.needed_by AS "Needed by",
                  r.requested_by AS "Requested by", r.decided_by AS "Decided by", r.note AS "Note", r.reason AS "Reason"
                  FROM material_requests r JOIN sites s ON s.id=r.site_id JOIN materials t ON t.id=r.material_id
                  WHERE r.site_id IN ({','.join('?' * len(site_ids))}) ORDER BY r.batch DESC, t.name""", tuple(site_ids))


def parse_req(ed, labels):
    ok, bad = [], []
    for pos, (_, r) in enumerate(ed.iterrows(), start=1):
        mat = r["Material"] if pd.notna(r["Material"]) else None
        new = str(r["New material"]).strip() if pd.notna(r["New material"]) else ""
        unit = str(r["Unit"]).strip() if pd.notna(r["Unit"]) else ""
        qty = pd.to_numeric(r["Qty"], errors="coerce")
        note = str(r["Note"]).strip() if pd.notna(r["Note"]) else ""
        if not mat and not new and (pd.isna(qty) or qty == 0) and not note:
            continue  # empty row
        if new and not unit:
            bad.append(f"Row {pos}: write the unit of the new material.")
        elif not new and mat not in labels:
            bad.append(f"Row {pos}: choose a material, or type a new one.")
        elif pd.isna(qty) or qty <= 0:
            bad.append(f"Row {pos}: the quantity must be above 0.")
        else:
            ok.append({"material": mat, "new": (new, unit) if new else None, "qty": float(qty), "note": note})
    return ok, bad


def create_mat_request(site_id, labels, items, needed_by, user):
    """All lines go into one request. Its number is the id of its first line."""
    first, today, b = True, str(date.today()), 0
    with conn() as c:
        for e in items:
            mid = ensure_named(c, "materials", e["new"][0], e["new"][1]) if e["new"] else labels[e["material"]]
            vals = (site_id, mid, e["qty"], str(needed_by), today, user, e["note"])
            if first:
                b = c.execute("INSERT INTO material_requests(batch, site_id, material_id, qty_requested, needed_by, requested_on, "
                              "requested_by, note) VALUES(0,?,?,?,?,?,?,?) RETURNING id", vals).fetchone()["id"]
                c.execute("UPDATE material_requests SET batch=? WHERE id=?", (b, b))
                first = False
            else:
                c.execute("INSERT INTO material_requests(batch, site_id, material_id, qty_requested, needed_by, requested_on, "
                          "requested_by, note) VALUES(?,?,?,?,?,?,?,?)", (b,) + vals)
    return b


def update_mat_pending(user, changes, removes):
    """The person who asked can still change or delete their own lines while they are pending."""
    skipped = 0
    with conn() as c:
        for rid in removes:
            skipped += c.execute("DELETE FROM material_requests WHERE id=? AND status='Pending' AND requested_by=?",
                                 (rid, user)).rowcount == 0
        for rid, q in changes.items():
            skipped += c.execute("UPDATE material_requests SET qty_requested=? WHERE id=? AND status='Pending' AND requested_by=?",
                                 (q, rid, user)).rowcount == 0
    return skipped


def decide_mat_requests(decisions, user, is_ceo):
    """Accountant or CEO. decisions = {request id: (quantity to approve, reason)}. Quantity 0 refuses the line.
    Nobody except the CEO can decide their own request."""
    today = str(date.today())
    try:
        with conn() as c:
            for rid, (q, why) in decisions.items():
                row = c.execute("SELECT requested_by FROM material_requests WHERE id=?", (rid,)).fetchone()
                if row is not None and row["requested_by"] == user and not is_ceo:
                    raise Abort("You cannot decide your own request. The CEO does that.")
                if q > 0:
                    n = c.execute("UPDATE material_requests SET status='Approved', qty_approved=?, decided_on=?, decided_by=? "
                                  "WHERE id=? AND status='Pending'", (q, today, user, rid)).rowcount
                else:
                    n = c.execute("UPDATE material_requests SET status='Rejected', qty_approved=0, decided_on=?, decided_by=?, "
                                  "reason=? WHERE id=? AND status='Pending'", (today, user, why or "Not approved", rid)).rowcount
                if n != 1:
                    raise Abort("Some of these requests were already decided by someone else. Please refresh the page.")
    except Abort as e:
        return str(e)
    return None


def deliver_mat_lines(deliveries, user, uid):
    """Store manager. deliveries = {request id: quantity received}. Adds the quantity to the stock of the field."""
    now, n = datetime.now().isoformat(timespec="seconds"), 0
    with conn() as c:
        for rid, qty in deliveries.items():
            row = c.execute("""SELECT site_id, material_id, batch FROM material_requests WHERE id=? AND status='Approved'
                               AND site_id IN (SELECT site_id FROM user_sites WHERE user_id=?)""", (rid, uid)).fetchone()
            if row is None or qty <= 0:
                continue
            c.execute("INSERT INTO material_moves(site_id, material_id, kind, qty, moved_on, detail, entered_by, entered_at) "
                      "VALUES(?,?,?,?,?,?,?,?)", (row["site_id"], row["material_id"], "Received", qty, str(date.today()),
                                                  f"Request #{row['batch']}", user, now))
            c.execute("UPDATE material_requests SET status='Delivered', qty_delivered=?, delivered_on=? WHERE id=?",
                      (qty, str(date.today()), rid))
            n += 1
    return n


def pending_material_requests():
    """How many requests wait for the signed-in Accountant or CEO."""
    sites = visible_sites()
    if sites.empty:
        return 0
    r = load_mat_requests([int(i) for i in sites.id])
    p = r[r.Status == "Pending"]
    if not has(CEO):
        p = p[p["Requested by"] != st.session_state.user]
    return int(p.batch.nunique())
