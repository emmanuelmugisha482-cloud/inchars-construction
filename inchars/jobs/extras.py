"""The Jobs extras: documents, payments, and the dashboard of the services. Separate from page.py so one can be repaired while the other works."""

import html
import pandas as pd
import streamlit as st
from datetime import date, timedelta

from inchars.accounts import has
from inchars.database import cached, conn, df
from inchars.jobs.data import (
    MAX_FILE_MB,
    OPEN_STATUSES,
    PAY_METHODS,
    after_commit,
    can,
    can_open_deliverable,
    fetch_job,
    load_jobs,
    log_event,
    notify,
    now,
    sees_all_jobs,
    sees_money,
    unread_count,
    usernames_with_role,
)
from inchars.settings import CEO, DASH_CSS, ENGINEER, RECEPTIONIST, XLSX
from inchars.widgets import (
    AMBER,
    CORE,
    GREEN,
    K,
    RED,
    c_bar,
    c_donut,
    c_grouped,
    card,
    done,
    download_sheet,
    insights_html,
    kpi,
    kpi_grid,
    match,
    money,
    month_label,
    monthly_vals,
    pick,
    pretty,
    search_box,
    sheet_number,
    spark_svg,
    to_excel,
    trend,
    wide,
)


# ======================================================================
# from services/files.py
# ======================================================================

