"""Where uploaded files are kept.

Today the files are stored in the database (tables job_files and job_file_data), because Streamlit Cloud cannot keep files on its
own disk. If the files become big or many (for example large drawings), only the four functions below need to change to use an
online file store such as Cloudflare R2 or Supabase Storage. Nothing else in the app touches file bytes."""

from datetime import datetime

from inchars.config import MAX_FILE_MB
from inchars.db import conn, df

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
                        (int(job_id), kind, name, MIME[ext], len(data), user, datetime.now().isoformat(timespec="seconds"))).fetchone()["id"]
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
