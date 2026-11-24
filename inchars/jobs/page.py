"""The Jobs page: one table for everything (like a register book). Eye = read a job, Pencil = work on it (review, send back, assign, add
information), Mail = email the customer the status. The updates that used to be in an Inbox are shown on each row. Also registering a job."""

import html
import pandas as pd
import streamlit as st
from datetime import date, datetime, timedelta

from inchars.accounts import has, smtp_conf
from inchars.jobs.data import (
    NEED_REASONS, OPEN_STATUSES, PAY_METHODS, PERMIT_STAGES, REVERT_REASONS, SERVICES, assign_job, can, can_act, change_status, create_job, edit_job,
    email_status, job_updates, load_customers, load_engineers, load_events, load_jobs, mark_all_read, mark_job_read, missing_info, save_customer,
    sees_all_jobs, sees_money, set_stage, unread_by_job, update_customer_info,
)
from inchars.settings import CEO, DASH_CSS, RECEPTIONIST, XLSX
from inchars.widgets import K, done, due_pick, kpi, kpi_grid, match, pick, run_section, search_box, to_excel, wide


# ======================================================================
# the table of jobs (the old Inbox is part of it: each row shows its new updates)
# ======================================================================

SHOW = ["Open", "Needs me", "New updates", "With CEO", "In review", "Reverted", "Ready", "Waiting", "Missing info", "All"]
PAGE_SIZE = 15
# the coloured label of each status
PILL = {"With CEO": ("#e0922f", ""), "With engineer": ("#2f6fb0", ""), "Waiting for customer": ("#b5527f", ""), "In review": ("#7a5fc4", ""),
        "Reverted": ("#f0605d", "\u2715 "), "Ready": ("#2e9b57", "\u2713 "), "Collected": ("#7b8794", ""), "Cancelled": ("#9aa3ad", "")}
ROW_CSS = """<style>
.jr-h{font-size:.78rem;color:#6b7785;font-weight:600;padding:6px 0;border-bottom:2px solid #cfe9f1}
.jr{font-size:.92rem;padding:7px 0;border-bottom:1px solid #d9eef4;line-height:1.25}
.jr small{color:#8a96a3}
.jr .newu{color:#2f6fb0;font-weight:600}
.pill{display:inline-block;color:#fff;border-radius:14px;padding:3px 12px;font-size:.8rem;font-weight:600;white-space:nowrap}
</style>"""
WIDTHS = [2.0, 1.8, 1.8, 1.6, 2.0, 2.1, 0.5, 0.5, 0.5]


def needs_me(jobs):
    """The jobs that need this person's action now."""
    if has(CEO):
        return jobs.status.isin(["With CEO", "In review"]) | jobs.overdue
    if has(RECEPTIONIST):
        return jobs.status.isin(["Ready", "Waiting for customer"])
    return jobs.status.isin(["With engineer", "Reverted"])  # the Engineer


def apply_show(jobs, show, unread):
    if show == "Open":
        return jobs[jobs.status.isin(OPEN_STATUSES)]
    if show == "Needs me":
        return jobs[needs_me(jobs)]
    if show == "New updates":
        return jobs[jobs.id.astype(int).isin(unread)]
    if show == "Waiting":
        return jobs[jobs.status == "Waiting for customer"]
    if show == "Missing info":
        return jobs[(jobs.missing != "") & jobs.status.isin(OPEN_STATUSES)]
    if show == "All":
        return jobs
    return jobs[jobs.status == show]


def summary_line(jobs, unread):
    n = lambda mask: int(mask.sum())
    parts = [f"{n(jobs.status.isin(OPEN_STATUSES))} open", f"{n(jobs.status == 'With CEO')} with the CEO", f"{n(jobs.status == 'In review')} waiting for the CEO's check",
             f"{n(jobs.status == 'Reverted')} sent back", f"{n(jobs.status == 'Ready')} ready", f"{n(jobs.overdue)} overdue"]
    if unread:
        parts.append(f"{sum(unread.values())} new update(s)")
    return " \u00b7 ".join(parts)


def when(iso):
    """2026-11-23T10:15:00 -> 23/11/2026 10:15:00"""
    try:
        return datetime.fromisoformat(str(iso)).strftime("%d/%m/%Y %H:%M:%S")
    except ValueError:
        return str(iso)


def pill(status):
    color, mark = PILL.get(status, ("#7b8794", ""))
    return f'<span class="pill" style="background:{color}">{mark}{html.escape(status)}</span>'


