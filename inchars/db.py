"""Database: connection (Neon Postgres online, SQLite for local testing), the tables, and small write helpers."""

from contextlib import contextmanager
import os
import sqlite3
import time

import pandas as pd
import streamlit as st

try:
    import psycopg2
    import psycopg2.extras
    import psycopg2.pool
except ImportError:  # only needed online (when DATABASE_URL is set)
    psycopg2 = None

from inchars.config import DB, DEFAULT_CATEGORIES, MONEY_CATEGORIES


def _secret(name):
    try:
        v = st.secrets[name]
    except Exception:
        return None
    return v if isinstance(v, str) else None


DATABASE_URL = _secret("DATABASE_URL") or os.getenv("DATABASE_URL")


IS_PG = bool(DATABASE_URL)


PK = "SERIAL PRIMARY KEY" if IS_PG else "INTEGER PRIMARY KEY AUTOINCREMENT"


BLOB = "BYTEA" if IS_PG else "BLOB"


IntegrityErrors = (sqlite3.IntegrityError,) + ((psycopg2.IntegrityError,) if psycopg2 else ())


_last_used = {}


@st.cache_resource
def _pool():
    if psycopg2 is None:
        raise RuntimeError("psycopg2 is missing. Add psycopg2-binary to requirements.txt.")
    return psycopg2.pool.ThreadedConnectionPool(1, 10, dsn=DATABASE_URL, connect_timeout=20,
                                                 cursor_factory=psycopg2.extras.RealDictCursor)


def _checkout():
    """Get a working connection. Neon sleeps when idle, so test connections that have been unused for a while."""
    pool, last = _pool(), None
    for _ in range(3):
        raw = None
        try:
            raw = pool.getconn()
            if raw.closed or time.time() - _last_used.get(id(raw), 0) > 90:
                with raw.cursor() as cur:
                    cur.execute("SELECT 1")
                raw.rollback()
            return pool, raw
        except Exception as e:
            last = e
            if raw is not None:
                pool.putconn(raw, close=True)
            time.sleep(1.5)
    raise RuntimeError(f"Cannot reach the database right now. Please try again in a moment. ({last})")


class Conn:
    """Lets the same SQL (written with ? marks) run on SQLite and on PostgreSQL."""

    def __init__(self, raw):
        self.raw = raw

    def execute(self, sql, params=()):
        params = tuple(p.item() if hasattr(p, "item") else p for p in params)  # numpy numbers -> python
        cur = self.raw.cursor()
        cur.execute(sql.replace("?", "%s") if IS_PG else sql, params)
        return cur


@contextmanager
def conn():
    if IS_PG:
        pool, raw = _checkout()
        try:
            yield Conn(raw)
            raw.commit()
        except BaseException:
            try:
                raw.rollback()
            except Exception:
                pass
            raise
        finally:
            _last_used[id(raw)] = time.time()
            pool.putconn(raw)
    else:
        raw = sqlite3.connect(DB)
        raw.row_factory = sqlite3.Row
        try:
            yield Conn(raw)
            raw.commit()
        except BaseException:
            raw.rollback()
            raise
        finally:
            raw.close()


def df(sql, params=()):
    with conn() as c:
        cur = c.execute(sql, params)
        rows, cols = cur.fetchall(), [x[0] for x in cur.description]
    return pd.DataFrame([list(r.values()) if isinstance(r, dict) else list(r) for r in rows], columns=cols)


def run(sql, params=()):
    with conn() as c:
        c.execute(sql, params)


class Abort(Exception):
    pass


