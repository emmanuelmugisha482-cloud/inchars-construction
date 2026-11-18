"""One job: its status, the actions each role can take, files, payments and history."""

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from inchars.accounts import has
from inchars.components.common import K, done, pretty, wide
from inchars.components.kpi import kpi, kpi_grid
from inchars.components.sheets import download_sheet, sheet_number
from inchars.config import ACCOUNTANT, CEO, ENGINEER, PAY_METHODS, PERMIT, PERMIT_STAGES, RECEPTIONIST
from inchars.data.jobs import (add_files, add_note, add_payment, assign_job, can, can_open_deliverable, change_status, delete_payment,
                               edit_job, load_engineers, load_events, load_jobs, load_payments, remove_file, set_stage)
from inchars.storage import list_files, read_file

TONE = {"New": "amber", "Assigned": "blue", "In progress": "blue", "Waiting for customer": "amber", "Ready": "green",
        "Collected": "grey", "Cancelled": "grey"}


def _msg(err, ok):
    """Show an error, or finish with a message."""
    if err:
        st.error(err)
    else:
        done(ok)


def job_detail(job_id):
    jobs = load_jobs()
    rows = jobs[jobs.id == job_id]
    if rows.empty:
        return st.warning("This job is not available to you.")
    j = rows.iloc[0]
    money = has(CEO, ACCOUNTANT, RECEPTIONIST)
    st.markdown(f"### {j.job_no} · {j.title}")
    st.caption(f"{j.service} · {j.customer} · {j.phone}")
    cards = [kpi("Status", j.status, "stage: " + str(j.stage) if j.stage else "where the job is now", TONE.get(j.status, "grey")),
             kpi("Engineer", j.assigned_to or "not assigned", "doing the work", "blue" if j.assigned_to else "amber", small=True),
             kpi("Promised date", j.due_on or "-", "overdue" if j.overdue else "date given to the customer", "red" if j.overdue else "grey")]
    if money:
        cards += [kpi("Fee", f"{j.fee:,.0f}", "Frw agreed with the customer", "green"),
                  kpi("Paid", f"{j.paid:,.0f}", "Frw received so far", "blue"),
                  kpi("Balance", f"{j.balance:,.0f}", "paid in full" if j.balance <= 0 else "Frw still to pay", "green" if j.balance <= 0 else "red")]
    kpi_grid(cards)
    if j.description:
        st.caption(j.description)
    files = list_files(job_id)
    n_del = int((files.kind == "Deliverable").sum()) if len(files) else 0
    names = ["Actions", "Files"] + (["Payments"] if money else []) + ["History"]
    tabs = st.tabs(names)
    with tabs[0]:
        actions_tab(j, n_del)
    with tabs[1]:
        files_tab(j, files)
    if money:
        with tabs[2]:
            payments_panel(j)
    with tabs[-1]:
        ev = load_events(job_id)
        if ev.empty:
            st.caption("No history yet.")
        else:
            wide(st.dataframe, ev, hide_index=True)


