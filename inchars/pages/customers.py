"""Customers tab: the people who bring jobs."""

import streamlit as st

from inchars.components.common import K, done, match, search_box, to_excel, wide
from inchars.config import XLSX
from inchars.data.jobs import load_customers, save_customer


def customers_tab():
    cust = load_customers()
    q = search_box("cu_q", "Search customer", "Type part of a name or phone number…")
    view = match(cust, ["Name", "Phone", "Email"], q).reset_index(drop=True)
    st.caption(f"{len(view)} customer(s)")
    shown = view.drop(columns="id").copy()
    shown.insert(0, "No", range(1, len(shown) + 1))
    wide(st.dataframe, shown, hide_index=True)
    if len(shown):
        st.download_button(":material/download: Download Excel", to_excel({"Customers": shown}), "customers.xlsx", mime=XLSX, key="dl_cust")
    with st.expander("Add or change a customer"):
        labels = {"New customer": None}
        labels.update({f"{r.Name} · {r.Phone}": int(r.id) for r in cust.itertuples()})
        pick = st.selectbox("Customer", list(labels), key=K("cu_pick"))
        cur = cust[cust.id == labels[pick]].iloc[0] if labels[pick] else None
        a, b = st.columns(2)
        name = a.text_input("Name", value=cur.Name if cur is not None else "", key=K("cu_n" + str(pick)))
        phone = b.text_input("Phone", value=cur.Phone if cur is not None else "", key=K("cu_p" + str(pick)))
        email = a.text_input("Email (optional)", value=(cur.Email or "") if cur is not None else "", key=K("cu_e" + str(pick)))
        note = b.text_input("Note (optional)", value=(cur.Note or "") if cur is not None else "", key=K("cu_o" + str(pick)))
        if st.button("Save customer", type="primary", key=K("cu_s")):
            cid, err = save_customer(name, phone, email, note, labels[pick])
            if err:
                st.error(err)
            else:
                done("Customer saved.")
