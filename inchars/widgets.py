"""Shared screen parts: small helpers, KPI cards, charts, the menu and header, the cost sheets, and the helpers of the Jobs side."""

import html
import importlib
import io
import pandas as pd
import re
import streamlit as st
import traceback
from contextlib import contextmanager
from datetime import date, datetime, timedelta

from inchars.accounts import categories_df, has, visible_sites
from inchars.settings import CEO, QUICK_DAYS, XLSX


# ======================================================================
# from components/common.py
# ======================================================================

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
              "New": "background-color:#fff3d6;color:#7a4a00", "With CEO": "background-color:#fff3d6;color:#7a4a00", "Returned": "background-color:#fde2e0;color:#8a1c14", "With engineer": "background-color:#dbeeff;color:#0b3a66", "In review": "background-color:#ece6fb;color:#3d2a85", "Reverted": "background-color:#fde2e0;color:#8a1c14", "Assigned": "background-color:#dbeeff;color:#0b3a66",
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


# ======================================================================
# from components/kpi.py
# ======================================================================

def spark_svg(values, labels, color="#5b9bd5"):
    """Tiny line with month names under it. Always the same blue."""
    color = "#5b9bd5"
    vals = [float(v) for v in values][-6:]
    labs = list(labels)[-6:]
    if len(vals) < 2:
        return ""
    w, h, pad = 170, 46, 14
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    xs = [pad + i * (w - 2 * pad) / (len(vals) - 1) for i in range(len(vals))]
    ys = [8 + (1 - (v - lo) / span) * (h - 22) for v in vals]
    pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys))
    dots = "".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{color}"/>' for x, y in zip(xs, ys))
    txt = "".join(f'<text x="{x:.1f}" y="{h + 6}" font-size="9.5" fill="#6b7a70" text-anchor="middle">{html.escape(str(l))}</text>' for x, l in zip(xs, labs))
    return (f'<svg class="spark" width="{w}" height="{h + 10}" viewBox="0 0 {w} {h + 10}"><polyline points="{pts}" fill="none" stroke="{color}" '
            f'stroke-width="2.2"/>{dots}{txt}</svg>')


def kpi(label, value, desc, tone="", spark="", delta=None, small=False):
    """One KPI card. Every card has the same light-blue look: `tone` is accepted only so that older calls keep working."""
    d = ""
    if delta:
        d = f'<div class="kx">{html.escape(delta[1])}</div>'
    return (f'<div class="kc"><div class="kl">{html.escape(label)}</div><div class="kv{" sm" if small else ""}">{html.escape(str(value))}</div>'
            f'{spark}{d}<div class="kd">{html.escape(desc)}</div></div>')


def kpi_grid(cards):
    st.markdown(f'<div class="kgrid">{"".join(cards)}</div>', unsafe_allow_html=True)


def trend(cur, prev, prev_label, fmt=money):
    """('up'|'down'|'same', '▲ 12% vs Oct (1,200)') - or None when there is nothing to compare with."""
    if prev is None or prev <= 0:
        return None
    pct = (cur - prev) / prev * 100
    if abs(pct) < 0.5:
        return ("same", f"■ Same as {prev_label} ({fmt(prev)})")
    return ("up" if pct > 0 else "down", f"{'▲' if pct > 0 else '▼'} {abs(pct):.0f}% vs {prev_label} ({fmt(prev)})")


def monthly_vals(frame, col, months, how="sum"):
    if frame is None or frame.empty:
        return [0.0] * len(months)
    g = frame.assign(M=frame.Date.astype(str).str[:7]).groupby("M")[col]
    s = g.sum() if how == "sum" else g.size()
    return [float(s.get(m, 0.0)) for m in months]


def insights_html(rows):
    if not rows:
        rows = [("#1b8a4b", "Nothing needs your attention right now.")]
    inner = "".join(f'<div class="ir"><span class="d" style="background:{c}"></span><div>{t}</div></div>' for c, t in rows)
    st.markdown(f'<div class="ins">{inner}</div>', unsafe_allow_html=True)


RED, AMBER, GREEN = "#c9453b", "#e0922f", "#1b8a4b"


