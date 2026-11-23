"""Process-wide service wiring (one instance per Data App container)."""
from __future__ import annotations

import os
from dataclasses import dataclass

import streamlit as st

from domain.models import Role
from services.auth_service import User, identity_from_headers, resolve_user
from services.config import Settings, load_settings, storage_token
from services.forecast_service import ForecastService
from services.keboola_client import Gateway, InMemoryGateway, KeboolaGateway, read_app
from services.reference_data import ReferenceData
from services.repository import ForecastRepository
from services import schema as S
from services.snapshot_service import SnapshotService


@dataclass
class AppContext:
    settings: Settings
    gateway: Gateway
    repo: ForecastRepository
    ref: ReferenceData
    forecasts: ForecastService
    snapshots: SnapshotService


@st.cache_resource(show_spinner=False)
def get_context() -> AppContext:
    settings = load_settings()
    if settings.backend == "memory":
        gw: Gateway = InMemoryGateway()
    else:
        missing = [n for n, v in (("Storage token", storage_token()), ("workspace", settings.workspace_id),
                                  ("QUERY_SERVICE_URL", settings.query_service_url)) if not v]
        if missing:
            raise RuntimeError("Missing runtime configuration: " + ", ".join(missing))
        gw = KeboolaGateway(settings.query_service_url, storage_token, settings.branch_id, settings.workspace_id,
                            settings.app_bucket, settings.staging_db)
    repo = ForecastRepository(gw)
    ref = ReferenceData(gw, settings)
    return AppContext(settings, gw, repo, ref, ForecastService(repo, ref), SnapshotService(repo, ref))


@st.cache_data(ttl=300, show_spinner=False)
def _app_access_table() -> "object":
    return read_app(get_context().gateway, S.USER_ACCESS)


def current_user() -> User | None:
    ctx = get_context()
    if ctx.settings.is_dev:                       # local / pre-SSO only (APP_DEV_USER)
        return User(ctx.settings.dev_user, Role(ctx.settings.dev_role), frozenset({"*"}))
    admins = frozenset(e.strip().lower() for e in os.environ.get("APP_BOOTSTRAP_ADMINS", "").split(",") if e.strip())
    email = identity_from_headers(st.context.headers)
    st.session_state["identity_email"] = email
    return resolve_user(email, _app_access_table(), ctx.ref.group_access(), admins)


def require_user() -> User:
    user = st.session_state.get("user")
    if user is None:
        st.error("You are not signed in or have no access to this application.")
        st.stop()
    return user


@st.cache_data(ttl=60, show_spinner=False)
def cached_submissions_and_status(snapshot: str):
    """Submission rows + snapshot status, shared for 60 s: HQ Workspace re-runs on every click and must not
    hit Keboola each time. Call data_changed() after any write of this session."""
    return get_context().repo.submissions_and_status(snapshot)


def data_changed() -> None:
    """Forget cached forecast data after a save / submit / FINAL so every page shows the new numbers."""
    cached_submissions_and_status.clear()
    for key in [k for k in st.session_state if str(k).startswith("report::")]:
        st.session_state.pop(key, None)


# ------------------------------------------------------------------ remembered filters across pages
# Streamlit drops the state of widgets of a page you leave; the choice is kept in a shared key instead
# ("app_snapshot", "app_bu", ...) and put back into the widget when the page is opened again.
def restore_choice(widget_key: str, shared_key: str, options) -> None:
    if widget_key not in st.session_state and st.session_state.get(shared_key) in list(options):
        st.session_state[widget_key] = st.session_state[shared_key]


def remember_choice(shared_key: str, value) -> None:
    st.session_state[shared_key] = value