def table_of(view, money):
    """The same rows as the list, for the Excel download."""
    t = pd.DataFrame({"Date": [when(d) for d in view.created_at], "Job no": view.job_no.values, "Family name": view.surname.values, "First name": view.firstname.values,
                      "Service": view.service.values, "Title": view.title.values, "Engineer": [e or "N/A" for e in view.assigned_to], "Status": view.status.values})
    if money:
        t["Balance"] = view.balance.values
    t["Missing info"] = view.missing.values
    t.insert(0, "No", range(1, len(t) + 1))
    return t


def open_job(job_id, mode):
    """The eye opens the job to read (view); the pencil opens it to work on it (edit)."""
    st.session_state.jb_open, st.session_state.jb_mode = int(job_id), mode
    st.session_state.jb_ver = st.session_state.get("jb_ver", 0) + 1


def job_rows(page_view, unread):
    """The list: one line per job like a register book. Eye = read the job. Pencil = work on it (only if there is something to do). Mail = email the customer the status."""
    st.markdown(ROW_CSS, unsafe_allow_html=True)
    head = st.columns(WIDTHS)
    for col, label in zip(head, ["Date", "Family name", "First name", "Service", "Engineer", "Status", "", "", ""]):
        col.markdown(f'<div class="jr-h">{label}</div>', unsafe_allow_html=True)
    for r in page_view.itertuples():
        job = {"status": r.status, "assigned_to": r.assigned_to, "service": r.service, "balance": r.balance}
        cells = st.columns(WIDTHS, vertical_alignment="center")
        cells[0].markdown(f'<div class="jr">{when(r.created_at)}</div>', unsafe_allow_html=True)
        cells[1].markdown(f'<div class="jr"><b>{html.escape(r.surname.upper())}</b></div>', unsafe_allow_html=True)
        hint = "<br><small>no email</small>" if not r.customer_email else ""
        cells[2].markdown(f'<div class="jr">{html.escape(r.firstname)}{hint}</div>', unsafe_allow_html=True)
        cells[3].markdown(f'<div class="jr">{html.escape(r.service)}</div>', unsafe_allow_html=True)
        cells[4].markdown(f'<div class="jr">{html.escape(r.assigned_to) if r.assigned_to else "N/A"}</div>', unsafe_allow_html=True)
        n = unread.get(int(r.id), 0)
        news = f'<br><span class="newu">\u25cf {n} new</span>' if n else ""
        cells[5].markdown(f'<div class="jr">{pill(r.status)}{news}</div>', unsafe_allow_html=True)
        if cells[6].button(":material/visibility:", key=f"jb_eye{r.id}", help="View this job"):
            open_job(r.id, "view")
        if can_act(job) and cells[7].button(":material/edit:", key=f"jb_pen{r.id}", help="Work on this job: review, send back, assign, add information..."):
            open_job(r.id, "edit")
        if can("notify", job) and cells[8].button(":material/mail:", key=f"jb_mail{r.id}", help="Email the customer the status of this job"):
            st.session_state.jb_msg = email_status(int(r.id))
            st.rerun()


