"""All the CSS used by the app (login page, side menu, dashboard cards)."""


LOGIN_CSS = """
<style>
header[data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stSidebar"] {display:none !important;}
[data-testid="stApp"], [data-testid="stAppViewContainer"] {background:#ffffff !important;}
.left-panel {position:fixed; top:0; left:0; width:50vw; height:100vh; z-index:1;
  background:#9fd0b0 url("__BG__") center/cover no-repeat;}
.left-panel .overlay {position:absolute; inset:0; display:flex; flex-direction:column; justify-content:flex-end;
  padding:3.5rem; background:linear-gradient(to top, rgba(196,228,205,.97) 0%, rgba(196,228,205,.65) 40%, rgba(196,228,205,0) 75%);}
.left-panel h1 {color:#0a3d1f !important; font-size:2.6rem; font-weight:800; line-height:1.15; margin:0; padding:0;}
.left-panel p {color:#2f6b46 !important; font-size:1.5rem; margin:.5rem 0 0 0;}
[data-testid="stMainBlockContainer"], .block-container {margin-left:50vw !important; width:50vw !important;
  max-width:50vw !important; padding:14vh 7vw 2rem 7vw !important;}
[data-testid="stApp"] h2, [data-testid="stApp"] label, [data-testid="stApp"] p, [data-testid="stApp"] span {color:#1f2937;}
.sub {color:#6b7280 !important; margin-bottom:1.2rem;}
[data-testid="stTextInput"] input {background:#f3f4f6 !important; color:#111827 !important;}
.stButton button, [data-testid="stFormSubmitButton"] button {width:100%; border-radius:8px;}
[data-testid="stFormSubmitButton"] button {background:#0b6a35; border:0;}
[data-testid="stFormSubmitButton"] button p {color:#fff !important; font-weight:600;}
@media (max-width: 800px) {
  .left-panel {position:relative; width:100vw; height:30vh;}
  .left-panel .overlay {padding:1.2rem;} .left-panel h1 {font-size:1.5rem;} .left-panel p {font-size:1rem;}
  [data-testid="stMainBlockContainer"], .block-container {margin-left:0 !important; width:100% !important;
    max-width:100% !important; padding:1.5rem !important;}
}
.left-panel {background:linear-gradient(160deg,#eef8f1 0%,#bfe0c9 100%) !important;}
.left-panel .overlay {background:none !important; justify-content:center; align-items:flex-start;}
.left-panel .logo {width:190px; background:#ffffff; border-radius:14px; padding:.6rem 1rem; margin-bottom:1.6rem;
  box-shadow:0 2px 10px rgba(10,61,31,.15);}
@media (max-width: 800px) {.left-panel .logo {width:110px; margin-bottom:.6rem;}}
</style>
"""


SIDEBAR_CSS = """
<style>
[data-testid="stSidebar"] {background:#dff0e4;}
[data-testid="stSidebar"] * {color:#0a3d1f;}
[data-testid="stSidebar"] hr {border-color:#a8d0b5;}
[data-testid="stSidebar"] .stButton {width:100%;}
[data-testid="stSidebar"] .stButton button {width:100%; justify-content:flex-start; border:0; border-radius:8px;
  background:transparent; padding:.55rem .8rem;}
[data-testid="stSidebar"] .stButton button:hover {background:#c6e5d0;}
[data-testid="stSidebar"] .stButton button p {text-align:left;}
[data-testid="stSidebar"] .stButton button[kind="primary"],
[data-testid="stSidebar"] .stButton button[data-testid="stBaseButton-primary"] {background:#0b6a35;}
[data-testid="stSidebarUserContent"] {padding-bottom:4rem;}
.st-key-userbox {position:fixed; bottom:1rem; left:1rem; width:auto !important; max-width:13rem; z-index:99;}
.st-key-userbox button {border:1px solid #a8d0b5; background:#eef8f1; border-radius:999px; min-height:0;
  padding:.2rem .8rem;}
.st-key-userbox button p {font-size:.85rem; margin:0;}
[data-testid="stSidebar"][aria-expanded="false"] .st-key-userbox {display:none;}
[data-testid="stSidebar"] .stButton button[kind="primary"] *,
[data-testid="stSidebar"] .stButton button[data-testid="stBaseButton-primary"] * {color:#ffffff !important;}
[data-testid="stMetricValue"] {font-size:1.45rem !important;}
[data-testid="stMetricValue"] > div {white-space:normal !important; overflow:visible !important; text-overflow:clip !important; line-height:1.2;}
[data-testid="stMetricLabel"] p {font-size:.85rem !important;}
.bars {margin:.3rem 0 1rem 0;}
.bars .brow {display:flex; align-items:center; gap:.6rem; margin:.28rem 0;}
.bars .blabel {width:30%; font-size:.85rem; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;}
.bars .btrack {flex:1; background:#e4efe8; border-radius:6px; height:16px;}
.bars .bfill {height:16px; border-radius:6px; background:#0b6a35;}
.bars .bfill.b2 {background:#e0922f;}
.bars .bval {width:22%; text-align:right; font-size:.85rem;}
.bars .legend {font-size:.8rem; color:#4b6a57; margin-bottom:.3rem;}
</style>
"""