SCHEMA = [
    f"""CREATE TABLE IF NOT EXISTS sites(
        id {PK}, name TEXT UNIQUE NOT NULL, client TEXT, location TEXT, budget BIGINT DEFAULT 0, start_date TEXT,
        status TEXT DEFAULT 'Active')""",
    "CREATE TABLE IF NOT EXISTS user_sites(user_id INTEGER, site_id INTEGER, PRIMARY KEY(user_id, site_id))",
    f"CREATE TABLE IF NOT EXISTS categories(id {PK}, name TEXT UNIQUE NOT NULL)",
    f"""CREATE TABLE IF NOT EXISTS expenses(
        id {PK}, site_id INTEGER, category_id INTEGER, item TEXT, qty DOUBLE PRECISION, unit_price DOUBLE PRECISION,
        total BIGINT, spent_on TEXT, entered_by TEXT, entered_at TEXT, source TEXT)""",
    f"""CREATE TABLE IF NOT EXISTS funds(
        id {PK}, site_id INTEGER, amount BIGINT, received_on TEXT, source TEXT, note TEXT, entered_by TEXT)""",
    f"""CREATE TABLE IF NOT EXISTS users(
        id {PK}, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, role TEXT NOT NULL, email TEXT UNIQUE)""",
    """CREATE TABLE IF NOT EXISTS reset_codes(
        user_id INTEGER PRIMARY KEY, code_hash TEXT NOT NULL, expires_at TEXT NOT NULL, attempts INTEGER DEFAULT 0)""",
    f"CREATE TABLE IF NOT EXISTS materials(id {PK}, name TEXT UNIQUE NOT NULL, unit TEXT)",
    f"""CREATE TABLE IF NOT EXISTS material_moves(
        id {PK}, site_id INTEGER, material_id INTEGER, kind TEXT, qty DOUBLE PRECISION, moved_on TEXT, detail TEXT,
        entered_by TEXT, entered_at TEXT)""",
    f"""CREATE TABLE IF NOT EXISTS material_requests(
        id {PK}, batch INTEGER, site_id INTEGER, material_id INTEGER, qty_requested DOUBLE PRECISION,
        qty_approved DOUBLE PRECISION, qty_delivered DOUBLE PRECISION, status TEXT DEFAULT 'Pending', needed_by TEXT,
        requested_on TEXT, requested_by TEXT, decided_on TEXT, decided_by TEXT, note TEXT, reason TEXT, delivered_on TEXT)""",
    "CREATE INDEX IF NOT EXISTS idx_mr_site ON material_requests(site_id)",
    "CREATE TABLE IF NOT EXISTS user_roles(user_id INTEGER, role TEXT, PRIMARY KEY(user_id, role))",
    f"CREATE TABLE IF NOT EXISTS money_categories(id {PK}, name TEXT UNIQUE NOT NULL)",
    f"""CREATE TABLE IF NOT EXISTS money_requests(
        id {PK}, site_id INTEGER, category TEXT, purpose TEXT, amount BIGINT, amount_approved BIGINT, needed_by TEXT,
        status TEXT DEFAULT 'Pending', requested_on TEXT, requested_by TEXT, decided_on TEXT, decided_by TEXT,
        reason TEXT, paid_on TEXT, paid_by TEXT)""",
    f"""CREATE TABLE IF NOT EXISTS money_ledger(
        id {PK}, spent_on TEXT, person TEXT, site_id INTEGER, kind TEXT, category TEXT, amount BIGINT, note TEXT,
        request_id INTEGER, entered_by TEXT, entered_at TEXT)""",
    f"""CREATE TABLE IF NOT EXISTS taxes(
        id {PK}, tax_type TEXT, period TEXT, amount BIGINT, paid_on TEXT, reference TEXT, site_id INTEGER, note TEXT,
        entered_by TEXT)""",
    f"""CREATE TABLE IF NOT EXISTS debts(
        id {PK}, party TEXT, direction TEXT, amount BIGINT, due_on TEXT, site_id INTEGER, note TEXT, entered_by TEXT,
        entered_on TEXT)""",
    f"CREATE TABLE IF NOT EXISTS debt_payments(id {PK}, debt_id INTEGER, amount BIGINT, paid_on TEXT, note TEXT, entered_by TEXT)",
    "CREATE INDEX IF NOT EXISTS idx_mm_site ON material_moves(site_id)",
    "CREATE INDEX IF NOT EXISTS idx_exp_site ON expenses(site_id)",
    "CREATE INDEX IF NOT EXISTS idx_exp_date ON expenses(spent_on)",
    "CREATE INDEX IF NOT EXISTS idx_funds_site ON funds(site_id)",
    # ---- services: customers, jobs, payments, files, history, notifications ----
    f"""CREATE TABLE IF NOT EXISTS customers(
        id {PK}, name TEXT NOT NULL, phone TEXT, email TEXT, note TEXT, created_at TEXT, created_by TEXT)""",
    f"""CREATE TABLE IF NOT EXISTS jobs(
        id {PK}, job_no TEXT UNIQUE NOT NULL, customer_id INTEGER, service TEXT, title TEXT, description TEXT,
        status TEXT DEFAULT 'New', stage TEXT, gov_ref TEXT, fee BIGINT DEFAULT 0, due_on TEXT, assigned_to TEXT,
        created_by TEXT, created_at TEXT, ready_at TEXT, collected_at TEXT, collected_by TEXT)""",
    f"""CREATE TABLE IF NOT EXISTS job_payments(
        id {PK}, job_id INTEGER, amount BIGINT, method TEXT, reference TEXT, paid_on TEXT, received_by TEXT, note TEXT, created_at TEXT)""",
    f"""CREATE TABLE IF NOT EXISTS job_files(
        id {PK}, job_id INTEGER, kind TEXT, filename TEXT, mime TEXT, size BIGINT, uploaded_by TEXT, uploaded_at TEXT)""",
    f"CREATE TABLE IF NOT EXISTS job_file_data(file_id INTEGER PRIMARY KEY, data {BLOB})",
    f"""CREATE TABLE IF NOT EXISTS job_events(
        id {PK}, job_id INTEGER, happened_at TEXT, by_user TEXT, event TEXT, detail TEXT)""",
    f"""CREATE TABLE IF NOT EXISTS job_notices(
        id {PK}, job_id INTEGER, to_user TEXT, message TEXT, created_at TEXT, read_at TEXT)""",
]