def actions_tab(j, n_del):
    jid, k = int(j.id), f"{int(j.id)}"
    shown = False
    if can("assign", j):
        shown = True
        st.subheader("Give the job to an engineer" if not j.assigned_to else "Change the engineer")
        engineers = load_engineers()
        if not engineers:
            st.info("There is no engineer yet. Add a user with the Engineer role in Users.")
        else:
            a, b = st.columns(2)
            default = engineers.index(j.assigned_to) if j.assigned_to in engineers else None
            who = a.selectbox("Engineer", engineers, index=default, placeholder="Choose an engineer", key=K("as" + k))
            due = b.date_input("Promised date", date.fromisoformat(j.due_on) if j.due_on else date.today() + timedelta(days=7), key=K("ad" + k))
            if st.button("Assign", type="primary", key=K("ab" + k)):
                _msg(assign_job(jid, who, due) if who else "Choose an engineer.", f"{j.job_no} was given to {who}.")
    if can("start", j):
        shown = True
        st.subheader("Start the work")
        if st.button("Start work", type="primary", key=K("st" + k)):
            _msg(change_status(jid, "start"), "Work started.")
    if can("stage", j):
        shown = True
        st.subheader("Permit: where is the file?")
        a, b = st.columns(2)
        cur = PERMIT_STAGES.index(j.stage) if j.stage in PERMIT_STAGES else None
        stage = a.selectbox("Stage", PERMIT_STAGES, index=cur, placeholder="Choose the stage", key=K("sg" + k))
        ref = b.text_input("Government reference number", value=j.gov_ref or "", key=K("sr" + k))
        if st.button("Save the stage", key=K("sb" + k)):
            _msg(set_stage(jid, stage, ref) if stage else "Choose the stage.", "Stage saved.")
    if can("wait", j):
        shown = True
        st.subheader("Need something from the customer?")
        need = st.text_input("What is needed", key=K("wn" + k), placeholder="For example: a copy of the land title")
        if st.button("Wait for the customer", key=K("wb" + k)):
            _msg(change_status(jid, "wait", need), "The Receptionist was told.")
    if can("resume", j):
        shown = True
        st.subheader("The customer answered")
        note = st.text_input("Note (optional)", key=K("rn" + k))
        if st.button("Continue the work", type="primary", key=K("rb" + k)):
            _msg(change_status(jid, "resume", note), "Work continues.")
    if j.status in ("In progress", "Waiting for customer") and (has(ENGINEER) and j.assigned_to == st.session_state.user or has(CEO)):
        shown = True
        st.subheader("Finished?")
        if n_del == 0:
            st.info("Upload the finished PDF in the Files tab first. Then you can mark the job ready.")
        else:
            note = st.text_input("Note for the Receptionist (optional)", key=K("fn" + k))
            if st.button("Mark as ready for the customer", type="primary", key=K("fb" + k)):
                _msg(change_status(jid, "ready", note), "The CEO and the Receptionist were told.")
    if can("collect", j):
        shown = True
        st.subheader("Customer collects the document")
        override, note = False, ""
        if j.balance > 0:
            st.warning(f"Not paid in full: {j.balance:,.0f} Frw is still to pay. Record the payment in the Payments tab before handing over the document.")
            if has(CEO):
                override = st.checkbox("Release the document without full payment", key=K("ov" + k))
                note = st.text_input("Reason", key=K("ovr" + k)) if override else ""
        else:
            st.success("Paid in full. The document can be handed over.")
            note = st.text_input("Note (optional)", key=K("cn" + k))
        if st.button("Document handed to the customer", type="primary", key=K("cb" + k), disabled=(j.balance > 0 and not override)):
            _msg(change_status(jid, "collect", note, override), "The job is closed as collected.")
    if can("reopen", j):
        shown = True
        st.subheader("Reopen the job")
        why = st.text_input("Why is it reopened", key=K("ro" + k))
        if st.button("Reopen", key=K("rob" + k)):
            _msg(change_status(jid, "reopen", why), "The job was reopened.")
    if can("edit", j):
        with st.expander("Change the details or the fee"):
            title = st.text_input("Title", value=j.title, key=K("et" + k))
            desc = st.text_area("Description", value=j.description or "", key=K("ed" + k))
            a, b = st.columns(2)
            fee = a.number_input("Fee (Frw)", min_value=0, step=1000, value=int(j.fee), key=K("ef" + k))
            due = b.date_input("Promised date", date.fromisoformat(j.due_on) if j.due_on else date.today(), key=K("eD" + k))
            if st.button("Save changes", key=K("eb" + k)):
                _msg(edit_job(jid, title, desc, fee, due), "Changes saved.")
    if can("cancel", j):
        with st.expander("Cancel the job"):
            why = st.text_input("Reason for cancelling", key=K("cr" + k))
            if st.button("Cancel this job", key=K("cc" + k)):
                _msg(change_status(jid, "cancel", why), "The job was cancelled.")
    if can("note", j):
        with st.expander("Add a note to the history"):
            text = st.text_input("Note", key=K("nt" + k))
            if st.button("Save the note", key=K("nb" + k)):
                _msg(add_note(jid, text), "Note saved.")
    if not shown:
        st.caption("Nothing for you to do on this job now.")


