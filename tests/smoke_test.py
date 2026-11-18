"""Smoke test: opens every page for every role on a temporary database with sample data, without a browser.

Run it after any change:   python tests/smoke_test.py
It needs only pandas (a small stand-in replaces Streamlit). It prints OK / FAIL for every page and ends with the number of failures.
It cannot show how the pages look; it checks that nothing crashes and that the saves work."""
import sys
import traceback

from harness import LOG, app, stub, st, users

failures = 0
for uname, (uid, role) in users.items():
    st.session_state.clear()
    st.session_state.update(user=uname, uid=uid, roles=[role], role=role, rk=0)
    pages = [k for k, _, _, roles in app.PAGE_DEFS if role in roles]
    print(f"\n== {uname} ({role}): {', '.join(pages)}")
    for field in ("All fields", "KDBS Bugesera"):
        for key in pages:
            st.session_state.ceo_field = field
            st.session_state.ceo_scope = None
            if key in app.NEW_RENDER:
                st.session_state.ceo_scope = app.field_bar()
            LOG.clear()
            try:
                app.RENDER[key]()
                broken = [l for l in LOG if l[0] == "caption" and "could not be drawn" in l[1]]
                print("OK  " if not broken else "WARN", key, field, "(a chart could not be drawn)" if broken else "")
            except stub.Rerun:
                print("OK  ", key, field, "(page asked for a reload)")
            except Exception:
                failures += 1
                print("FAIL", uname, key, field)
                traceback.print_exc(limit=4)

# ---- the login screen ----
st.session_state.clear()
try:
    app.main()
    print("\nOK   login screen")
except stub.Rerun:
    print("\nOK   login screen")
except Exception:
    failures += 1
    print("\nFAIL login screen")
    traceback.print_exc(limit=4)

print("\nFAILURES:", failures)
sys.exit(1 if failures else 0)
