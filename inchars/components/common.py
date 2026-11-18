"""Small helpers used by every page: money format, tables, search box, Excel export, flash messages."""

import html
import io
import re

import pandas as pd
import streamlit as st


def to_excel(sheets):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        for name, frame in sheets.items():
            frame.to_excel(w, sheet_name=name[:31], index=False)
            ws = w.sheets[name[:31]]
            ws.freeze_panes = "A2"
            for i, col in enumerate(frame.columns, 1):
                width = max([len(str(col))] + [len(str(v)) for v in frame[col].head(300)]) + 2
                ws.column_dimensions[ws.cell(1, i).column_letter].width = min(width, 60)
    return buf.getvalue()


def search_box(key, label="Search drug", placeholder="Type part of a name…"):
    """A visible search bar for a table. Returns the typed text in lower case (press Enter to apply)."""
    return st.text_input(label, placeholder=placeholder, key=key).strip().lower()


def match(frame, cols, text):
    """Keep the rows where any of the columns contains the text (capitals are ignored)."""
    if not text or frame.empty:
        return frame
    hit = pd.Series(False, index=frame.index)
    for c in cols:
        hit |= frame[c].astype(str).str.lower().str.contains(text, regex=False)
    return frame[hit]


def wide(fn, *a, **kw):
    """Full-width table/editor that works on old and new Streamlit versions."""
    try:
        return fn(*a, width="stretch", **kw)
    except Exception:
        return fn(*a, use_container_width=True, **kw)


def K(name):  # widget keys that change after each save, so the form clears itself
    return f"{name}{st.session_state.get('rk', 0)}"


def done(msg):
    st.session_state.flash = msg
    st.session_state.rk = st.session_state.get("rk", 0) + 1
    st.rerun()


def show_flash():
    m = st.session_state.pop("flash", None)
    if m:
        st.success(m)


def money(v):
    return f"{int(round(float(v))):,}"


def norm(s):
    return re.sub(r"[^a-z]", "", str(s).lower())


def _q(v):
    return "" if pd.isna(v) else f"{v:,.2f}".rstrip("0").rstrip(".")


def pretty(frame, money_cols=(), status_col=None, qty_cols=()):
    """Thousands separators for money and quantity columns, and colours for a status column."""
    red, green = "background-color:#fde2e2;color:#7a1f1f", "background-color:#e3f5e8;color:#1b5e30"
    colors = {"Shortfall": red, "OK": green, "No funds": "background-color:#eceff1;color:#455a64",
              "Out of stock": red, "In stock": green, "Rejected": red, "Delivered": green,
              "Pending": "background-color:#fff3d6;color:#7a4a00", "Approved": "background-color:#dbeeff;color:#0b3a66",
              "Paid": green, "Settled": green, "Overdue": red, "Open": "background-color:#fff3d6;color:#7a4a00",
              "New": "background-color:#fff3d6;color:#7a4a00", "Assigned": "background-color:#dbeeff;color:#0b3a66",
              "In progress": "background-color:#dbeeff;color:#0b3a66", "Waiting for customer": "background-color:#ffe6cc;color:#7a3a00",
              "Ready": green, "Collected": "background-color:#eceff1;color:#455a64", "Cancelled": "background-color:#eceff1;color:#455a64"}
    try:
        def _m(v):
            n = pd.to_numeric(v, errors="coerce")
            return "" if pd.isna(n) else f"{n:,.0f}"

        def _qq(v):
            n = pd.to_numeric(v, errors="coerce")
            return "" if pd.isna(n) else _q(n)

        fmt = {c: _m for c in money_cols if c in frame.columns}
        fmt.update({c: _qq for c in qty_cols if c in frame.columns})
        sty = frame.style.format(fmt)
        if status_col in frame.columns:
            sty = (getattr(sty, "map", None) or sty.applymap)(lambda v: colors.get(v, ""), subset=[status_col])
        return sty
    except Exception:
        return frame


def bars(frame, label, value, fmt=None, color=""):
    """Horizontal bars drawn with plain HTML, biggest first. No chart add-on is needed, so it always loads."""
    if frame is None or frame.empty:
        return
    fmt = fmt or money
    top = max(float(frame[value].abs().max()), 1e-9)
    rows = "".join(
        f'<div class="brow"><div class="blabel" title="{html.escape(str(l))}">{html.escape(str(l))}</div>'
        f'<div class="btrack"><div class="bfill {color}" style="width:{max(abs(float(v)) / top * 100, 0.8):.1f}%"></div></div>'
        f'<div class="bval">{fmt(v)}</div></div>' for l, v in zip(frame[label], frame[value]))
    st.markdown(f'<div class="bars">{rows}</div>', unsafe_allow_html=True)


def month_series(frame, date_col, val_col):
    if frame is None or frame.empty:
        return pd.Series(dtype=float)
    return frame.assign(M=frame[date_col].astype(str).str[:7]).groupby("M")[val_col].sum()


def top_other(frame, label, value, n=6):
    """Biggest n rows, everything else together as 'Other'."""
    g = frame.groupby(label, as_index=False)[value].sum().sort_values(value, ascending=False)
    if len(g) > n:
        rest = float(g[value].iloc[n:].sum())
        g = pd.concat([g.head(n), pd.DataFrame([{label: "Other", value: rest}])], ignore_index=True)
    return g