@st.cache_resource
def init_db():
    with conn() as c:
        if IS_PG:
            c.execute(";\n".join(SCHEMA))
        else:
            for s in SCHEMA:
                c.execute(s)
        if c.execute("SELECT COUNT(*) AS n FROM categories").fetchone()["n"] == 0:
            bulk_insert(c, "categories", ["name"], [(n,) for n in DEFAULT_CATEGORIES])
        if c.execute("SELECT COUNT(*) AS n FROM money_categories").fetchone()["n"] == 0:
            bulk_insert(c, "money_categories", ["name"], [(n,) for n in MONEY_CATEGORIES])
        for stmt in ("ALTER TABLE materials ADD COLUMN category TEXT",
                     "ALTER TABLE money_ledger ADD COLUMN qty DOUBLE PRECISION",
                     "ALTER TABLE money_ledger ADD COLUMN unit_price DOUBLE PRECISION"):
            if IS_PG:
                c.execute(stmt.replace("ADD COLUMN", "ADD COLUMN IF NOT EXISTS"))
            else:
                try:
                    c.execute(stmt)
                except sqlite3.OperationalError:
                    pass  # column already there
        # users made before several roles existed keep their one role
        c.execute("INSERT INTO user_roles(user_id, role) SELECT id, role FROM users WHERE id NOT IN (SELECT user_id FROM user_roles)")


def bulk_insert(c, table, cols, data, chunk=50):
    """Insert many rows with few round trips (one statement per `chunk` rows)."""
    mark = "(" + ",".join("?" * len(cols)) + ")"
    for i in range(0, len(data), chunk):
        part = data[i:i + chunk]
        c.execute(f"INSERT INTO {table}({','.join(cols)}) VALUES " + ",".join([mark] * len(part)),
                  [v for row in part for v in row])


def ensure_named(c, table, name, extra=None):
    """Id of the row called `name` in categories / money_categories / materials (case ignored). Creates it when new."""
    row = c.execute(f"SELECT id FROM {table} WHERE lower(name)=lower(?)", (name.strip(),)).fetchone()
    if row:
        return row["id"]
    if table == "materials":
        return c.execute("INSERT INTO materials(name, unit) VALUES(?,?) RETURNING id", (name.strip(), (extra or "").strip())).fetchone()["id"]
    return c.execute(f"INSERT INTO {table}(name) VALUES(?) RETURNING id", (name.strip(),)).fetchone()["id"]