def jobs_tab():
    jobs = load_jobs()
    msg = st.session_state.pop("jb_msg", None)
    if msg:
        {"ok": st.success, "warn": st.warning}.get(msg[0], st.error)(msg[1])
    if jobs.empty:
        return st.info("There are no jobs yet." if sees_all_jobs() else "No job has been given to you yet.")
    money = sees_money()
    unread = unread_by_job()
    st.caption(summary_line(jobs, unread))
    q = search_box("jb_q", "Search", "Job number, name, email, phone, title, engineer...")
    a, b, c, d = st.columns(4)
    show = a.selectbox("Show", SHOW, key="jb_show")
    service = b.selectbox("Service", ["All services"] + SERVICES, key="jb_service")
    engineers = ["All engineers", "Not given yet"] + sorted(set(jobs.assigned_to) - {""})
    who = c.selectbox("Engineer", engineers, key="jb_eng") if sees_all_jobs() else "All engineers"
    months = ["All months"] + sorted({str(x)[:7] for x in jobs.created_at}, reverse=True)
    month = d.selectbox("Month", months, key="jb_month")
    view = apply_show(jobs, show, unread)
    if service != "All services":
        view = view[view.service == service]
    if who == "Not given yet":
        view = view[view.assigned_to == ""]
    elif who != "All engineers":
        view = view[view.assigned_to == who]
    if month != "All months":
        view = view[view.created_at.astype(str).str[:7] == month]
    view = match(view, ["job_no", "customer", "phone", "customer_email", "title", "assigned_to", "status", "stage"], q).reset_index(drop=True)
    sig = (q, show, service, who, month)
    if st.session_state.get("jb_sig") != sig:  # a new search or filter starts on the first page
        st.session_state.jb_sig, st.session_state.jb_page = sig, 0
    pages = max(1, -(-len(view) // PAGE_SIZE))
    page = min(st.session_state.get("jb_page", 0), pages - 1)
    st.caption(f"{len(view)} job(s). Eye: read the job. Pencil: work on it. Mail: email the customer the status.")
    if unread and st.button("Mark all updates as read", key="jb_readall"):
        mark_all_read()
        st.rerun()
    job_rows(view.iloc[page * PAGE_SIZE:(page + 1) * PAGE_SIZE], unread)
    if pages > 1:
        a, b, c = st.columns([1, 3, 1])
        if a.button("\u2039 Previous", key="jb_prev", disabled=page == 0):
            st.session_state.jb_page = page - 1
            st.rerun()
        b.markdown(f'<div style="text-align:center;padding-top:6px">Page {page + 1} of {pages}</div>', unsafe_allow_html=True)
        if c.button("Next \u203a", key="jb_next", disabled=page >= pages - 1):
            st.session_state.jb_page = page + 1
            st.rerun()
    if len(view):
        st.download_button(":material/download: Download Excel", to_excel({"Jobs": table_of(view, money)}), f"jobs_{date.today()}.xlsx", mime=XLSX, key="dl_jobs")
    jid = st.session_state.get("jb_open")
    if jid and jid in set(jobs.id.astype(int)):
        run_section("inchars.jobs.page", "job_panel", jid, "jb")


def jobs_page():
    st.markdown(DASH_CSS, unsafe_allow_html=True)
    try:
        n = sum(unread_by_job().values())
    except Exception:  # noqa: BLE001  (the number is a nicety: the page works without it)
        n = 0
    names = [f"Jobs ({n} new)" if n else "Jobs"] + (["New job"] if has(CEO, RECEPTIONIST) else []) + (["Payments"] if sees_money() else [])
    tabs = dict(zip(names, st.tabs(names)))
    with tabs[names[0]]:
        run_section("inchars.jobs.page", "jobs_tab")
    if "New job" in tabs:
        with tabs["New job"]:
            run_section("inchars.jobs.page", "new_job_form")
    if "Payments" in tabs:
        with tabs["Payments"]:
            run_section("inchars.jobs.extras", "payments_tab")


# from services/new_job.py
# ======================================================================

def new_job_form():
    cust = load_customers()
    st.caption("Register the job when the customer comes. It goes to the CEO. Give the customer the job number: it is needed to collect the document.")
    mode = pick("Customer", ["Existing customer", "New customer"], K("nj_mode"), default="Existing customer" if len(cust) else "New customer") or "New customer"
    cid, new, extra, email = None, {}, {}, ""
    if mode == "Existing customer":
        labels = {f"{r.Name} · {r.Email or 'no email'}" + ("" if r.Phone else " · no phone"): int(r.id) for r in cust.itertuples()}
        chosen = st.selectbox("Search by name or phone", list(labels), index=None, placeholder="Type to search, then choose", key=K("nj_c"))
        cid = labels.get(chosen)
        if cid:
            row = cust[cust.id == cid].iloc[0]
            email = row.Email or ""
            lacking = missing_info(row.Phone, row.Email)
            if lacking:  # the 'information we do not have' bar: optional
                st.info("Information we do not have: " + " and ".join(l for _, l in lacking) + ". Not required: add it now if the customer is here.")
                cols = st.columns(len(lacking))
                for (col, label), box in zip(lacking, cols):
                    extra[col] = box.text_input(label.capitalize(), key=K(f"nj_x{col}"), placeholder="0788 123 456" if col == "phone" else "name@example.com")
    else:
        a, b = st.columns(2)
        new = {"name": a.text_input("Name", key=K("nj_n"), placeholder="Family name first, for example MUKAMWIZA Isabelle"),
               "email": b.text_input("Email (optional)", key=K("nj_e"), placeholder="name@example.com"),
               "phone": a.text_input("Phone number (optional)", key=K("nj_p"), placeholder="0788 123 456")}
        email = new["email"]
        st.caption("With an email address we keep the customer informed by email. Without it we can add it later.")
    service = pick("Service", SERVICES, K("nj_s"), default=SERVICES[0])
    a, b = st.columns([3, 2])
    title = a.text_input("Title", placeholder="For example: 3-bedroom house design, Kicukiro", key=K("nj_t"))
    fee = b.number_input("Fee (Frw)", min_value=0, step=1000, value=None, placeholder="Agreed fee", key=K("nj_f"))
    due = due_pick(K("nj_due"), date.today() + timedelta(days=7))
    a, b = st.columns(2)
    deposit = a.number_input("Deposit paid now (Frw, optional)", min_value=0, step=1000, value=None, placeholder="0", key=K("nj_dep"))
    method = pick("Deposit paid by", PAY_METHODS, K("nj_m"), default="Cash") or "Cash"
    with st.expander("Add the customer's documents (optional)"):
        up = st.file_uploader("PDF, JPG or PNG", type=["pdf", "jpg", "jpeg", "png"], accept_multiple_files=True, key=K("nj_u"))
    if st.button("Register the job", type="primary", key=K("nj_go")):
        if mode == "New customer":
            cid, err = save_customer(new["name"], new["phone"], new["email"])
            if err:
                return st.error(err)
        if not cid:
            return st.warning("Choose the customer.")
        if any((v or "").strip() for v in extra.values()):  # the optional bar was filled in
            err = update_customer_info(cid, extra.get("phone", ""), extra.get("email", ""))
            if err:
                return st.error(err)
            email = extra.get("email") or email
        jid, job_no, err = create_job(cid, service, title, "", fee, due, deposit, method, "")
        if jid is None:
            return st.error(err)
        problem = None
        if up:
            try:
                from inchars.jobs.extras import add_files  # read here: if the Files file has a problem, the job is still registered
                problem = add_files(jid, "Customer document", [(u.name, u.getvalue()) for u in up])
            except Exception:  # noqa: BLE001
                problem = "The documents could not be attached (the Files part has a problem), but the job is registered."
        mail = ("The customer was emailed a thank-you." if email.strip() and smtp_conf()
                else "No email was sent: " + ("there is no email address yet (you can add it in the job)." if not email.strip() else "email is not set up yet."))
        done(f"Job {job_no} registered and sent to the CEO. Give the number {job_no} to the customer. {mail}" + (f" {problem}" if problem else ""))


# ======================================================================


# from services/panel.py
# ======================================================================



def _do(err, ok):
    if err:
        st.error(err)
    else:
        done(ok)


def _reasons(label, options, k):
    """Tap the reasons and/or write one. Returns the text (empty when nothing was chosen)."""
    chosen = pick(label, options, K(k + "r"), multi=True)
    extra = st.text_input("More details (optional)", key=K(k + "rx"))
    return "; ".join(list(chosen) + ([extra.strip()] if extra.strip() else []))


# ---------------------------------------------------------------- the 'information we do not have' bar
def missing_bar(j, p):
    """Shows what we still do not know about this customer. Nothing is required: fill it in when the customer is here, or skip it."""
    lacking = missing_info(j.phone, j.customer_email)
    if not lacking or not can("set_contact", j):
        return
    names = " and ".join(label for _, label in lacking)
    with st.container(border=True):
        st.markdown(f"**Information we do not have: {names}.**")
        st.caption("Not required. Add it now if the customer is here, or leave it. An email address lets us keep the customer informed by email.")
        cols = st.columns(len(lacking) + 1)
        vals = {}
        for (col, label), box in zip(lacking, cols):
            vals[col] = box.text_input(label.capitalize(), key=K(f"{p}mi{col}{int(j.id)}"), placeholder="0788 123 456" if col == "phone" else "name@example.com")
        if cols[-1].button("Save", key=K(f"{p}mis{int(j.id)}")):
            _do(update_customer_info(int(j.customer_id), vals.get("phone", ""), vals.get("email", "")), "Saved. Thank you.")


# ---------------------------------------------------------------- Work: what this person can do now
def work_section(j, p):
    jid, k = int(j.id), f"{p}{int(j.id)}_"
    shown = False
    if j.status == "Reverted" and j.returned_reason:
        st.warning(f"The CEO sent this work back: {j.returned_reason}")
    if can("confirm", j):  # the CEO checks the finished work
        shown = True
        st.markdown(f"**{j.assigned_to} finished this work. Please check it.**")
        st.caption("Open the Files part to read what the engineer attached.")
        if st.button(":material/check_circle: Confirm: the work is good", type="primary", key=K(k + "confirm")):
            _do(change_status(jid, "confirm"), "Confirmed. The Receptionist was told and the customer was emailed that the work is ready.")
        with st.expander("Send it back to the engineer (with a reason)"):
            why = _reasons("What is wrong", REVERT_REASONS, k + "rev")
            if st.button("Send back", key=K(k + "revert"), disabled=not why):
                _do(change_status(jid, "revert", why), "Sent back. The engineer was told.")
    if can("assign", j):
        shown = True
        st.markdown("**Give the job to an engineer**" if not j.assigned_to else "**Change the engineer**")
        engineers = load_engineers()
        if not engineers:
            st.info("There is no engineer yet. Add a user with the Engineer role in Users.")
        else:
            who = pick("Engineer", engineers, K(k + "eng"), default=j.assigned_to if j.assigned_to in engineers else None)
            due = due_pick(K(k + "due"), date.fromisoformat(j.due_on) if j.due_on else None)
            if st.button("Assign", type="primary", key=K(k + "assign"), disabled=not who):
                _do(assign_job(jid, who, due), f"{j.job_no} was given to {who}, who was told.")
    if can("submit", j):
        shown = True
        st.markdown("**When the work is finished**")
        st.caption("You can attach the finished PDF in the Files part (optional). Then send the work to the CEO.")
        note = st.text_input("Note for the CEO (optional)", key=K(k + "subn"))
        if st.button(":material/send: Send to the CEO", type="primary", key=K(k + "submit")):
            _do(change_status(jid, "submit", note), "Sent to the CEO.")
    if can("stage", j):
        shown = True
        with st.expander("Building permit: where is the file?"):
            stage = pick("Stage", PERMIT_STAGES, K(k + "stage"), default=j.stage if j.stage in PERMIT_STAGES else None)
            ref = st.text_input("Government reference number", value=j.gov_ref or "", key=K(k + "ref"))
            if st.button("Save the stage", key=K(k + "stageb"), disabled=not stage):
                _do(set_stage(jid, stage, ref), "Stage saved.")
    if can("resume", j):
        shown = True
        note = st.text_input("Note (optional)", key=K(k + "resn"))
        if st.button(":material/play_arrow: The customer answered: continue the work", type="primary", key=K(k + "resume")):
            _do(change_status(jid, "resume", note), "The work continues. The engineer was told.")
    if can("wait", j):
        shown = True
        with st.expander("Need something from the customer?"):
            text = _reasons("What is needed", NEED_REASONS, k + "need")
            if st.button("Ask the customer", key=K(k + "wait"), disabled=not text):
                _do(change_status(jid, "wait", text), "The customer was emailed (if there is an address) and the Receptionist was told.")
    if can("collect", j):
        shown = True
        st.markdown("**The customer collects the document**")
        override, note = False, ""
        if j.balance > 0:
            st.warning(f"Not paid in full: {j.balance:,.0f} Frw is still to pay. Record the payment in the Money part first.")
            if has(CEO):
                override = st.checkbox("Release without full payment", key=K(k + "ov"))
                note = st.text_input("Reason", key=K(k + "ovr")) if override else ""
        else:
            st.success("Paid in full. The document can be handed over.")
            note = st.text_input("Note (optional)", key=K(k + "cn"))
        if st.button(":material/done_all: Handed to the customer", type="primary", key=K(k + "collect"), disabled=(j.balance > 0 and not override)):
            _do(change_status(jid, "collect", note, override), "Done. The job is closed.")
    if can("reopen", j):
        shown = True
        why = st.text_input("Why is the job reopened", key=K(k + "why"))
        if st.button("Reopen the job", key=K(k + "reopen"), disabled=not why.strip()):
            _do(change_status(jid, "reopen", why), "The job was reopened.")
    if not shown:
        st.caption("Nothing for you to do on this job now.")


# ---------------------------------------------------------------- Details: change the job, cancel it
def details_section(j, p):
    jid, k = int(j.id), f"{p}{int(j.id)}_"
    if can("edit", j):
        title = st.text_input("Title", value=j.title, key=K(k + "et"))
        desc = st.text_area("Description", value=j.description or "", key=K(k + "ed"))
        a, b = st.columns(2)
        fee = a.number_input("Fee (Frw)", min_value=0, step=1000, value=int(j.fee), key=K(k + "ef"))
        due = b.date_input("Promised date", date.fromisoformat(j.due_on) if j.due_on else date.today(), key=K(k + "eD"))
        if st.button("Save the changes", type="primary", key=K(k + "eb")):
            _do(edit_job(jid, title, desc, fee, due), "Saved.")
    if can("cancel", j):
        with st.expander("Cancel this job"):
            why = st.text_input("Reason for cancelling", key=K(k + "cr"))
            if st.button("Cancel the job", key=K(k + "cc"), disabled=not why.strip()):
                _do(change_status(jid, "cancel", why), "The job was cancelled.")


# ---------------------------------------------------------------- History
def history_section(j, p):
    ev = load_events(int(j.id))
    if ev.empty:
        st.caption("Nothing yet.")
    else:
        wide(st.dataframe, ev, hide_index=True)


# ---------------------------------------------------------------- the opened job


def view_section(j, p):
    """Read-only: what the job is, who the customer is, the updates and what happened."""
    st.markdown("**What the customer asked for**")
    st.write(j.description or "No description was written.")
    a, b = st.columns(2)
    a.markdown(f"**Customer**  \n{j.customer}  \n{j.customer_email or 'no email'}  \n{j.phone or 'no phone'}")
    b.markdown(f"**Job**  \n{j.service}" + (f" · {j.stage}" if j.stage else "") + (f" · reference {j.gov_ref}" if j.gov_ref else "")
               + f"  \nRegistered {when(j.created_at)}  \nPromised {j.due_on or '-'}")
    if j.status == "Reverted" and j.returned_reason:
        st.warning(f"The CEO sent this work back: {j.returned_reason}")
    st.markdown("**What happened**")
    history_section(j, p)
    if can_act({"status": j.status, "assigned_to": j.assigned_to, "service": j.service, "balance": j.balance}) and st.button(":material/edit: Work on this job", key=K(f"{p}towork{int(j.id)}")):
        open_job(j.id, "edit")
        st.rerun()


def job_panel(job_id, p="jb"):
    """The eye opens it to read (view). The pencil opens it to work (edit): Work, Details, Files, Money, History.
    The Engineer never sees money: the Money part does not exist for him or her."""
    jobs = load_jobs()
    rows = jobs[jobs.id == job_id]
    if rows.empty:
        return st.warning("This job is not available to you.")
    j = rows.iloc[0]
    money = sees_money()
    mode = st.session_state.get(p + "_mode", "view")
    with st.container(border=True):
        a, b = st.columns([6, 1])
        a.markdown(f"### {j.job_no} · {j.title}" + ("" if mode == "edit" else "  (view)"))
        if b.button("Close", key=K(f"{p}close{int(j.id)}")):
            st.session_state[p + "_open"] = None
            st.rerun()
        st.caption(f"{j.service} · {j.customer} · {j.customer_email or 'no email'} · {j.phone or 'no phone'}")
        news = job_updates(int(j.id))
        fresh = news[news.read_at.isna()] if len(news) else news
        if len(fresh):
            st.info("New for you:\n\n" + "\n\n".join(f"- {w}: {u}" for w, u in zip(fresh["When"], fresh["Update"])))
            mark_job_read(int(j.id))
        if mode == "edit":
            run_section("inchars.jobs.page", "missing_bar", j, p)
        cards = [kpi("Status", j.status, ("stage: " + j.stage) if j.stage else "where the job is now"),
                 kpi("Engineer", j.assigned_to or "not given yet", "doing the work", small=True),
                 kpi("Promised", j.due_on or "-", "overdue" if j.overdue else "date given to the customer")]
        if money:
            cards.append(kpi("Balance", f"{j.balance:,.0f}", "paid in full" if j.balance <= 0 else f"Frw to pay · fee {j.fee:,.0f}"))
        kpi_grid(cards)
        if mode != "edit":
            return view_section(j, p)
        if j.description:
            st.caption(j.description)
        job = {"status": j.status, "assigned_to": j.assigned_to, "service": j.service}
        parts = ["Work"] + (["Details"] if can("edit", job) or can("cancel", job) else []) + ["Files"] + (["Money"] if money else []) + ["History"]
        part = st.selectbox("Part of the job", parts, key=K(f"{p}part{int(j.id)}_{st.session_state.get(p + '_ver', 0)}"))
        where = {"Work": ("inchars.jobs.page", "work_section"), "Details": ("inchars.jobs.page", "details_section"), "Files": ("inchars.jobs.extras", "files_section"),
                 "Money": ("inchars.jobs.extras", "money_section"), "History": ("inchars.jobs.page", "history_section")}[part]
        run_section(where[0], where[1], j, p)