DASH_CSS = """
<style>
.dh{font-size:30px;font-weight:800;color:#0f2a1d;letter-spacing:.2px;margin:0 0 2px 0}
.dsub{color:#6b7a70;font-size:14px;margin-bottom:10px}
.dsec{font-size:13px;font-weight:800;letter-spacing:1px;color:#0b6a35;text-transform:uppercase;margin:22px 0 2px 0}
.dcap{color:#6b7a70;font-size:13px;margin:0 0 10px 0}
.kgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(185px,1fr));gap:14px;margin:6px 0 4px 0}
.kc{background:#eef7f1;border-radius:14px;border-top:5px solid #1b8a4b;padding:14px 14px 12px 14px;box-shadow:0 1px 4px rgba(0,0,0,.06);text-align:center;min-height:150px}
.kc.amber{background:#fff6e3;border-top-color:#e0922f}.kc.red{background:#fdecea;border-top-color:#c9453b}
.kc.blue{background:#eaf2fb;border-top-color:#2f6fb0}.kc.grey{background:#f4f5f4;border-top-color:#8a948d}
.kl{font-size:12.5px;font-weight:700;letter-spacing:.6px;color:#33423a;text-transform:uppercase}
.kv{font-size:30px;font-weight:800;color:#0f2a1d;line-height:1.15;margin:6px 0 2px 0;word-break:break-word}
.kv.sm{font-size:19px;line-height:1.3;margin:10px 0 6px 0}
.kc.red .kv{color:#a52a20}
.kd{font-size:12.5px;color:#5c6b62;margin-top:4px;line-height:1.3}
.kx{font-size:12.5px;font-weight:700;margin-top:2px}.kx.up{color:#c06a00}.kx.down{color:#1b8a4b}.kx.same{color:#2f6fb0}
.spark{display:block;margin:2px auto 0 auto}
.ins{background:#fff;border:1px solid #e3e8e5;border-radius:14px;padding:14px 18px;margin:4px 0 8px 0}
.ins .ir{display:flex;gap:12px;align-items:flex-start;padding:8px 0;border-bottom:1px solid #eef1ef;font-size:15px;color:#1d2b22}
.ins .ir:last-child{border-bottom:none}.ins .d{flex:0 0 12px;height:12px;border-radius:50%;margin-top:6px}
.ct{font-size:17px;font-weight:800;color:#0f2a1d}.cs{font-size:13px;color:#6b7a70;margin:0 0 8px 0}
.legend{display:flex;flex-wrap:wrap;gap:6px 18px;justify-content:center;margin-top:4px}
.lg{display:flex;align-items:center;gap:6px;font-size:13px;color:#1d2b22}
.lg .dot{width:11px;height:11px;border-radius:50%;display:inline-block}.lg .lv{color:#6b7a70}
</style>
"""