# ======================================================================
# from components/charts.py
# ======================================================================

PALETTE = ["#0b6a35", "#e0922f", "#2f6fb0", "#8e5ea2", "#c9453b", "#3aa6a0", "#7c8b2c", "#b5527f", "#5c6b73", "#d4b21f"]


SERIES_COLORS = {"Spent": "#e0922f", "Funds received": "#0b6a35", "Received": "#2f6fb0", "Used": "#e0922f", "Spent (cumulative)": "#e0922f",
                 "Money used": "#8e5ea2", "Transactions": "#2f6fb0", "In stock": "#0b6a35", "Out of stock": "#c9453b"}


AXIS_EXPR = ("datum.value >= 1000000 ? format(datum.value/1000000, ',.1f') + 'M' : "
             "datum.value >= 1000 ? format(datum.value/1000, ',.0f') + 'K' : datum.value")


def short(v):
    """12,345,678 -> 12.3M, so labels on charts stay short and readable."""
    v = float(v)
    a = abs(v)
    if a >= 1e9:
        return f"{v / 1e9:.1f}B"
    if a >= 1e6:
        return f"{v / 1e6:.1f}M"
    if a >= 1e3:
        return f"{v / 1e3:.0f}K"
    return f"{v:,.0f}" if float(v).is_integer() else f"{v:,.1f}"


def month_label(m, with_year=True):
    try:
        return datetime.strptime(m + "-01", "%Y-%m-%d").strftime("%b %Y" if with_year else "%b")
    except Exception:
        return str(m)


def _alt():
    try:
        import altair as alt
        return alt
    except Exception:
        return None


def _recs(frame, cols):
    out = []
    for row in frame[cols].itertuples(index=False):
        out.append({c: (None if pd.isna(v) else (v.item() if hasattr(v, "item") else v)) for c, v in zip(cols, row)})
    return out


def _draw(chart, key=None, select=False):
    """Draw a chart across the page width. With select=True a click on a bar is returned (needs a recent Streamlit)."""
    attempts = []
    if select:
        attempts += [dict(width="stretch", on_select="rerun", key=key), dict(use_container_width=True, on_select="rerun", key=key)]
    attempts += [dict(width="stretch"), dict(use_container_width=True)]
    for kw in attempts:
        try:
            return st.altair_chart(chart, **kw)
        except TypeError:
            continue
    return None


def _none(msg="Nothing to show yet."):
    st.caption(msg)


PAD = {"left": 8, "right": 48, "top": 14, "bottom": 8}


def c_bar(frame, label, value, horizontal=True, highlight=None, xtitle=None, unit="Frw", key=None, select=False, color=None, fmt=",.0f"):
    """Bars with the value written at the end of each bar. Returns the clicked label when select=True."""
    if frame is None or frame.empty:
        return _none()
    alt = _alt()
    try:
        rec = frame[[label, value]].copy()
        rec["Label"] = [short(v) for v in rec[value]]
        rec["Hi"] = [bool(highlight) and str(l) == str(highlight) for l in rec[label]]
        top = max(float(rec[value].max()), 1.0)
        data = alt.Data(values=_recs(rec, [label, value, "Label", "Hi"]))
        base = alt.Chart(data)
        col = alt.condition("datum.Hi", alt.value("#e0922f"), alt.value(color or "#0b6a35"))
        tip = [alt.Tooltip(f"{label}:N", title=label), alt.Tooltip(f"{value}:Q", format=fmt, title=unit)]
        if horizontal:
            y = alt.Y(f"{label}:N", sort="-x", title=None, axis=alt.Axis(labelLimit=260, labelFontSize=12, ticks=False, domain=False))
            x = alt.X(f"{value}:Q", title=xtitle or unit, scale=alt.Scale(domain=[0, top * 1.22], nice=False),
                      axis=alt.Axis(labelExpr=AXIS_EXPR, grid=True, tickCount=5, title=xtitle or unit))
            bars = base.mark_bar(cornerRadiusEnd=4, size=20).encode(y=y, x=x, color=col, tooltip=tip)
            txt = base.mark_text(align="left", dx=5, fontSize=12, fontWeight="bold", color="#1d2b22").encode(y=y, x=x, text="Label:N")
            height = max(120, 32 * len(rec) + 40)
        else:
            x = alt.X(f"{label}:N", sort=None, title=xtitle, axis=alt.Axis(labelAngle=-35, labelLimit=140, labelFontSize=11, ticks=False))
            y = alt.Y(f"{value}:Q", title=unit, scale=alt.Scale(domain=[0, top * 1.2], nice=False), axis=alt.Axis(labelExpr=AXIS_EXPR, tickCount=5))
            bars = base.mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(x=x, y=y, color=col, tooltip=tip)
            txt = base.mark_text(align="center", dy=-8, fontSize=11, fontWeight="bold", color="#1d2b22").encode(x=x, y=y, text="Label:N")
            height = 290
        if select:
            pick = alt.selection_point(name="pick", fields=[label])
            bars = bars.add_params(pick).encode(opacity=alt.condition(pick, alt.value(1), alt.value(0.6)))
        ev = _draw((bars + txt).properties(height=height, padding=PAD), key=key, select=select)
        if select and ev is not None:
            sel = getattr(ev, "selection", None)
            if sel is None and isinstance(ev, dict):
                sel = ev.get("selection")
            items = (sel or {}).get("pick") or []
            return items[0].get(label) if items else None
    except Exception:
        bars_html(frame, label, value)
    return None


