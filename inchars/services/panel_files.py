"""Job panel, 'Documents' part: the customer's documents and the finished PDF. Everything here is optional."""

import streamlit as st

from inchars.accounts import has
from inchars.components.common import K, done
from inchars.config import CEO
from inchars.services.files import add_files, list_files, read_file, remove_file
from inchars.services.rules import can, can_open_deliverable


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
