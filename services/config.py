"""Runtime configuration.

In the Keboola Data App the platform injects QUERY_SERVICE_URL, the workspace
(KBC_WORKSPACE_MANIFEST_PATH / WORKSPACE_ID), BRANCH_ID and the Storage token
(STORAGE_API_TOKEN_PATH / KBC_TOKEN). Locally the same names are read from .env.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_810_MAIN_BRANCH = "151246"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def storage_token() -> str:
    """Rotating token file wins over the static env var (platform recommendation)."""
    path = _env("STORAGE_API_TOKEN_PATH")
    if path and Path(path).is_file():
        return Path(path).read_text(encoding="utf-8").strip()
    return _env("KBC_TOKEN")


def workspace_id() -> str:
    manifest = _env("KBC_WORKSPACE_MANIFEST_PATH")
    if manifest and Path(manifest).is_file():
        try:
            return str(json.loads(Path(manifest).read_text(encoding="utf-8"))["workspaceId"])
        except (KeyError, ValueError):
            pass
    return _env("WORKSPACE_ID")


@dataclass(frozen=True)
class Settings:
    kbc_url: str
    query_service_url: str
    branch_id: str
    workspace_id: str
    app_bucket: str            # writeback bucket, e.g. in.c-3mfc-app
    staging_db: str            # Snowflake database holding the project's storage
    bucket_weekly: str
    bucket_bootstrap: str
    bucket_fx: str
    backend: str               # "keboola" | "memory"
    dev_user: str              # dev only: fixed identity when no SSO identity is available
    dev_role: str
    app_url: str

    @property
    def is_dev(self) -> bool:
        return bool(self.dev_user)


def load_settings() -> Settings:
    try:  # local development: read .env (never committed)
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    except ImportError:
        pass
    branch = _env("BRANCH_ID")
    return Settings(
        kbc_url=_env("KBC_URL", "https://connection.europe-west3.gcp.keboola.com"),
        query_service_url=_env("QUERY_SERVICE_URL", "https://query.europe-west3.gcp.keboola.com"),
        branch_id=branch if branch.isdigit() else _env("DEFAULT_BRANCH_ID", PROJECT_810_MAIN_BRANCH),
        workspace_id=workspace_id(),
        app_bucket=_env("APP_BUCKET", "in.c-3mfc-app"),
        staging_db=_env("STAGING_DB", "KBC_EUW3_810"),
        bucket_weekly=_env("BUCKET_WEEKLY", "out.c-3MFC-Weekly-Cash-Flow-Facts--App-Prefill-staging"),
        bucket_bootstrap=_env("BUCKET_BOOTSTRAP", "out.c-3MFC-App-Backend-Bootstrap-no-writes"),
        bucket_fx=_env("BUCKET_FX", "out.c-3MFC-VIT-Entity-Dictionary--Prior-Month-FX-staging"),
        backend=_env("APP_BACKEND", "keboola"),
        dev_user=_env("APP_DEV_USER"),
        dev_role=_env("APP_DEV_ROLE", "HQ"),
        app_url=_env("APP_PUBLIC_URL"),
    )