def bars_html(frame, label, value):
    bars(frame, label, value, fmt=lambda v: f"{v:,.0f}")


def legend_html(frame, label, value, colors):
    total = max(float(frame[value].sum()), 1e-9)
    items = "".join(f'<div class="lg"><span class="dot" style="background:{c}"></span><span class="ln">{html.escape(str(l))}</span>'
                    f'<span class="lv">{short(v)} · {float(v) / total * 100:.0f}%</span></div>'
                    for l, v, c in zip(frame[label], frame[value], colors))
    st.markdown(f'<div class="legend">{items}</div>', unsafe_allow_html=True)


def c_donut(frame, label, value, center=None, colors=None):
    """Donut in a fixed square (never clipped) with the legend written under it."""
    if frame is None or frame.empty or float(frame[value].sum()) <= 0:
        return _none()
    frame = frame[frame[value] > 0].reset_index(drop=True)
    cols = (colors or PALETTE)
    cols = [cols[i % len(cols)] for i in range(len(frame))]
    alt = _alt()
    try:
        rec = frame[[label, value]].copy()
        rec["Pct"] = rec[value] / rec[value].sum() * 100
        data = alt.Data(values=_recs(rec, [label, value, "Pct"]))
        arc = alt.Chart(data).mark_arc(innerRadius=68, outerRadius=108, stroke="white", strokeWidth=2).encode(
            theta=alt.Theta(f"{value}:Q", stack=True),
            color=alt.Color(f"{label}:N", scale=alt.Scale(domain=list(rec[label]), range=cols), legend=None),
            tooltip=[alt.Tooltip(f"{label}:N"), alt.Tooltip(f"{value}:Q", format=",.0f"), alt.Tooltip("Pct:Q", format=".1f", title="%")])
        ch = arc
        if center:
            ch = arc + alt.Chart(alt.Data(values=[{"t": center}])).mark_text(size=26, fontWeight="bold", color="#0f2a1d").encode(text="t:N")
        _draw(ch.properties(height=240, padding={"left": 10, "right": 10, "top": 10, "bottom": 10}))
        legend_html(frame, label, value, cols)
    except Exception:
        bars_html(frame, label, value)


def _long(frame, x, ys):
    return pd.DataFrame([{x: r[x], "Series": y, "Value": float(r[y])} for _, r in frame.iterrows() for y in ys])


def alt_scale(ys):
    alt = _alt()
    rng = [SERIES_COLORS.get(y, PALETTE[i % len(PALETTE)]) for i, y in enumerate(ys)]
    return alt.Scale(domain=list(ys), range=rng)


