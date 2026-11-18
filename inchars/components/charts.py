"""Charts (Altair) and the white chart card with the Download data button."""

from contextlib import contextmanager
from datetime import date, datetime, timedelta
import html

import pandas as pd
import streamlit as st

from inchars.components.common import bars, norm


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