def files_tab(j, files):
    jid = int(j.id)
    if not can("see_files", j):
        return st.caption("Documents are not shown to your role.")
    release = can_open_deliverable(j)
    if files.empty:
        st.caption("No files yet.")
    for _, f in files.iterrows():
        locked = f.kind == "Deliverable" and not release
        a, b, c, d = st.columns([3, 1.4, 2, 1.6])
        a.markdown(f"**{f.filename}**  \n{f.kind} · {f['size'] / 1024:,.0f} KB")
        b.caption(f"{f.uploaded_by}")
        c.caption(str(f.uploaded_at).replace("T", " "))
        if locked:
            d.caption("Locked until paid")
        elif d.button("Get file", key=K(f"gf{int(f.id)}")):
            st.session_state["file_ready"] = int(f.id)
    ready = st.session_state.get("file_ready")
    if ready and ready in set(files.id.astype(int)):
        f = files[files.id == ready].iloc[0]
        if not (f.kind == "Deliverable" and not release):
            data = read_file(ready)
            if data:
                st.download_button(f":material/download: Download {f.filename}", data, f.filename, mime=f.mime, type="primary", key=K(f"dl{ready}"))
                st.caption("Open the file, then print it from the PDF viewer (Ctrl+P).")
    if can("upload_customer", j):
        st.subheader("Add the customer's documents")
        up = st.file_uploader("PDF, JPG or PNG", type=["pdf", "jpg", "jpeg", "png"], accept_multiple_files=True, key=K("uc" + str(jid)))
        if up and st.button("Save the documents", key=K("ucb" + str(jid))):
            _msg(add_files(jid, "Customer document", [(u.name, u.getvalue()) for u in up]), f"{len(up)} document(s) saved.")
    if can("upload_deliverable", j):
        st.subheader("Upload the finished document (PDF)")
        up = st.file_uploader("PDF only", type=["pdf"], accept_multiple_files=True, key=K("ud" + str(jid)))
        if up and st.button("Save the PDF", type="primary", key=K("udb" + str(jid))):
            _msg(add_files(jid, "Deliverable", [(u.name, u.getvalue()) for u in up]), f"{len(up)} PDF saved.")
    if len(files):
        mine = files[(files.uploaded_by == st.session_state.user) | has(CEO)]
        if len(mine) and j.status != "Collected":
            with st.expander("Remove a file"):
                pick = st.selectbox("File", mine.filename.tolist(), index=None, placeholder="Choose a file", key=K("rf" + str(jid)))
                if pick and st.button("Remove this file", key=K("rfb" + str(jid))):
                    _msg(remove_file(jid, int(mine[mine.filename == pick].id.iloc[0])), "File removed.")


def payments_panel(j):
    jid = int(j.id)
    pays = load_payments(jid)
    if pays.empty:
        st.caption("No payment recorded yet.")
    else:
        sheet = pays.assign(Date=pays["date"], Item=pays.method + (" " + pays.reference.fillna("")).str.rstrip(), Qty=1.0,
                            **{"P/U": pays.amount, "Total": pays.amount})
        t = sheet_number(sheet, ["No", "Date", "Item", "Qty", "P/U", "Total", "received_by"]).rename(columns={"received_by": "Received by"})
        st.markdown(f"**{len(t)} payment(s) · Total {pays.amount.sum():,.0f} Frw**")
        wide(st.dataframe, pretty(t, ["P/U", "Total"], qty_cols=["Qty"]), hide_index=True)
        download_sheet(sheet.rename(columns={"received_by": "Received by"}), ["Received by"], f"Payments {j.job_no}", "jp" + str(jid))
        if has(CEO):
            with st.expander("Delete a payment (CEO)"):
                labels = {f"{r.date} · {r.amount:,.0f} Frw · {r.method}": int(r.id) for r in pays.itertuples()}
                pick = st.selectbox("Payment", list(labels), index=None, placeholder="Choose a payment", key=K("dp" + str(jid)))
                if pick and st.button("Delete this payment", key=K("dpb" + str(jid))):
                    _msg(delete_payment(labels[pick]), "Payment deleted.")
    if can("pay", j) and j.balance > 0:
        st.subheader("Record a payment")
        a, b, c, d = st.columns([1.4, 1.4, 1.6, 1.4])
        amount = a.number_input("Amount (Frw)", min_value=0, max_value=int(j.balance), step=1000, value=None, placeholder=f"up to {j.balance:,.0f}", key=K("pa" + str(jid)))
        method = b.selectbox("Paid by", PAY_METHODS, key=K("pm" + str(jid)))
        ref = c.text_input("Reference (optional)", key=K("pr" + str(jid)), placeholder="Receipt or transaction number")
        day = d.date_input("Date", date.today(), max_value=date.today(), key=K("pd" + str(jid)))
        if st.button("Save payment", type="primary", key=K("pb" + str(jid))):
            _msg(add_payment(jid, amount, method, ref, day), "Payment recorded.")