def c_lines(frame, x, ys, area=False, ytitle="Frw", xtitle="Month"):
    """Lines over the months, with the value written at each point (or at the last point when there are many)."""
    if frame is None or frame.empty:
        return _none()
    alt = _alt()
    try:
        lg = _long(frame, x, ys)
        lg["Label"] = [short(v) for v in lg.Value]
        n = frame[x].nunique()
        lg["Show"] = True if (len(ys) == 1 and n <= 14) else [i == n - 1 or False for i in lg.groupby("Series").cumcount()]
        top = max(float(lg.Value.max()), 1.0)
        data = alt.Data(values=_recs(lg, [x, "Series", "Value", "Label", "Show"]))
        base = alt.Chart(data).encode(
            x=alt.X(f"{x}:O", sort=None, title=xtitle, axis=alt.Axis(labelAngle=-35, labelFontSize=11)),
            y=alt.Y("Value:Q", title=ytitle, scale=alt.Scale(domain=[0, top * 1.18], nice=False), axis=alt.Axis(labelExpr=AXIS_EXPR, tickCount=5)),
            color=alt.Color("Series:N", scale=alt_scale(ys), legend=alt.Legend(title=None, orient="top")),
            tooltip=[alt.Tooltip(f"{x}:O", title=xtitle), alt.Tooltip("Series:N"), alt.Tooltip("Value:Q", format=",.0f", title="Frw")])
        layers = [base.mark_line(strokeWidth=3), base.mark_point(size=60, filled=True)]
        if area and len(ys) == 1:
            layers.insert(0, base.mark_area(opacity=0.15))
        layers.append(base.transform_filter("datum.Show").mark_text(dy=-11, fontSize=11, fontWeight="bold").encode(text="Label:N"))
        ch = layers[0]
        for extra in layers[1:]:
            ch = ch + extra
        _draw(ch.properties(height=290, padding=PAD))
    except Exception:
        st.caption("This chart could not be drawn.")


def c_grouped(frame, x, ys, stacked=False, ytitle="Frw", xtitle=None):
    """Several bars side by side (or stacked) for every x, with the values written on the bars."""
    if frame is None or frame.empty:
        return _none()
    alt = _alt()
    try:
        lg = _long(frame, x, ys)
        lg["Label"] = [short(v) if v else "" for v in lg.Value]
        top = float(lg.groupby(x).Value.sum().max()) if stacked else float(lg.Value.max())
        data = alt.Data(values=_recs(lg, [x, "Series", "Value", "Label"]))
        enc = dict(x=alt.X(f"{x}:N", sort=None, title=xtitle, axis=alt.Axis(labelAngle=-35, labelLimit=150, labelFontSize=11, ticks=False)),
                   y=alt.Y("Value:Q", title=ytitle, scale=alt.Scale(domain=[0, max(top, 1.0) * 1.18], nice=False),
                           axis=alt.Axis(labelExpr=AXIS_EXPR, tickCount=5)),
                   color=alt.Color("Series:N", scale=alt_scale(ys), legend=alt.Legend(title=None, orient="top")),
                   tooltip=[alt.Tooltip(f"{x}:N", title=xtitle or "Group"), alt.Tooltip("Series:N"), alt.Tooltip("Value:Q", format=",.0f")])
        if not stacked:
            enc["xOffset"] = alt.XOffset("Series:N")
        bars = alt.Chart(data).mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3).encode(**enc)
        ch = bars
        if not stacked and frame[x].nunique() * len(ys) <= 24:
            ch = bars + alt.Chart(data).mark_text(dy=-7, fontSize=10, fontWeight="bold").encode(
                x=enc["x"], y=enc["y"], xOffset=alt.XOffset("Series:N"), text="Label:N")
        _draw(ch.properties(height=300, padding=PAD))
    except Exception:
        st.caption("This chart could not be drawn.")


