"""A very small stand-in for Streamlit, used only by smoke_test.py so the pages can run without a browser."""
import sys, types, os, io
from unittest.mock import MagicMock
import pandas as pd

PRESS, OVERRIDE, EDITORS, LOG = set(), {}, {}, []
CLICK = {}

class Rerun(BaseException): pass  # like Streamlit's own rerun signal: ordinary 'except Exception' does not catch it

class SS(dict):
    def __getattr__(self, k):
        try: return self[k]
        except KeyError: raise AttributeError(k)
    def __setattr__(self, k, v): self[k] = v

class CC:
    def __getattr__(self, name):
        return lambda *a, **k: dict(kind=name, args=a, kw=k)

def noop(*a, **k): return None

class W:
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def __getattr__(self, name):
        if name.startswith("_"): raise AttributeError(name)
        f = STClass.__dict__.get(name)
        return f.__get__(STW) if f else noop

class STClass(W):
    def __init__(self):
        self.session_state = SS(); self.secrets = {}; self.sidebar = W(); self.column_config = CC()
    def set_page_config(self, **k): pass
    def cache_resource(self, *a, **k):
        if a and callable(a[0]): return a[0]
        return lambda f: f
    cache_data = cache_resource
    def columns(self, spec, **k):
        n = spec if isinstance(spec, int) else len(spec); return [W() for _ in range(n)]
    def tabs(self, labels): return [W() for _ in labels]
    def form(self, *a, **k): return W()
    def form_submit_button(self, *a, **k): return False
    def expander(self, *a, **k): return W()
    def container(self, *a, **k): return W()
    def popover(self, *a, **k): return W()
    def _o(self, label, key, default):
        for k in (key, label):
            if k in OVERRIDE: return OVERRIDE[k]
        return default
    def selectbox(self, label, options=(), index=0, key=None, **k):
        options = list(options); d = options[index] if options and index is not None else None
        return self._o(label, key, d)
    def radio(self, label, options, index=0, key=None, **k): return self._o(label, key, list(options)[index])
    def text_input(self, label, value="", key=None, **k): return self._o(label, key, value)
    def number_input(self, label, *a, value=None, key=None, **k): return self._o(label, key, value)
    def date_input(self, label, value=None, key=None, **k): return self._o(label, key, value)
    def text_area(self, label, value="", key=None, **k): return self._o(label, key, value)
    def multiselect(self, label, options=(), default=None, key=None, **k): return self._o(label, key, list(default or []))
    def file_uploader(self, label, key=None, **k): return self._o(label, key, [])
    def pills(self, label, options=(), selection_mode="single", default=None, key=None, **k):
        d = list(default or []) if selection_mode == "multi" else default
        return self._o(label, key, d)
    def checkbox(self, label, value=False, key=None, **k): return self._o(label, key, value)
    def button(self, label, key=None, **k): return (key in PRESS) or (label in PRESS)
    def download_button(self, label, data, *a, **k):
        LOG.append(("download", label, len(data)))
        return False
    def data_editor(self, data, key=None, **k):
        for pre, fr in EDITORS.items():
            if key and key.startswith(pre): return fr
        return data
    def rerun(self): raise Rerun()
    def metric(self, l, v, *a, **k): LOG.append(("metric", l, v))
    def altair_chart(self, *a, **k):
        LOG.append(("chart",)); import types as _t; return _t.SimpleNamespace(selection=CLICK.get('sel', {}))
    def markdown(self, t, *a, **k): LOG.append(("md", str(t)))
    def write(self, *a, **k): pass
    def error(self, t, *a, **k): LOG.append(("error", str(t)))
    def warning(self, t, *a, **k): LOG.append(("warning", str(t)))
    def caption(self, t, *a, **k): LOG.append(("caption", str(t)))
    def stop(self): raise Rerun()

STW = STClass()
mod = types.ModuleType("streamlit")
for n in dir(STW):
    if not n.startswith("__"):
        try: setattr(mod, n, getattr(STW, n))
        except Exception: pass
mod.__getattr__ = lambda name: (STClass.__dict__[name].__get__(STW) if name in STClass.__dict__ else noop)
mod.session_state = STW.session_state; mod.secrets = STW.secrets; mod.sidebar = STW.sidebar; mod.column_config = STW.column_config
sys.modules["streamlit"] = mod