PDF_ONLY = ("pdf",)
DOCUMENT_TYPES = ("pdf", "jpg", "jpeg", "png")
MIME = {"pdf": "application/pdf", "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}


def save_file(job_id, kind, filename, data, user, allowed=DOCUMENT_TYPES):
    """Keep one file. Returns an error text, or None when saved."""
    name = (filename or "").strip()
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in allowed:
        return f"{name or 'This file'}: only {', '.join(t.upper() for t in allowed)} files are accepted."
    if not data:
        return f"{name}: the file is empty."
    if len(data) > MAX_FILE_MB * 1024 * 1024:
        return f"{name}: the file is bigger than {MAX_FILE_MB} MB."
    if ext == "pdf" and not data[:5] == b"%PDF-":
        return f"{name}: this is not a real PDF file."
    with conn() as c:
        fid = c.execute("INSERT INTO job_files(job_id, kind, filename, mime, size, uploaded_by, uploaded_at) VALUES(?,?,?,?,?,?,?) RETURNING id",
                        (int(job_id), kind, name, MIME[ext], len(data), user, now())).fetchone()["id"]
        c.execute("INSERT INTO job_file_data(file_id, data) VALUES(?,?)", (fid, data))
    return None


def list_files(job_id):
    """The files of a job (without their content, so the list stays fast)."""
    return df("SELECT id, kind, filename, mime, size, uploaded_by, uploaded_at FROM job_files WHERE job_id=? ORDER BY id DESC", (int(job_id),))


@cached(ttl=8)
def deliverable_map():
    """{job_id: (file_id, filename)}: the newest finished PDF of each job, found with one question for the whole table."""
    r = df("SELECT job_id, id, filename FROM job_files WHERE kind='Deliverable' ORDER BY id")
    return {int(a): (int(b), c) for a, b, c in zip(r.job_id, r.id, r.filename)}


def read_file(file_id):
    """The bytes of one file (None when it does not exist)."""
    with conn() as c:
        row = c.execute("SELECT data FROM job_file_data WHERE file_id=?", (int(file_id),)).fetchone()
    return bytes(row["data"]) if row else None


def delete_file(file_id):
    with conn() as c:
        c.execute("DELETE FROM job_file_data WHERE file_id=?", (int(file_id),))
        c.execute("DELETE FROM job_files WHERE id=?", (int(file_id),))


@after_commit
def add_files(job_id, kind, files):
    """files = [(filename, bytes)]. The finished document must be a PDF. Returns an error text, or None."""
    with conn() as c:
        j = fetch_job(c, job_id)
    action = "upload_deliverable" if kind == "Deliverable" else "upload_customer"
    if not j or not can(action, j):
        return "You cannot add files to this job now."
    msgs, saved = [], 0
    for name, data in files:
        err = save_file(job_id, kind, name, data, st.session_state.user, PDF_ONLY if kind == "Deliverable" else DOCUMENT_TYPES)
        if err:
            msgs.append(err)
        else:
            saved += 1
    if saved:
        with conn() as c:
            log_event(c, job_id, "Document added" if kind == "Deliverable" else "Customer document added", f"{saved} file(s)")
            if kind == "Deliverable" and j["status"] == "Ready":
                notify(c, job_id, usernames_with_role(c, CEO) + usernames_with_role(c, RECEPTIONIST), f"Job {j['job_no']}: a new version of the document was uploaded.")
            if kind != "Deliverable" and j["assigned_to"]:
                notify(c, job_id, [j["assigned_to"]], f"Job {j['job_no']}: the customer's documents were updated.")
    return " ".join(msgs) or None


def remove_file(job_id, file_id):
    """The CEO, or the person who uploaded the file while the job is still open, can remove it."""
    with conn() as c:
        j = fetch_job(c, job_id)
        f = c.execute("SELECT filename, uploaded_by FROM job_files WHERE id=? AND job_id=?", (int(file_id), int(job_id))).fetchone()
        if not j or not f:
            return "This file no longer exists."
        if not (has(CEO) or (f["uploaded_by"] == st.session_state.user and j["status"] in OPEN_STATUSES)):
            return "You cannot remove this file."
        log_event(c, job_id, "File removed", f["filename"])
    delete_file(file_id)
    return None


def _msg(err, ok):
    if err:
        st.error(err)
    else:
        done(ok)


def files_section(j, p):
    jid, k = int(j.id), f"{p}{int(j.id)}_"
    if not can("see_files", j):
        return st.caption("Documents are not shown to your role.")
    files = list_files(jid)
    release = can_open_deliverable(j)
    st.caption("Documents are optional. The finished PDF can be opened when the job is paid in full.")
    if files.empty:
        st.caption("No file yet.")
    for _, f in files.iterrows():
        locked = f.kind == "Deliverable" and not release
        a, b, c, d = st.columns([3, 1.4, 2, 1.6])
        a.markdown(f"**{f.filename}**  \n{f.kind} · {f['size'] / 1024:,.0f} KB")
        b.caption(f"{f.uploaded_by}")
        c.caption(str(f.uploaded_at).replace("T", " "))
        if locked:
            d.caption("Locked until paid")
        elif d.button("Get file", key=K(f"{k}gf{int(f.id)}")):
            st.session_state["file_ready"] = int(f.id)
    ready = st.session_state.get("file_ready")
    if ready and ready in set(files.id.astype(int)):
        f = files[files.id == ready].iloc[0]
        if not (f.kind == "Deliverable" and not release):
            data = read_file(ready)
            if data:
                st.download_button(f":material/download: Download {f.filename}", data, f.filename, mime=f.mime, type="primary", key=K(f"{k}dl{ready}"))
                st.caption("Open the file, then print it from the PDF viewer (Ctrl+P).")
    if can("upload_customer", j):
        with st.expander("Add the customer's documents (optional)"):
            up = st.file_uploader("PDF, JPG or PNG", type=["pdf", "jpg", "jpeg", "png"], accept_multiple_files=True, key=K(k + "uc"))
            if up and st.button("Save the documents", key=K(k + "ucb")):
                _msg(add_files(jid, "Customer document", [(u.name, u.getvalue()) for u in up]), f"{len(up)} document(s) saved.")
    if can("upload_deliverable", j):
        with st.expander("Attach the finished document, PDF (optional)"):
            up = st.file_uploader("PDF only", type=["pdf"], accept_multiple_files=True, key=K(k + "ud"))
            if up and st.button("Save the PDF", type="primary", key=K(k + "udb")):
                _msg(add_files(jid, "Deliverable", [(u.name, u.getvalue()) for u in up]), f"{len(up)} PDF saved.")
    if len(files) and j.status != "Collected":
        mine = files[(files.uploaded_by == st.session_state.user) | has(CEO)]
        if len(mine):
            with st.expander("Remove a file"):
                pick = st.selectbox("File", mine.filename.tolist(), index=None, placeholder="Choose a file", key=K(k + "rf"))
                if pick and st.button("Remove this file", key=K(k + "rfb")):
                    _msg(remove_file(jid, int(mine[mine.filename == pick].id.iloc[0])), "File removed.")


# ======================================================================
# from services/money.py
# ======================================================================

@cached(ttl=8)
def load_payments(job_id=None):
    sql = """SELECT p.id, p.job_id, j.job_no, c.name AS customer, p.paid_on AS date, p.amount, p.method, p.reference, p.received_by, p.note
             FROM job_payments p JOIN jobs j ON j.id=p.job_id JOIN customers c ON c.id=j.customer_id"""
    out = df(sql + (" WHERE p.job_id=?" if job_id else "") + " ORDER BY p.paid_on DESC, p.id DESC", (int(job_id),) if job_id else ())
    if not sees_money():  # an Engineer never sees payments
        out = out.iloc[0:0]
    return out.reset_index(drop=True)


@after_commit
def add_payment(job_id, amount, method, reference, paid_on):
    with conn() as c:
        j = fetch_job(c, job_id)
        if not j or not can("pay", j):
            return "You cannot record a payment on this job."
        amount = int(amount or 0)
        if amount <= 0:
            return "Enter the amount paid."
        if method not in PAY_METHODS:
            return "Choose how it was paid."
        if amount > j["balance"]:
            return f"The amount is more than the balance ({j['balance']:,.0f} Frw)."
        if paid_on and str(paid_on) > str(date.today()):
            return "The date is in the future."
        c.execute("INSERT INTO job_payments(job_id, amount, method, reference, paid_on, received_by, note, created_at) VALUES(?,?,?,?,?,?,?,?)",
                  (int(job_id), amount, method, (reference or "").strip(), str(paid_on or date.today()), st.session_state.user, "", now()))
        left = j["balance"] - amount
        log_event(c, job_id, "Payment", f"{amount:,} Frw ({method})" + (". Paid in full." if left <= 0 else f". Balance {left:,.0f} Frw."))
        if left <= 0 and j["status"] == "Ready":
            notify(c, job_id, usernames_with_role(c, RECEPTIONIST) + usernames_with_role(c, CEO), f"Job {j['job_no']} ({j['customer']}) is paid in full and ready to hand over.")
    return None


def delete_payment(payment_id):
    if not has(CEO):
        return "Only the CEO can delete a payment."
    with conn() as c:
        p = c.execute("SELECT job_id, amount FROM job_payments WHERE id=?", (int(payment_id),)).fetchone()
        if p:
            c.execute("DELETE FROM job_payments WHERE id=?", (int(payment_id),))
            log_event(c, p["job_id"], "Payment deleted", f"{int(p['amount']):,} Frw")
    return None


def money_section(j, p):
    """The Money part of an opened job."""
    jid, k = int(j.id), f"{p}{int(j.id)}_"
    pays = load_payments(jid)
    st.markdown(f"Fee **{j.fee:,.0f}** · Paid **{j.paid:,.0f}** · Balance **{j.balance:,.0f}** Frw")
    if pays.empty:
        st.caption("No payment recorded yet.")
    else:
        sheet = pays.assign(Date=pays["date"], Item=pays.method + (" " + pays.reference.fillna("")).str.rstrip(), Qty=1.0, **{"P/U": pays.amount, "Total": pays.amount})
        t = sheet_number(sheet, ["No", "Date", "Item", "Qty", "P/U", "Total", "received_by"]).rename(columns={"received_by": "Received by"})
        wide(st.dataframe, pretty(t, ["P/U", "Total"], qty_cols=["Qty"]), hide_index=True)
        download_sheet(sheet.rename(columns={"received_by": "Received by"}), ["Received by"], f"Payments {j.job_no}", k + "jp")
        if has(CEO):
            with st.expander("Delete a payment (CEO)"):
                labels = {f"{r.date} · {r.amount:,.0f} Frw · {r.method}": int(r.id) for r in pays.itertuples()}
                chosen = st.selectbox("Payment", list(labels), index=None, placeholder="Choose a payment", key=K(k + "dp"))
                if chosen and st.button("Delete this payment", key=K(k + "dpb")):
                    _msg(delete_payment(labels[chosen]), "Payment deleted.")
    if can("pay", j) and j.balance > 0:
        st.markdown("**Record a payment**")
        bal = int(j.balance)
        quick = {f"Full balance ({bal:,})": bal, f"Half ({bal // 2:,})": bal // 2, "Other amount": None}
        choice = pick("Amount", list(quick), K(k + "amt"), default=f"Full balance ({bal:,})")
        amount = quick.get(choice)
        if choice == "Other amount" or choice is None:
            amount = st.number_input("Amount (Frw)", min_value=0, max_value=bal, step=1000, value=None, placeholder=f"up to {bal:,}", key=K(k + "pa"))
        method = pick("Paid by", PAY_METHODS, K(k + "pm"), default="Mobile money") or "Mobile money"
        a, b = st.columns(2)
        ref = a.text_input("Reference (optional)", key=K(k + "pr"), placeholder="Receipt or transaction number")
        day = b.date_input("Date", date.today(), max_value=date.today(), key=K(k + "pd"))
        if st.button("Save payment", type="primary", key=K(k + "pb"), disabled=not amount):
            _msg(add_payment(jid, amount, method, ref, day), "Payment recorded.")


def payments_tab():
    """The Payments tab: money received for services, and the balances still to pay."""
    pays, jobs = load_payments(), load_jobs()
    live = jobs[jobs.status != "Cancelled"]
    unpaid = live[live.balance > 0]
    month = str(date.today())[:7]
    kpi_grid([kpi("Received this month", money(pays[pays["date"].astype(str).str[:7] == month].amount.sum()) if len(pays) else "0", "Frw for services", "green"),
              kpi("Received in total", money(pays.amount.sum()) if len(pays) else "0", f"{len(pays)} payment(s)", "blue"),
              kpi("Still to pay", money(unpaid.balance.sum()) if len(unpaid) else "0", f"{len(unpaid)} job(s) with a balance", "red" if len(unpaid) else "green")])
    st.subheader("Payments")
    a, b, c = st.columns([2, 2, 3])
    months = ["All months"] + sorted({str(d)[:7] for d in pays["date"]}, reverse=True) if len(pays) else ["All months"]
    sel_m = a.selectbox("Month", months, key="jp_month")
    method = b.selectbox("Paid by", ["All"] + sorted(set(pays.method)) if len(pays) else ["All"], key="jp_method")
    with c:
        q = search_box("jp_q", "Search job, customer or reference", "Type part of a word…")
    view = pays if sel_m == "All months" else pays[pays["date"].astype(str).str[:7] == sel_m]
    view = view if method == "All" else view[view.method == method]
    view = match(view, ["job_no", "customer", "reference", "received_by"], q)
    sheet = view.assign(Date=view["date"], Item=view.job_no + " " + view.customer, Qty=1.0, **{"P/U": view.amount, "Total": view.amount})
    t = sheet_number(sheet, CORE + ["method", "reference", "received_by"]).rename(columns={"method": "Paid by", "reference": "Reference", "received_by": "Received by"})
    st.markdown(f"**{len(t)} payment(s) · Total {money(view.amount.sum()) if len(view) else '0'} Frw**")
    wide(st.dataframe, pretty(t, ["P/U", "Total"], qty_cols=["Qty"]), hide_index=True)
    if len(t):
        download_sheet(sheet.rename(columns={"method": "Paid by", "reference": "Reference", "received_by": "Received by"}), ["Paid by", "Reference", "Received by"], "Service payments", "jp_dl")
    st.subheader("Balances still to pay")
    if unpaid.empty:
        return st.success("Every job is paid in full.")
    bal = unpaid[["job_no", "customer", "phone", "service", "status", "fee", "paid", "balance"]].rename(
        columns={"job_no": "Job no", "customer": "Customer", "phone": "Phone", "service": "Service", "status": "Status", "fee": "Fee", "paid": "Paid", "balance": "Balance"})
    bal = bal.sort_values("Balance", ascending=False).reset_index(drop=True)
    bal.insert(0, "No", range(1, len(bal) + 1))
    wide(st.dataframe, pretty(bal, ["Fee", "Paid", "Balance"], "Status"), hide_index=True)
    st.download_button(":material/download: Download Excel", to_excel({"Balances": bal}), f"balances_{date.today()}.xlsx", mime=XLSX, key="dl_bal")


# ======================================================================
# from services/dashboard.py
# ======================================================================

STATUS_COLORS = {"With CEO": "#e0922f", "Assigned": "#2f6fb0", "Waiting for customer": "#b5527f", "Done": "#7a5fc4", "Reverted": "#f0605d", "Ready": "#2e9b57", "Collected": "#7b8794", "Cancelled": "#9aa3ad"}


def _last_months(n=6):
    out, cur = [], date.today().replace(day=1)
    for _ in range(n):
        out.insert(0, str(cur)[:7])
        cur = (cur - timedelta(days=1)).replace(day=1)
    return out


def _who():
    if has(CEO):
        return "boss"
    return "reception" if has(RECEPTIONIST) else "engineer"


def service_cards(jobs, pays, who):
    month, months = str(date.today())[:7], _last_months()
    labs = [month_label(m, False) for m in months]
    open_ = jobs[jobs.status.isin(OPEN_STATUSES)]
    ready, waiting = jobs[jobs.status == "Ready"], jobs[jobs.status == "Waiting for customer"]
    unpaid = jobs[(jobs.status != "Cancelled") & (jobs.balance > 0)]
    overdue = int(jobs.overdue.sum())
    reg = jobs.assign(Date=jobs.created_at.astype(str), One=1)
    reg_m = monthly_vals(reg, "One", months)
    got_month = float(pays[pays["date"].astype(str).str[:7] == month].amount.sum()) if len(pays) else 0.0
    coll_month = int((jobs.collected_at.astype(str).str[:7] == month).sum())
    if who == "boss":
        return [kpi("Open jobs", len(open_), "not collected yet", "blue", spark_svg(reg_m, labs, "#2f6fb0"), trend(reg_m[-1], reg_m[-2], labs[-2], lambda v: f"{v:,.0f}")),
                kpi("Needs your decision", int(jobs.status.isin(["With CEO", "Done"]).sum()), "to assign, or to check", "amber" if jobs.status.isin(["With CEO", "Done"]).any() else "green"),
                kpi("Ready for the customer", len(ready), "document finished", "green"),
                kpi("Overdue", overdue, "past the promised date", "red" if overdue else "green"),
                kpi("Still to pay", money(unpaid.balance.sum()) if len(unpaid) else "0", f"Frw · {len(unpaid)} job(s)", "red" if len(unpaid) else "green"),
                kpi("Received this month", money(got_month), "Frw for services", "green")]
    if who == "reception":
        return [kpi("Open jobs", len(open_), "not collected yet", "blue", spark_svg(reg_m, labs, "#2f6fb0"), trend(reg_m[-1], reg_m[-2], labs[-2], lambda v: f"{v:,.0f}")),
                kpi("Ready to hand over", len(ready), f"{int((ready.balance > 0).sum())} not paid in full", "green" if not (ready.balance > 0).any() else "amber"),
                kpi("Waiting for customer", len(waiting), "an engineer needs something", "amber" if len(waiting) else "green"),
                kpi("Customer details missing", int((open_.missing != "").sum()), "open jobs with no email or phone", "amber" if (open_.missing != "").any() else "green"),
                kpi("Registered this month", int(reg_m[-1]), "new jobs", "blue"),
                kpi("Collected this month", coll_month, "documents handed over", "grey")]
    todo = int(jobs.status.isin(["Assigned", "Reverted"]).sum())
    finished = int((jobs.ready_at.astype(str).str[:7] == month).sum())
    return [kpi("My open jobs", len(open_), "not collected yet", "blue"),
            kpi("To do", todo, "given to you, or sent back to you", "amber" if todo else "green"),
            kpi("Waiting for customer", len(waiting), "you asked the customer for something", "amber" if len(waiting) else "green"),
            kpi("Overdue", overdue, "past the promised date", "red" if overdue else "green"),
            kpi("Ready for the customer", len(ready), "finished, waiting to be collected", "green"),
            kpi("Finished this month", finished, "marked ready", "grey")]


def service_says(jobs, who):
    says = []
    new = int((jobs.status == "With CEO").sum())
    if who == "boss" and new:
        says.append((AMBER, f"<b>To assign:</b> {new} job(s) are waiting for your decision: give to an engineer, or send back."))
    overdue = jobs[jobs.overdue]
    if len(overdue):
        says.append((RED, f"<b>Overdue:</b> {len(overdue)} job(s) are past the promised date ({html.escape(', '.join(overdue.job_no.head(3)))}{'…' if len(overdue) > 3 else ''})."))
    ready = jobs[jobs.status == "Ready"]
    if len(ready):
        unpaid = ready[ready.balance > 0]
        if who != "engineer" and len(unpaid):
            says.append((AMBER, f"<b>Ready but not paid:</b> {len(unpaid)} finished job(s) cannot be handed over before payment" + (f" ({money(unpaid.balance.sum())} Frw)." if who == "boss" else ".")))
        else:
            says.append((GREEN, f"<b>Ready:</b> {len(ready)} finished job(s) are waiting for the customer."))
    wait = jobs[jobs.status == "Waiting for customer"]
    if len(wait):
        says.append((AMBER, f"<b>Waiting for customer:</b> {len(wait)} job(s) cannot continue until the customer gives what was asked."))
    n = unread_count()
    if n:
        says.append(("#2f6fb0", f"<b>Notifications:</b> you have {n} new message(s) on the Jobs page."))
    return says


def services_tab(who=None):
    who = who or _who()
    if not has(CEO, RECEPTIONIST, ENGINEER):
        return st.info("The customers' jobs are handled by the CEO, the Receptionist and the Engineers.")
    jobs = load_jobs()
    pays = load_payments() if sees_all_jobs() else pd.DataFrame(columns=["date", "amount"])
    st.markdown('<div class="dsec">Services</div><div class="dcap">Consultancy, house design and building permits</div>', unsafe_allow_html=True)
    if jobs.empty:
        return st.info("No job has been registered yet." if sees_all_jobs() else "No job has been given to you yet.")
    kpi_grid(service_cards(jobs, pays, who))
    st.markdown('<div class="dsec">What the data says</div>', unsafe_allow_html=True)
    insights_html(service_says(jobs, who))
    st.markdown('<div class="dsec">Analysis</div><div class="dcap">Hover a chart for the exact value. Every chart has a Download data button.</div>', unsafe_allow_html=True)
    money_ok = who == "boss"  # the receptionist sees what is owed in the Jobs table, not in graphs
    c1, c2 = st.columns(2)
    with c1:
        by = jobs.groupby("status", as_index=False).size().rename(columns={"size": "Jobs"})
        by["o"] = by.status.map({s: i for i, s in enumerate(STATUS_COLORS)})
        by = by.sort_values("o")
        with card("Jobs by status", "Where all the jobs are now", by[["status", "Jobs"]], "jstatus"):
            c_donut(by.rename(columns={"status": "Status"}), "Status", "Jobs", colors=[STATUS_COLORS[s] for s in by.status])
    with c2:
        sv = jobs.groupby("service", as_index=False).size().rename(columns={"size": "Jobs", "service": "Service"}).sort_values("Jobs", ascending=False)
        with card("Jobs by service", "Number of jobs of each service", sv, "jservice"):
            c_bar(sv, "Service", "Jobs", unit="Jobs", color="#2f6fb0")
    c1, c2 = st.columns(2)
    with c1:
        months = _last_months(12)
        reg = jobs.assign(Date=jobs.created_at.astype(str), One=1)
        col = jobs.assign(Date=jobs.collected_at.astype(str), One=1)
        mm = pd.DataFrame({"Month": [month_label(m) for m in months], "Registered": monthly_vals(reg, "One", months), "Collected": monthly_vals(col, "One", months)})
        with card("Jobs registered and collected by month", "Last 12 months", mm, "jmonth"):
            c_grouped(mm, "Month", ["Registered", "Collected"], ytitle="Jobs")
    with c2:
        if who == "boss" or who == "reception":
            wl = jobs[jobs.status.isin(OPEN_STATUSES) & (jobs.assigned_to != "")].groupby("assigned_to", as_index=False).size().rename(columns={"size": "Open jobs", "assigned_to": "Engineer"})
            with card("Work of each engineer", "Open jobs given to each engineer", wl if len(wl) else None, "jwork"):
                if len(wl):
                    c_bar(wl.sort_values("Open jobs", ascending=False), "Engineer", "Open jobs", unit="Jobs", color="#8e5ea2")
                else:
                    st.caption("No job is assigned yet.")
        else:
            soon = jobs[jobs.status.isin(OPEN_STATUSES) & (jobs.due_on != "")].sort_values("due_on")
            tb = soon[["job_no", "customer", "title", "status", "due_on"]].rename(columns={"job_no": "Job no", "customer": "Customer", "title": "Title", "status": "Status", "due_on": "Promised"}).head(10)
            with card("Promised dates", "Your open jobs, the earliest first", tb if len(tb) else None, "jdue"):
                if len(tb):
                    st.dataframe(tb, hide_index=True)
                else:
                    st.caption("Nothing open.")
    c1, c2 = st.columns(2)
    done_ = jobs[(jobs.ready_at != "")]
    with c1:
        if len(done_):
            d = done_.assign(Days=[max((pd.to_datetime(r).normalize() - pd.to_datetime(c).normalize()).days, 0) for r, c in zip(done_.ready_at, done_.created_at)])
            av = d.groupby("service", as_index=False).Days.mean().rename(columns={"service": "Service"})
            av["Days"] = av.Days.round(1)
        else:
            av = pd.DataFrame()
        with card("Days to finish a job", "Average from registering to ready, by service", av if len(av) else None, "jdays"):
            if len(av):
                c_bar(av.sort_values("Days", ascending=False), "Service", "Days", fmt=",.1f", unit="Days", color="#e0922f")
            else:
                st.caption("No job has been finished yet.")
    with c2:
        if money_ok:
            fs = jobs[jobs.status != "Cancelled"].groupby("service", as_index=False)[["fee", "paid"]].sum().rename(columns={"service": "Service", "fee": "Fee", "paid": "Received"})
            with card("Fees and money received by service", "Frw · agreed fees against what was paid", fs if len(fs) else None, "jfees"):
                if len(fs):
                    c_grouped(fs, "Service", ["Fee", "Received"])
                else:
                    st.caption("Nothing to show yet.")
        else:
            wt = jobs[jobs.status == "Waiting for customer"][["job_no", "customer", "phone", "title"]].rename(columns={"job_no": "Job no", "customer": "Customer", "phone": "Phone", "title": "Title"})
            with card("Waiting for the customer", "Jobs where you asked the customer for something", wt if len(wt) else None, "jwait"):
                if len(wt):
                    st.dataframe(wt, hide_index=True)
                else:
                    st.caption("No job is waiting for a customer.")
    if money_ok and len(pays):
        rm = pays.assign(Date=pays["date"].astype(str))
        months = _last_months(12)
        pm = pd.DataFrame({"Month": [month_label(m) for m in months], "Received": monthly_vals(rm, "amount", months)})
        with card("Money received by month", "Frw received for services · last 12 months", pm, "jrecv"):
            c_bar(pm, "Month", "Received", horizontal=False, unit="Frw")


def jobs_dashboard():
    """Landing page of the Receptionist and the Engineer."""
    user = st.session_state.user
    st.markdown(DASH_CSS, unsafe_allow_html=True)
    st.markdown(f'<div class="dh">Dashboard</div><div class="dsub">Services · {html.escape(user)} · updates automatically</div>', unsafe_allow_html=True)
    services_tab()