def c_heat(frame, x, y, value):
    if frame is None or frame.empty:
        return _none()
    alt = _alt()
    try:
        rec = frame[[x, y, value]].copy()
        rec["Label"] = [short(v) for v in rec[value]]
        data = alt.Data(values=_recs(rec, [x, y, value, "Label"]))
        base = alt.Chart(data).encode(x=alt.X(f"{x}:O", sort=None, title=None, axis=alt.Axis(labelAngle=-35, orient="top")),
                                      y=alt.Y(f"{y}:N", title=None, axis=alt.Axis(labelLimit=240)))
        rect = base.mark_rect(cornerRadius=3).encode(color=alt.Color(f"{value}:Q", scale=alt.Scale(range=["#e8f3ec", "#0b6a35"]), legend=None),
                                                      tooltip=[alt.Tooltip(f"{x}:O"), alt.Tooltip(f"{y}:N"), alt.Tooltip(f"{value}:Q", format=",.0f")])
        txt = base.mark_text(fontSize=10).encode(text="Label:N", color=alt.condition(f"datum.{value} > {float(rec[value].max()) * 0.55}", alt.value("white"), alt.value("#1d2b22")))
        _draw((rect + txt).properties(height=max(160, 34 * rec[y].nunique() + 40), padding=PAD))
    except Exception:
        st.caption("This chart could not be drawn.")


@contextmanager
def card(title, sub="", data=None, key="x"):
    """A white card: bold title, grey one-line description, the chart, and a 'Download data (CSV)' button like the health dashboard."""
    with st.container(border=True):
        st.markdown(f'<div class="ct">{html.escape(title)}</div><div class="cs">{html.escape(sub)}</div>', unsafe_allow_html=True)
        yield
        if data is not None and len(data):
            st.download_button("Download data (CSV)", data.to_csv(index=False).encode("utf-8"), f"{norm(title) or 'data'}_{date.today()}.csv",
                               mime="text/csv", key=f"csv_{key}")


# ======================================================================
# from components/sheets.py
# ======================================================================

CORE = ["No", "Date", "Item", "Qty", "P/U", "Total"]


NEW_CAT = "+ New category…"


def sheet_number(frame, cols):
    out = frame.reset_index(drop=True).copy()
    out.insert(0, "No", range(1, len(out) + 1))
    return out[cols]


def _xl_dates(tbl):
    """Real Excel dates (not text), so the file looks like the workbook and can be imported again."""
    tbl = tbl.copy()
    if "Date" in tbl.columns:
        tbl["Date"] = pd.Series([pd.to_datetime(v, errors="coerce") for v in tbl["Date"]], index=tbl.index, dtype=object).map(
            lambda v: None if pd.isna(v) else v.date())
    return tbl


def sheet_with_total(frame):
    """One category sheet exactly like the workbook: No, Date, Item, Qty, P/U, Total, then the Grand total line."""
    t = _xl_dates(sheet_number(frame, CORE))
    line = {"No": None, "Date": None, "Item": "Grand total", "Qty": None, "P/U": None, "Total": float(pd.to_numeric(t.Total).sum())}
    return pd.concat([t, pd.DataFrame([line])], ignore_index=True)


def _sheet_name(name, used):
    base = re.sub(r"[\[\]:*?/\\]", "", str(name)).strip()[:28] or "Sheet"
    out, n = base, 2
    while out.lower() in used:
        out, n = f"{base}_{n}", n + 1
    used.add(out.lower())
    return out


def cost_workbook(frame, category_col="Category"):
    """Excel file with a SUMMARY sheet and one sheet per category (the same layout as the cost workbook)."""
    used = {"summary"}
    sheets = {}
    summ = []
    for cat, part in frame.groupby(category_col):
        sheets[_sheet_name(cat, used)] = sheet_with_total(part)
        summ.append({"Category": cat, "Rows": len(part), "Total": float(pd.to_numeric(part.Total).sum())})
    summary = pd.DataFrame(summ, columns=["Category", "Rows", "Total"])
    if len(summary):
        summary = pd.concat([summary, pd.DataFrame([{"Category": "Grand total", "Rows": int(summary.Rows.sum()),
                                                      "Total": float(summary.Total.sum())}])], ignore_index=True)
    return to_excel({"SUMMARY": summary, **sheets})


