"""Process-wide service wiring (one instance per Data App container)."""
from __future__ import annotations

import os
from dataclasses import dataclass

import streamlit as st

from services.auth_service import User, resolve_user
from services.config import Settings, load_settings, storage_token
from services.forecast_service import ForecastService
from services.keboola_client import Gateway, InMemoryGateway, KeboolaGateway
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
def _access_table() -> "object":
    return get_context().gateway.read_app(S.USER_ACCESS)


def current_user() -> User | None:
    ctx = get_context()
    admins = frozenset(e.strip().lower() for e in os.environ.get("APP_BOOTSTRAP_ADMINS", "").split(",") if e.strip())
    return resolve_user(st.context.headers, _access_table(), ctx.settings, admins)


def require_user() -> User:
    user = st.session_state.get("user")
    if user is None:
        st.error("You are not signed in or have no access to this application.")
        st.stop()
    return user
