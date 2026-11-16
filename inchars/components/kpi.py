"""KPI cards (big number with a short description), the small trend line, and the 'What the data says' box."""

import html

import streamlit as st

from inchars.components.common import money


def spark_svg(values, labels, color="#1b8a4b"):
    """Tiny line with month names under it, like the cards of the health dashboard."""
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
    d = ""
    if delta:
        d = f'<div class="kx {delta[0]}">{html.escape(delta[1])}</div>'
    return (f'<div class="kc {tone}"><div class="kl">{html.escape(label)}</div><div class="kv{" sm" if small else ""}">{html.escape(str(value))}</div>'
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