def sheet_view(frame, key, extras=(), removable=False):
    """Excel-like table with No, Date, Item, Qty, P/U, Total (and the extra columns), the Grand total, and a
    download bar. Returns the ids ticked for removal when removable=True (the frame needs an id column)."""
    cols = CORE + [c for c in extras if c in frame.columns]
    tbl = sheet_number(frame, cols)
    total = float(pd.to_numeric(tbl.Total).sum()) if len(tbl) else 0.0
    st.markdown(f"**{len(tbl)} rows · Grand total {money(total)} Frw**")
    ticked = []
    if removable and len(tbl):
        t2 = tbl.copy()
        t2["Remove"] = False
        ed = wide(st.data_editor, t2, hide_index=True, key=f"{K(key)}_{len(t2)}_{int(total)}", disabled=cols)
        ticked = [int(i) for i, r in zip(frame.reset_index(drop=True).id, ed["Remove"]) if r]
    else:
        wide(st.dataframe, pretty(tbl, ["P/U", "Total"], qty_cols=["Qty"]), hide_index=True)
    return ticked


def download_sheet(frame, extras, name, key, label="Download Excel"):
    """Download bar for one table. Keeps the Excel layout, so he can keep working in Excel if he prefers."""
    cols = CORE + [c for c in extras if c in frame.columns]
    tbl = _xl_dates(sheet_number(frame, cols))
    if len(tbl):
        line = {c: None for c in cols}
        line.update({"Item": "Grand total", "Total": float(pd.to_numeric(tbl.Total).sum())})
        tbl = pd.concat([tbl, pd.DataFrame([line])], ignore_index=True)
    sheet_name = _sheet_name(name, set())
    st.download_button(f":material/download: {label}", to_excel({sheet_name: tbl}), f"{norm(name) or 'sheet'}_{date.today()}.xlsx",
                       mime=XLSX, key=f"dl_{key}")


def download_workbook(frame, name, key, label="Download Excel (one sheet per category)"):
    if frame.empty:
        return
    st.download_button(f":material/download: {label}", cost_workbook(frame), f"{norm(name) or 'sheets'}_{date.today()}.xlsx",
                       mime=XLSX, key=f"dl_{key}")


def category_pick(key):
    """Category list (the same for everybody, the workbook categories) with the option to add a new one."""
    cats = categories_df().name.tolist()
    choice = st.selectbox("Category", cats + [NEW_CAT], key=key)
    if choice == NEW_CAT:
        return st.text_input("New category name", key=key + "n").strip(), True
    return choice, False


def sheet_editor(key, rows=5):
    """Rows written like in Excel: Date, Item, Qty, P/U and the Total of every row, live, with the Grand total below."""
    n_key = K(key + "_n")
    n = st.session_state.get(n_key, rows)
    widths = [0.5, 1.6, 3.2, 1.0, 1.6, 1.7]
    head = st.columns(widths)
    for col, t in zip(head, ["No", "Date", "Item", "Qty", "P/U (Frw)", "Total"]):
        col.markdown(f"**{t}**")
    recs, grand = [], 0.0
    for i in range(n):
        c = st.columns(widths)
        c[0].markdown(f"{i + 1}")
        d = c[1].date_input("Date", date.today(), max_value=date.today(), key=f"{K(key)}_d{i}", label_visibility="collapsed")
        item = c[2].text_input("Item", key=f"{K(key)}_t{i}", placeholder="Item", label_visibility="collapsed")
        q = c[3].number_input("Qty", min_value=0.0, value=1.0, step=1.0, key=f"{K(key)}_q{i}", label_visibility="collapsed")
        p = c[4].number_input("P/U", min_value=0.0, value=None, step=100.0, key=f"{K(key)}_p{i}", placeholder="0", label_visibility="collapsed")
        line = float(q or 0) * float(p or 0)
        grand += line
        c[5].markdown(f"<div style='padding-top:6px;font-weight:700;text-align:right'>{money(line)}</div>", unsafe_allow_html=True)
        recs.append({"Date": d, "Item": item, "Qty": q, "P/U": p})
    foot = st.columns(widths)
    if foot[1].button("+ Add 5 rows", key=f"{K(key)}_more"):
        st.session_state[n_key] = n + 5
        st.rerun()
    foot[2].markdown("<div style='text-align:right;padding-top:6px'><b>Total</b></div>", unsafe_allow_html=True)
    foot[5].markdown(f"<div style='padding-top:6px;font-weight:800;text-align:right;border-top:2px solid #0b6a35'>{money(grand)}</div>", unsafe_allow_html=True)
    return pd.DataFrame(recs, columns=["Date", "Item", "Qty", "P/U"])


