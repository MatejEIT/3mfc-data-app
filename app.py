"""3M Cash Flow Forecast – Keboola Data App entry point."""
from __future__ import annotations

import streamlit as st

st.set_page_config(page_title="3M Cash Flow Forecast", page_icon=":material/account_balance:", layout="wide")

from components.theme import apply_theme, header  # noqa: E402
from services.bootstrap import current_user, get_context  # noqa: E402

apply_theme()

try:
    get_context()
except Exception as exc:  # configuration problems must be visible, not a blank page
    st.error(f"Application backend is not configured: {exc}")
    st.stop()

user = current_user()
if user is None:
    who = st.session_state.get("identity_email") or "unknown user"
    st.error(f"Access denied for **{who}**. Your e-mail is not assigned to any company in the group access list "
             "(VIT_AR_REPORT_COMMENTS_ACCESS). Please ask HQ finance to add you.")
    st.stop()
st.session_state["user"] = user

pages = [
    st.Page("pages/forecast_editor.py", title="Forecast Editor", icon=":material/edit_note:", default=True),
]
if user.is_hq:
    pages.append(st.Page("pages/hq_workspace.py", title="HQ Workspace", icon=":material/apartment:"))
pages.append(st.Page("pages/methodology.py", title="Methodology", icon=":material/menu_book:"))
if user.is_hq:
    pages.append(st.Page("pages/hq_report.py", title="HQ report", icon=":material/monitoring:"))

nav = st.navigation(pages, position="top")
header(user.email, user.role.value, nav.title)
nav.run()
