"""Files of a job (data and the Documents part of an opened job): the customer's documents and the finished PDF. Both are optional.

Where the files are kept: in the database (tables job_files and job_file_data), because Streamlit Cloud cannot keep files on its own
disk. If the files become big or many, only save_file, read_file and delete_file need to change to use an online file store."""

import streamlit as st

from inchars.accounts import has
from inchars.components.common import K, done
from inchars.config import CEO, RECEPTIONIST
from inchars.db import conn, df
from inchars.services.config import MAX_FILE_MB, OPEN_STATUSES
from inchars.services.core import can, can_open_deliverable, fetch_job, log_event, now
from inchars.services.notify import after_commit, notify, usernames_with_role

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