def parse_sheet(ed):
    ok, bad = [], []
    for pos, (_, r) in enumerate(ed.iterrows(), start=1):
        item = str(r["Item"]).strip() if pd.notna(r["Item"]) else ""
        qty, pu = pd.to_numeric(r["Qty"], errors="coerce"), pd.to_numeric(r["P/U"], errors="coerce")
        if not item and (pd.isna(pu) or pu == 0):
            continue  # empty row
        d = pd.to_datetime(r["Date"], errors="coerce")
        d = date.today() if pd.isna(d) else d.date()
        if not item:
            bad.append(f"Row {pos}: write the item name.")
        elif pd.isna(qty) or qty <= 0:
            bad.append(f"Row {pos}: the quantity must be above 0.")
        elif pd.isna(pu) or pu <= 0:
            bad.append(f"Row {pos}: enter the P/U.")
        elif d > date.today():
            bad.append(f"Row {pos}: the date is in the future.")
        else:
            ok.append({"date": d, "item": item, "qty": float(qty), "price": float(pu), "total": int(round(float(qty) * float(pu)))})
    return ok, bad


def sheet_preview(ok):
    if ok:
        prev = pd.DataFrame({"Date": [str(e["date"]) for e in ok], "Item": [e["item"] for e in ok], "Qty": [e["qty"] for e in ok],
                             "P/U": [e["price"] for e in ok], "Total": [e["total"] for e in ok]})
        st.markdown(f"**{len(ok)} row(s) ready · Grand total {money(prev.Total.sum())} Frw**")
        wide(st.dataframe, pretty(sheet_number(prev, CORE), ["P/U", "Total"], qty_cols=["Qty"]), hide_index=True)


def field_target(sites, key):
    """The field a new row belongs to: the chosen field, or a list when All fields is chosen."""
    if len(sites) == 1:
        st.markdown(f"**Field:** {sites.name.iloc[0]}")
        return int(sites.id.iloc[0]), sites.name.iloc[0]
    name = st.selectbox("Add to field", sites.name.tolist(), key=key)
    return int(sites[sites.name == name].id.iloc[0]), name


def add_cost_rows_form(sites, key):
    """Add rows to a category sheet (they are saved as expenses of the field)."""
    from inchars.data import add_expenses  # read here, so a problem in data.py does not stop every page that only uses small helpers
    user = st.session_state.user
    a, b = st.columns(2)
    with a:
        cat, is_new = category_pick(K(key + "c"))
    with b:
        site_id, fname = field_target(sites, K(key + "f"))
    ed = sheet_editor(key + "g")
    ok, bad = parse_sheet(ed)
    sheet_preview(ok)
    for msg in bad:
        st.warning(msg)
    if st.button("Save rows", type="primary", key=f"{key}_save"):
        if not cat:
            st.warning("Write the name of the new category.")
        elif bad:
            st.error("Please correct the lines marked above.")
        elif not ok:
            st.info("Nothing to save yet.")
        else:
            cats = categories_df()
            cat_ids = dict(zip(cats.name, cats.id.astype(int)))
            entries = [{"date": e["date"], "category": None if is_new else cat, "new_category": cat if is_new else "",
                        "item": e["item"], "qty": e["qty"], "price": e["price"], "total": e["total"]} for e in ok]
            add_expenses(site_id, cat_ids, entries, user)
            done(f"{len(ok)} row(s) saved in {cat} for {fname}.")


def cost_frame(exp):
    """Expenses in the workbook layout."""
    return exp.rename(columns={"Unit price": "P/U"})


def sheet_show(frame, extras, name, key):
    """A read-only table in the workbook layout (No, Date, Item, Qty, P/U, Total + extras) with its download bar."""
    cols = CORE + [c for c in extras if c in frame.columns]
    t = sheet_number(frame, cols)
    st.markdown(f"**{len(t)} rows · Grand total {money(pd.to_numeric(t.Total).sum())} Frw**")
    wide(st.dataframe, pretty(t.head(300), ["P/U", "Total"], "Status" if "Status" in t.columns else None, qty_cols=["Qty"]), hide_index=True)
    if len(t):
        download_sheet(frame, extras, name, key)


# ======================================================================
# from components/layout.py
# ======================================================================

def side_menu(items, counts=None):
    """Sidebar menu. items = [(key, label, material_icon)]. Remembers the choice. Returns (key, label)."""
    skey = f"menu_{st.session_state.role}"
    if st.session_state.get(skey) not in [k for k, _, _ in items]:
        st.session_state[skey] = items[0][0]
    with st.sidebar:
        for k, label, icon in items:
            n = (counts or {}).get(k)
            text = f":material/{icon}: {label}" + (f" ({n})" if n else "")
            if st.button(text, key=f"nav_{k}", type="primary" if st.session_state[skey] == k else "secondary"):
                st.session_state[skey] = k
                st.rerun()
    return st.session_state[skey], next(label for k, label, _ in items if k == st.session_state[skey])


def header(title):
    st.title(title)


def user_box():
    """Small user name at the very bottom of the sidebar. Click it to see the role and the Log out button."""
    with st.container(key="userbox"):
        with st.popover(f":material/account_circle: {st.session_state.user}"):
            st.caption(st.session_state.role)
            if st.button(":material/logout: Log out", type="primary", key="logout"):
                st.session_state.clear()
                st.rerun()


def field_bar():
    """The Field drop-down at the top of every data page (All fields, then each field). A person with one field sees its name."""
    st.session_state.ceo_scope = None
    names = visible_sites().name.tolist()
    if not names:
        return "All fields"
    if len(names) == 1 and not has(CEO):
        st.markdown(f"**Field:** {names[0]}")
        st.session_state.ceo_field = names[0]
        return names[0]
    options = ["All fields"] + names
    saved = st.session_state.get("ceo_field", "All fields")
    if saved not in options:
        saved = "All fields"
    a, _ = st.columns([2, 3])
    choice = a.selectbox("Field", options, index=options.index(saved), key="ceo_field_pick")
    st.session_state.ceo_field = choice
    return choice


# ======================================================================
# from services/ui.py
# ======================================================================

def pick(label, options, key, default=None, multi=False):
    """A drop-down. One choice (None if nothing is chosen), or several with multi=True (a list)."""
    options = list(options)
    vis, label = ("visible", label) if label else ("collapsed", "Choose")
    if multi:
        return list(st.multiselect(label, options, default=default or [], key=key, label_visibility=vis, placeholder="Choose one or more"))
    return st.selectbox(label, options, index=options.index(default) if default in options else None, key=key, label_visibility=vis, placeholder="Choose")


def due_pick(key, default=None):
    """The promised date: choose '3 days', '1 week'... or pick a date."""
    quick = dict(QUICK_DAYS)
    choice = pick("Promised date", list(quick) + ["Pick a date"], key=key + "q", default="1 week")
    if choice in quick:
        return date.today() + timedelta(days=quick[choice])
    return st.date_input("Date", default or date.today() + timedelta(days=7), min_value=date.today(), key=key + "d")


def problem(name, error):
    """Show that one part has a problem, in place, without stopping the rest of the page."""
    traceback.print_exc()
    st.error(f"This part ({name}) has a problem. The rest of the page still works." + (f"  Details for the CEO: {type(error).__name__}: {error}" if has(CEO) else ""))


def run_section(module, func, *args):
    """Run one part of a page: `module` is the full path, for example "inchars.jobs.page". If it fails, only that part shows an error."""
    try:
        fn = getattr(importlib.import_module(module), func)
    except Exception as e:  # noqa: BLE001  (a missing or old file)
        return problem(module, e)
    try:
        fn(*args)
    except Exception as e:  # noqa: BLE001
        problem(module, e)
